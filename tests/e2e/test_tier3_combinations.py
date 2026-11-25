"""Tier 3 cross-feature pairwise combination E2E tests and CPU latency benchmark.

IMPORTANT: Zero top-level ``import hm_recommender.v2...`` or
``import hm_recommender.api.recommend_v2`` statements are allowed here.
"""

from __future__ import annotations

import ast
import base64
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import hmac
import importlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any
from unittest.mock import patch
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
import httpx
import polars as pl
import pytest

from hm_recommender.api import auth
from hm_recommender.api.app import create_app
from hm_recommender.api.recommend import HistoryState
from hm_recommender.data.io import sha256
from hm_recommender.releases.package import verify_history_release
from hm_recommender.ui.images import PLACEHOLDER
from tests.e2e.harness import (
    MIGRATIONS_DIR,
    REPO_ROOT,
    FakeEventRegistry,
    InMemoryApiAdapter,
    build_synthetic_dataset,
    build_synthetic_image_zip,
    build_synthetic_v2_release,
    count_catalog_calls,
    find_buttons,
    find_control,
    make_recording_transport,
    measure_p95_ms,
    select_option_matching,
    start_uvicorn_app,
    ui_apptest_session,
    visible_text,
)

_TEN_DIGIT_RE = re.compile(r"^[0-9]{10}$")
_V2_BADGE_RE = re.compile(
    r"\b(?:OTRO COLOR|COMB[ÍI]NALO|COMPRADO JUNTO|ESTILO AF[ÍI]N|TU ESTILO|"
    r"ESTILO SIMILAR|M[ÁA]S COMO ESTE|PRENDA SIMILAR|SIMILAR|TENDENCIA PARA TI|"
    r"POPULAR EN TU PERFIL|TU HISTORIAL)\b",
    re.I,
)


def _import_two_stage_state() -> type:
    rec_v2 = importlib.import_module("hm_recommender.api.recommend_v2")
    cls = getattr(rec_v2, "TwoStageState", None)
    assert isinstance(cls, type), "hm_recommender.api.recommend_v2 must expose TwoStageState"
    return cls


def _prepare_and_activate_v2(
    fake_registry: FakeEventRegistry,
    v2_release_dir: Path,
    tmp_path: Path,
    *,
    admin_token: str = "local-test-token",
    enable_events: bool = False,
) -> tuple[TestClient, str, str | None]:
    """Configure events/registry BEFORE create_app, then prepare & activate v2-*."""
    two_stage_cls = _import_two_stage_state()
    stream_id = fake_registry.enable_in_memory_events() if enable_events else None

    input_root = tmp_path / f"input_v2_{uuid4().hex[:8]}"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_release_dir, input_root / "rel_v2")
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_token}"}
    prep = client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"})
    assert prep.status_code == 200, f"Failed to prepare v2-* release: {prep.text}"
    rid = prep.json()["release_id"]
    act = client.post("/admin/releases/activate", headers=headers, json={"release_id": rid})
    assert act.status_code == 200, f"Failed to activate v2-* release: {act.text}"
    assert isinstance(fake_registry.snapshot(), two_stage_cls)
    return client, rid, stream_id


def _gate_result_passed(res: Any) -> bool:
    if isinstance(res, bool):
        return res
    if isinstance(res, (list, tuple)):
        if res and isinstance(res[0], bool):
            return bool(res[0])
        return len(res) == 0
    if isinstance(res, dict):
        if "all_required_gates_passed" in res:
            return bool(res["all_required_gates_passed"])
        if "passed" in res:
            return bool(res["passed"])
        if "eligible" in res:
            return bool(res["eligible"])
        if "ok" in res:
            return bool(res["ok"])
        regs = res.get("regressing_segments_over_2pct", res.get("regressions"))
        if regs is not None:
            return len(regs) == 0
    raise AssertionError(f"Unsupported quality gate return value: {res!r}")


def _invoke_summary_gate(
    gate_fn: Any,
    *,
    users: int,
    rel_delta: float,
    mean_map_at_12: float = 0.0315,
    discovery_map_at_12: float = 0.0095,
    test_2020_09_16_map_at_12: float = 0.0320,
) -> bool:
    seg_row = {
        "segment": "age_<25",
        "users": users,
        "buyers": users,
        "rel_delta": rel_delta,
        "relative_delta": rel_delta,
        "baseline_map": 0.025,
        "candidate_map": 0.025 * (1.0 + rel_delta),
    }
    summary_payload: dict[str, Any] = {
        "mean_map_at_12": mean_map_at_12,
        "map_at_12": mean_map_at_12,
        "discovery_map_at_12": discovery_map_at_12,
        "discovery_map": discovery_map_at_12,
        "test_2020_09_16_map_at_12": test_2020_09_16_map_at_12,
        "test_map_at_12": test_2020_09_16_map_at_12,
        "test_2020_09_16": {"map_at_12": test_2020_09_16_map_at_12},
        "regressing_segments_over_2pct": [],
        "regressing_segments": [],
        "segments": [seg_row],
    }
    sig = inspect.signature(gate_fn)
    req_params = [
        p
        for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(req_params) == 2 and any(
        tok in req_params[0].name.lower() for tok in ("user", "buyer", "count")
    ):
        return _gate_result_passed(gate_fn(users, rel_delta))
    if len(req_params) == 2 and (
        "config" in req_params[1].name.lower()
        or "qualityv2config" in str(req_params[1].annotation).lower()
        or getattr(gate_fn, "__name__", "") == "evaluate_quality_gates"
    ):
        v2_eval = importlib.import_module("hm_recommender.v2.evaluate")
        cfg = v2_eval.load_quality_v2_config(REPO_ROOT / "configs" / "quality" / "quality-v2.json")

        def _make_fold(map_val: float, h_map_val: float) -> dict[str, Any]:
            return {
                "v2": {
                    "metrics": {"map_at_12": map_val, "ndcg_at_12": 0.04, "recall_at_200": 0.25},
                    "targets": {
                        "discovery": {"metrics": {"map_at_12": discovery_map_at_12}},
                        "repeat": {"metrics": {"map_at_12": 0.30}},
                    },
                    "segments": {
                        "recent28d": {
                            "users": users,
                            "metrics": {"map_at_12": 0.025 * (1.0 + rel_delta)},
                        }
                    },
                },
                "baselines": {
                    "H": {
                        "metrics": {"map_at_12": h_map_val},
                        "targets": {"discovery": {"metrics": {"map_at_12": 0.0045}}},
                        "segments": {
                            "recent28d": {
                                "users": users,
                                "metrics": {"map_at_12": 0.025},
                            }
                        },
                    },
                    "P": {
                        "targets": {"discovery": {"metrics": {"map_at_12": 0.0064}}},
                    },
                },
            }

        test_fid = cfg.descriptive_test_window.fold_id
        fold_reports = {
            "V1": _make_fold(mean_map_at_12, 0.022),
            "V2": _make_fold(mean_map_at_12, 0.022),
            "V3": _make_fold(mean_map_at_12, 0.022),
            test_fid: _make_fold(test_2020_09_16_map_at_12, 0.02628451),
        }
        return _gate_result_passed(gate_fn(fold_reports, cfg))
    if len(req_params) == 2:
        baseline_payload = {
            "H": 0.022225,
            "RG": 0.025444,
            "P_discovery": 0.006437,
            "H_2020_09_16": 0.026285,
            "segments": [{"segment": "age_<25", "users": users, "map_at_12": 0.025}],
        }
        return _gate_result_passed(gate_fn(summary_payload, baseline_payload))
    if req_params and any(tok in req_params[0].name.lower() for tok in ("segment", "rows")):
        return _gate_result_passed(gate_fn([seg_row]))
    return _gate_result_passed(gate_fn(summary_payload))


# ---------------------------------------------------------------------------
# T01: F_PKG x F_REC — CPU p95 < 40 ms & Real TwoStageState Signal Ordering
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_rec_v2_cpu_p95_under_40ms_across_modes_cold_start_and_streams(
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    source_commit_env: str,
) -> None:
    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    verify_v2 = getattr(v2_pkg, "verify_v2_release", None)
    assert callable(verify_v2), "hm_recommender.v2.package must expose verify_v2_release"
    retrieval_mod = importlib.import_module("hm_recommender.v2.retrieval")
    assert retrieval_mod.EVENT_KIND_WEIGHTS == {"purchase": 1.0, "click": 0.55, "view": 0.25}
    two_stage_cls = _import_two_stage_state()

    cutoff = date(2020, 9, 9)
    v2_dir, v2_id = build_synthetic_v2_release(
        tmp_path / "v2_perf",
        data_as_of=cutoff,
        num_articles=120,
        num_users=16,
    )
    assert verify_v2(v2_dir).release_id == v2_id

    sid = fake_registry.enable_in_memory_events()
    input_root = tmp_path / "input_perf"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_dir, input_root / "rel_v2")
    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(input_root))
    headers = {"Authorization": "Bearer local-test-token"}

    with TestClient(app) as client:
        assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"}).status_code == 200
        assert client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200
        assert isinstance(fake_registry.snapshot(), two_stage_cls)

        ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
        for aid, kind in (("0000000018", "view"), ("0000000024", "click"), ("0000000028", "purchase")):
            ev_res = client.post(
                "/events",
                json={
                    "event_id": str(uuid4()),
                    "stream_id": sid,
                    "release_id": v2_id,
                    "customer_id": "cust_0015",
                    "article_id": aid,
                    "kind": kind,
                    "origin": "synthetic",
                    "event_time": ev_time,
                },
            )
            assert ev_res.status_code == 201

        ref_meta = fake_registry.refresh_in_memory_stream(sid, cutoff)
        assert ref_meta["processed_events"] == 3

        # Verify live events (purchase/click/view) surface on real TwoStageState and discovery excludes purchase
        bal_stream = client.post(
            f"/streams/{sid}/recommend",
            json={"customer_id": "cust_0015", "k": 12, "mode": "balanced"},
        )
        assert bal_stream.status_code == 200
        bal_body = bal_stream.json()
        assert bal_body["strategy"] == "two_stage_lgbm_v2"
        assert bal_body["fallback"] is False
        real_bal_ids = [it["article_id"] for it in bal_body["items"]]
        assert "0000000028" in real_bal_ids
        assert "0000000024" in real_bal_ids
        assert "0000000018" in real_bal_ids

        disc_stream = client.post(
            f"/streams/{sid}/recommend",
            json={"customer_id": "cust_0015", "k": 12, "mode": "discovery"},
        )
        assert disc_stream.status_code == 200
        real_disc_ids = [it["article_id"] for it in disc_stream.json()["items"]]
        assert "0000000028" not in real_disc_ids
        assert "0000000024" in real_disc_ids
        assert "0000000018" in real_disc_ids

        workloads = [
            ("/recommend", {"customer_id": "cust_0000", "k": 12, "mode": "balanced"}),
            ("/recommend", {"customer_id": "cust_0000", "k": 12, "mode": "discovery"}),
            (
                "/recommend",
                {
                    "customer_id": "anon-perf",
                    "k": 12,
                    "mode": "balanced",
                    "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
                },
            ),
            (f"/streams/{sid}/recommend", {"customer_id": "cust_0015", "k": 12, "mode": "balanced"}),
            (f"/streams/{sid}/recommend", {"customer_id": "cust_0015", "k": 12, "mode": "discovery"}),
        ]
        idx = 0

        def _run_one() -> None:
            nonlocal idx
            endpoint, payload = workloads[idx % len(workloads)]
            idx += 1
            res = client.post(endpoint, json=payload)
            assert res.status_code == 200
            body = res.json()
            assert body["release_id"] == v2_id
            assert body["strategy"] == "two_stage_lgbm_v2"

        p95_ms = measure_p95_ms(_run_one, iterations=60, warmup=10)
        assert p95_ms < 40.0, f"Expected CPU p95 < 40.0 ms on real TwoStageState, got {p95_ms:.2f} ms"

        metrics_txt = client.get("/metrics").text
        match = re.search(r'hmrec_recommendations_total\{status="ok"\}\s+(\d+)', metrics_txt)
        assert match is not None and int(match.group(1)) >= 40


# ---------------------------------------------------------------------------
# T02: F_PKG x F_REC x F_CAT — Upgrade h->v2, Cached Catalog, Rollback v2->h
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_pkg_rec_cat_upgrade_v2_serve_rollback_h_and_catalog_swap(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_stage_cls = _import_two_stage_state()
    retrieval_mod = importlib.import_module("hm_recommender.v2.retrieval")
    explainable_reasons = set(retrieval_mod.EXPLAINABLE_REASONS)

    v2_dir, v2_id = synthetic_v2_release
    h_id = verify_history_release(synthetic_h_release).release_id
    sid = fake_registry.enable_in_memory_events()

    input_root = tmp_path / "input_swap"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(synthetic_h_release, input_root / "rel_h")
    shutil.copytree(v2_dir, input_root / "rel_v2")

    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(input_root))
    headers = {"Authorization": "Bearer local-test-token"}

    with TestClient(app) as client:
        assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"}).status_code == 200
        assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"}).status_code == 200

        # 1. Activate h-*
        assert client.post("/admin/releases/activate", headers=headers, json={"release_id": h_id}).status_code == 200
        assert isinstance(fake_registry.snapshot(), HistoryState)
        assert client.get("/health/ready").json()["release_id"] == h_id
        h_cat = client.get("/catalog")
        assert h_cat.status_code == 200
        assert h_cat.json()["release_id"] == h_id
        assert h_cat.json()["total"] == 40
        h_rec = client.post("/recommend", json={"customer_id": "cust_0000", "k": 6})
        assert h_rec.status_code == 200
        assert h_rec.json()["strategy"] == "recent_then_popular"

        # 2. Upgrade to v2-*
        assert client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200
        assert isinstance(fake_registry.snapshot(), two_stage_cls)
        assert client.get("/health/ready").json()["release_id"] == v2_id

        # Warm /catalog once, then verify in-memory cache (0 parquet reads)
        warm_cat = client.get("/catalog")
        assert warm_cat.status_code == 200
        assert warm_cat.json()["release_id"] == v2_id

        parquet_reads = 0
        orig_scan = pl.scan_parquet
        orig_read = pl.read_parquet

        def _count_scan(*args: Any, **kwargs: Any):
            nonlocal parquet_reads
            parquet_reads += 1
            return orig_scan(*args, **kwargs)

        def _count_read(*args: Any, **kwargs: Any):
            nonlocal parquet_reads
            parquet_reads += 1
            return orig_read(*args, **kwargs)

        with monkeypatch.context() as mp:
            mp.setattr(pl, "scan_parquet", _count_scan)
            mp.setattr(pl, "read_parquet", _count_read)
            filtered_cat = client.get(
                "/catalog",
                params={"q": "Dress", "index_group_name": "Ladieswear", "limit": 12},
            )
        assert parquet_reads == 0
        assert filtered_cat.status_code == 200
        f_body = filtered_cat.json()
        assert f_body["release_id"] == v2_id
        assert f_body["total"] >= 2
        f_items = f_body["items"]
        assert all(it["index_group_name"] == "Ladieswear" for it in f_items)
        assert all("dress" in json.dumps(it).lower() for it in f_items)
        assert all(_TEN_DIGIT_RE.fullmatch(it["article_id"]) for it in f_items)

        # Feed filtered catalog IDs into live events + discovery recommendation
        viewed_aid = f_items[0]["article_id"]
        purchased_aid = f_items[1]["article_id"]
        cutoff = date(2020, 9, 9)
        ev_time = datetime.combine(cutoff - timedelta(days=1), time(10), tzinfo=timezone.utc).isoformat()
        for aid, kind in ((viewed_aid, "view"), (purchased_aid, "purchase")):
            ev_res = client.post(
                "/events",
                json={
                    "event_id": str(uuid4()),
                    "stream_id": sid,
                    "release_id": v2_id,
                    "customer_id": "cust_0007",
                    "article_id": aid,
                    "kind": kind,
                    "origin": "synthetic",
                    "event_time": ev_time,
                },
            )
            assert ev_res.status_code == 201
        fake_registry.refresh_in_memory_stream(sid, cutoff)

        v2_stream = client.post(
            f"/streams/{sid}/recommend",
            json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
        )
        assert v2_stream.status_code == 200
        s_body = v2_stream.json()
        assert s_body["strategy"] == "two_stage_lgbm_v2"
        assert s_body["fallback"] is False
        s_ids = [it["article_id"] for it in s_body["items"]]
        assert purchased_aid not in s_ids
        assert viewed_aid in s_ids
        assert all(it["reason"] in explainable_reasons for it in s_body["items"])

        cat_df = pl.read_parquet(v2_dir / "catalog.parquet")
        pcode_map = dict(zip(cat_df["article_id"].to_list(), cat_df["product_code"].to_list()))
        pcodes = [pcode_map[aid] for aid in s_ids]
        assert len(pcodes) == len(set(pcodes))

        # 3. Rollback v2-* -> h-*
        rb = client.post("/admin/releases/rollback", headers=headers)
        assert rb.status_code == 200
        assert rb.json()["release_id"] == h_id
        assert isinstance(fake_registry.snapshot(), HistoryState)
        assert client.get("/health/ready").json()["release_id"] == h_id

        post_rb_cat = client.get("/catalog")
        assert post_rb_cat.status_code == 200
        assert post_rb_cat.json()["release_id"] == h_id

        post_rb_rec = client.post("/recommend", json={"customer_id": "cust_0000", "k": 6})
        assert post_rb_rec.status_code == 200
        assert post_rb_rec.json()["release_id"] == h_id
        assert post_rb_rec.json()["strategy"] == "recent_then_popular"

        # Stale v2-* event submission rejected with 409 after rollback
        stale_ev = client.post(
            "/events",
            json={
                "event_id": str(uuid4()),
                "stream_id": sid,
                "release_id": v2_id,
                "customer_id": "cust_0007",
                "article_id": viewed_aid,
                "kind": "view",
                "origin": "synthetic",
                "event_time": ev_time,
            },
        )
        assert stale_ev.status_code == 409


# ---------------------------------------------------------------------------
# T03: F_REC x F_CAT x F_IMG — Filtered Catalog -> Events -> Filtered Recs -> Images
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_rec_cat_img_filtered_catalog_events_images_and_10digit_id_integrity(
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _import_two_stage_state()
    v2_dir, v2_id = synthetic_v2_release

    partial_zip = build_synthetic_image_zip(
        tmp_path / "partial_imgs.zip",
        [f"{i:010d}" for i in range(39)],
        corrupt_ids=["0000000001"],
    )
    img_cache_dir = tmp_path / "img_cache_t3"
    monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(partial_zip))
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(img_cache_dir))

    sid = fake_registry.enable_in_memory_events()
    input_root = tmp_path / "input_t3"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_dir, input_root / "rel_v2")
    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(input_root))
    headers = {"Authorization": "Bearer local-test-token"}

    with TestClient(app) as client:
        assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"}).status_code == 200
        assert client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200

        exact_q = client.get("/catalog", params={"q": "0000000000"})
        assert exact_q.status_code == 200
        assert exact_q.json()["total"] == 1
        assert exact_q.json()["items"][0]["article_id"] == "0000000000"

        mens_cat = client.get(
            "/catalog",
            params={"q": "Atelier", "index_group_name": "Menswear", "limit": 20},
        )
        assert mens_cat.status_code == 200
        mens_items = mens_cat.json()["items"]
        assert mens_items
        assert all(it["index_group_name"] == "Menswear" for it in mens_items)
        assert all(_TEN_DIGIT_RE.fullmatch(it["article_id"]) for it in mens_items)

        # Spy ZipFile.infolist vs ZipFile.getinfo on HTTP /articles/{id}/image miss
        infolist_calls = 0
        getinfo_calls = 0
        orig_infolist = zipfile.ZipFile.infolist
        orig_getinfo = zipfile.ZipFile.getinfo

        def _spy_infolist(self: zipfile.ZipFile):
            nonlocal infolist_calls
            infolist_calls += 1
            return orig_infolist(self)

        def _spy_getinfo(self: zipfile.ZipFile, name: str):
            nonlocal getinfo_calls
            getinfo_calls += 1
            return orig_getinfo(self, name)

        with monkeypatch.context() as mp:
            mp.setattr(zipfile.ZipFile, "infolist", _spy_infolist)
            mp.setattr(zipfile.ZipFile, "getinfo", _spy_getinfo)
            img0 = client.get("/articles/0000000000/image")
        assert img0.status_code == 200
        assert img0.headers["content-type"] == "image/jpeg"
        assert infolist_calls == 0, f"Expected O(1) ZipFile.getinfo(), saw {infolist_calls} infolist() calls"
        assert getinfo_calls >= 1

        for fallback_id in ("0000000001", "0000000039"):
            fb = client.get(f"/articles/{fallback_id}/image")
            assert fb.status_code == 200
            assert fb.headers["content-type"] == "image/svg+xml"
            assert fb.content == PLACEHOLDER

        cutoff = date(2020, 9, 9)
        ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
        for aid, kind in ((mens_items[0]["article_id"], "view"), (mens_items[1]["article_id"], "click")):
            res = client.post(
                "/events",
                json={
                    "event_id": str(uuid4()),
                    "stream_id": sid,
                    "release_id": v2_id,
                    "customer_id": "cust_0007",
                    "article_id": aid,
                    "kind": kind,
                    "origin": "synthetic",
                    "event_time": ev_time,
                },
            )
            assert res.status_code == 201
        fake_registry.refresh_in_memory_stream(sid, cutoff)

        rec = client.post(
            f"/streams/{sid}/recommend",
            json={
                "customer_id": "cust_0007",
                "k": 10,
                "mode": "discovery",
                "filters": {"colour_group_name": "Black"},
            },
        )
        assert rec.status_code == 200
        rec_ids = [it["article_id"] for it in rec.json()["items"]]
        assert rec_ids
        assert all(_TEN_DIGIT_RE.fullmatch(aid) for aid in rec_ids)

        details = client.post("/articles/details", json={"article_ids": rec_ids})
        assert details.status_code == 200
        det_items = details.json()["items"]
        assert len(det_items) == len(rec_ids)
        assert all(it["colour_group_name"] == "Black" for it in det_items)

        for aid in rec_ids:
            img_res = client.get(f"/articles/{aid}/image")
            assert img_res.status_code == 200
            assert img_res.headers["content-type"] in {"image/jpeg", "image/svg+xml"}


# ---------------------------------------------------------------------------
# T04: F_PKG x F_IMG x F_CAT — v2 Catalog Alignment & Image Archive Swap
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_pkg_img_v2_catalog_alignment_and_archive_swap_isolation(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    verify_v2 = getattr(v2_pkg, "verify_v2_release", None)
    assert callable(verify_v2)
    two_stage_cls = _import_two_stage_state()

    v2_dir, v2_id = synthetic_v2_release
    assert verify_v2(v2_dir).release_id == v2_id
    cat_df = pl.read_parquet(v2_dir / "catalog.parquet")
    assert all(_TEN_DIGIT_RE.fullmatch(aid) for aid in cat_df["article_id"].to_list())

    live_zip = tmp_path / "live_images.zip"
    shutil.copy2(synthetic_image_zip, live_zip)
    monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(live_zip))
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(tmp_path / "swap_cache"))

    input_root = tmp_path / "input_t4"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_dir, input_root / "rel_v2")
    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(input_root))
    headers = {"Authorization": "Bearer local-test-token"}

    with TestClient(app) as client:
        assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"}).status_code == 200
        assert client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200
        assert isinstance(fake_registry.snapshot(), two_stage_cls)

        before = client.get("/articles/0000000000/image")
        assert before.status_code == 200
        assert before.headers["content-type"] == "image/jpeg"

        # Swap archive at same path omitting 0000000000 and advance mtime_ns
        build_synthetic_image_zip(live_zip, [f"{i:010d}" for i in range(1, 40)])
        st = live_zip.stat()
        os.utime(live_zip, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

        after_0 = client.get("/articles/0000000000/image")
        assert after_0.status_code == 200
        assert after_0.headers["content-type"] == "image/svg+xml"
        assert after_0.content == PLACEHOLDER

        after_2 = client.get("/articles/0000000002/image")
        assert after_2.status_code == 200
        assert after_2.headers["content-type"] == "image/jpeg"

        assert client.get("/articles/9999999999/image").status_code == 404
        assert client.get("/catalog").status_code == 200
        assert client.post("/recommend", json={"customer_id": "cust_0000", "k": 6}).status_code == 200


# ---------------------------------------------------------------------------
# T05: F_UI x F_REC x F_CAT — Onboarding, Search, Live Actions, Mode & Backend Parity
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_ui_rec_cat_onboarding_catalog_search_live_events_and_mode_toggle(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, v2_id = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        assert count_catalog_calls(calls) == 1
        initial_text = visible_text(app)
        assert re.search(r"\b[0-9]{10}\b", initial_text)
        assert not re.search(r"ART\.\s*[0-9]{8}\b", initial_text)

        # 1. Cold-start onboarding
        profile_sb = find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None
        assert select_option_matching(profile_sb, r"nuevo visitante") is not None
        app.run()
        assert not app.exception

        section_ctrl = find_control(
            app,
            ("selectbox", "radio", "pills", "segmented_control"),
            label_re=r"sección|categor|departamento|inter[eé]s|preferencia",
            key_re=r"(?:onboarding|onboard|cold_start).*(?:section|group)|pref_group|onboarding_group",
            option_re=r"menswear|hombre",
            exclude_key_re=r"catalog|filter|^index_group_name$",
        )
        assert section_ctrl is not None
        assert select_option_matching(section_ctrl, r"menswear|hombre") is not None
        app.run()

        age_ctrl = find_control(
            app,
            ("selectbox", "radio", "pills", "segmented_control"),
            label_re=r"edad|rango",
            key_re=r"age_bin|pref_age|onboarding_age",
            option_re=r"<\s*25|menor(?:es)?\s+de\s+25|menos\s+de\s+25|hasta\s+24",
            exclude_key_re=r"catalog|filter",
        )
        assert age_ctrl is not None
        assert select_option_matching(age_ctrl, r"<\s*25|menor(?:es)?\s+de\s+25|menos\s+de\s+25|hasta\s+24") is not None
        app.run()

        refresh_btns = find_buttons(app, key_re=r"^refresh$", label_re=r"actualizar recomendaciones")
        if refresh_btns:
            refresh_btns[0].click().run()
        assert not app.exception

        pref_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("preferences")
        ]
        assert pref_calls
        assert pref_calls[-1]["json"]["preferences"] == {"age_bin": "<25", "index_group_name": "Menswear"}
        pref_items = (pref_calls[-1]["response_json"] or {}).get("items", [])
        assert pref_items and adapter.catalog_by_id[pref_items[0]["article_id"]]["index_group_name"] == "Menswear"

        # 2. Catalog search & section filter
        search_input = find_control(
            app,
            ("text_input",),
            label_re=r"buscar|búsqueda|palabra|catálogo|search",
            key_re=r"search|query|catalog_q|^q$",
        )
        assert search_input is not None
        calls.clear()
        search_input.input("Dress").run()
        assert not app.exception
        assert any(c["method"] == "GET" and c["path"] == "/catalog" and c["params"].get("q") == "Dress" for c in calls)

        section_filter = find_control(
            app,
            ("selectbox", "radio", "pills", "segmented_control"),
            key_re=r"catalog_.*(?:section|group|index_group)|filter_.*(?:section|group)|^index_group_name$|^catalog_section$",
            label_re=r"sección|categor[ií]a|grupo|departamento",
            option_re=r"ladieswear|menswear|mujer|hombre",
            exclude_key_re=r"onboarding|onboard|cold_start|pref_group|profile",
        )
        assert section_filter is not None
        calls.clear()
        assert select_option_matching(section_filter, r"ladieswear|mujer") is not None
        app.run()
        assert not app.exception
        assert any(
            c["method"] == "GET" and c["path"] == "/catalog" and c["params"].get("index_group_name") == "Ladieswear"
            for c in calls
        )

        # Reset search & filter for full card actions
        search_input = find_control(
            app,
            ("text_input",),
            label_re=r"buscar|búsqueda|palabra|catálogo|search",
            key_re=r"search|query|catalog_q|^q$",
        )
        if search_input is not None:
            search_input.input("").run()
        section_filter = find_control(
            app,
            ("selectbox", "radio", "pills", "segmented_control"),
            key_re=r"catalog_.*(?:section|group|index_group)|filter_.*(?:section|group)|^index_group_name$|^catalog_section$",
            label_re=r"sección|categor[ií]a|grupo|departamento",
            option_re=r"ladieswear|menswear|mujer|hombre",
            exclude_key_re=r"onboarding|onboard|cold_start|pref_group|profile",
        )
        if section_filter is not None:
            select_option_matching(section_filter, r"todas|todos|all")
            app.run()

        # 3. View, Click (Favorito), Purchase actions
        detail_btns = find_buttons(app, label_re=r"ver\s+detalle|detalle", key_re=r"detail|view_")
        assert detail_btns
        detail_btns[0].click().run()
        assert not app.exception

        fav_btns = find_buttons(app, label_re=r"favorit|♡|me gusta", key_re=r"fav|click_|like_")
        assert fav_btns
        fav_btns[0].click().run()
        assert not app.exception

        buy_btns = find_buttons(app, label_re=r"comprar|simular compra", key_re=r"buy|purchase")
        assert buy_btns
        calls.clear()
        buy_btns[0].click().run()
        assert not app.exception

        assert {"view", "click", "purchase"}.issubset({e["kind"] for e in adapter.store.events_log})
        purchase_events = [e for e in adapter.store.events_log if e["kind"] == "purchase"]
        purchased_aid = purchase_events[-1]["article_id"]
        assert re.search(r"actividad en (?:esta|la) sesión|vistos|favoritos|comprados", visible_text(app), re.I)

        # 4. Mode toggle -> Solo descubrimiento
        mode_ctrl = find_control(
            app,
            ("radio", "segmented_control", "pills", "selectbox", "toggle"),
            label_re=r"modo|descubrimiento|equilibrado",
            key_re=r"^mode$|rec_mode|discovery",
            option_re=r"solo\s+descubrimiento|descubrimiento",
        )
        assert mode_ctrl is not None
        calls.clear()
        if hasattr(mode_ctrl, "options") and getattr(mode_ctrl, "options", None):
            assert select_option_matching(mode_ctrl, r"solo\s+descubrimiento|descubrimiento") is not None
        elif hasattr(mode_ctrl, "set_value"):
            mode_ctrl.set_value(True)
        app.run()
        assert not app.exception

        disc_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("mode") == "discovery"
        ]
        assert disc_calls
        disc_items = (disc_calls[-1]["response_json"] or {}).get("items", [])
        assert disc_items
        disc_ids = [it["article_id"] for it in disc_items]
        assert purchased_aid not in disc_ids
        pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in disc_ids]
        assert len(pcodes) == len(set(pcodes))

    # 5. Replay exact payloads against real create_app + TwoStageState (no mock masking)
    http_client, _, real_sid = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path, enable_events=True)
    assert real_sid is not None
    with http_client:
        real_pref = http_client.post(
            "/recommend",
            json={
                "customer_id": "anon-t3-ui",
                "k": 12,
                "mode": "balanced",
                "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
            },
        )
        assert real_pref.status_code == 200
        assert real_pref.json()["strategy"] == "two_stage_lgbm_v2"
        assert adapter.catalog_by_id[real_pref.json()["items"][0]["article_id"]]["index_group_name"] == "Menswear"

        real_cat = http_client.get("/catalog", params={"q": "Dress", "index_group_name": "Ladieswear", "limit": 12})
        assert real_cat.status_code == 200
        assert all(it["index_group_name"] == "Ladieswear" for it in real_cat.json()["items"])

        cutoff = date(2020, 9, 9)
        ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
        for aid, kind in (("0000000018", "view"), ("0000000024", "click"), ("0000000028", "purchase")):
            assert (
                http_client.post(
                    "/events",
                    json={
                        "event_id": str(uuid4()),
                        "stream_id": real_sid,
                        "release_id": v2_id,
                        "customer_id": "cust_0007",
                        "article_id": aid,
                        "kind": kind,
                        "origin": "synthetic",
                        "event_time": ev_time,
                    },
                ).status_code
                == 201
            )
        fake_registry.refresh_in_memory_stream(real_sid, cutoff)

        real_disc = http_client.post(
            f"/streams/{real_sid}/recommend",
            json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
        )
        assert real_disc.status_code == 200
        real_disc_body = real_disc.json()
        assert real_disc_body["strategy"] == "two_stage_lgbm_v2"
        assert real_disc_body["fallback"] is False
        real_disc_ids = [it["article_id"] for it in real_disc_body["items"]]
        assert "0000000028" not in real_disc_ids
        real_pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in real_disc_ids]
        assert len(real_pcodes) == len(set(real_pcodes))


# ---------------------------------------------------------------------------
# T06: F_UI x F_IMG x F_PKG — v2 Storefront Images, Badges & Placeholder Resilience
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_ui_img_pkg_v2_storefront_images_badges_and_placeholder_resilience(
    synthetic_v2_release: tuple[Path, str],
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    verify_v2 = getattr(v2_pkg, "verify_v2_release", None)
    assert callable(verify_v2)

    v2_dir, v2_id = synthetic_v2_release
    assert verify_v2(v2_dir).release_id == v2_id

    partial_zip = build_synthetic_image_zip(
        tmp_path / "ui_partial.zip",
        [f"{i:010d}" for i in range(39) if i != 1],
        corrupt_ids=["0000000000"],
    )
    monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(partial_zip))
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(tmp_path / "real_t6_cache"))

    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)
    with http_client:
        for fb_aid in ("0000000000", "0000000001"):
            fb_res = http_client.get(f"/articles/{fb_aid}/image")
            assert fb_res.status_code == 200
            assert fb_res.headers["content-type"] == "image/svg+xml"
            assert fb_res.content == PLACEHOLDER
        ok_res = http_client.get("/articles/0000000002/image")
        assert ok_res.status_code == 200
        assert ok_res.headers["content-type"] == "image/jpeg"

    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=partial_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        profile_sb = find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None
        assert select_option_matching(profile_sb, r"comprador frecuente") is not None
        app.run()
        assert not app.exception
        assert len(app.get("image")) > 0

        rendered = visible_text(app)
        assert v2_id not in rendered
        assert _V2_BADGE_RE.search(rendered), "Expected explainable v2 reason badge in UI"

        next_btns = find_buttons(app, key_re=r"btn_next_page|next_page", label_re=r"siguiente")
        assert next_btns
        calls.clear()
        next_btns[0].click().run()
        assert not app.exception
        cat_calls = [c for c in calls if c["method"] == "GET" and c["path"] == "/catalog"]
        assert len(cat_calls) <= 1
        if cat_calls:
            assert cat_calls[0]["params"].get("offset") == "12"


# ---------------------------------------------------------------------------
# T07: F_SEC x F_PKG x F_REC — Gateway Auth, Admin Isolation & Live Uvicorn
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_sec_pkg_rec_gateway_auth_admin_bearer_and_uvicorn_isolation(
    gateway_config_factory,
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    two_stage_cls = _import_two_stage_state()
    v2_dir, v2_id = synthetic_v2_release

    legacy_pwd = "LegacyGatewayPass2026!"
    scrypt_pwd = "ScryptStrongPass2026!"
    cfg_path = gateway_config_factory({"alice": scrypt_pwd}, hash_mode="scrypt")
    cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg_data["password_hashes"]["legacy"] = hashlib.sha256(legacy_pwd.encode("utf-8")).hexdigest()
    cfg_path.write_text(json.dumps(cfg_data), encoding="utf-8")

    sid = fake_registry.enable_in_memory_events()
    input_root = tmp_path / "input_sec"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(synthetic_h_release, input_root / "rel_h")
    shutil.copytree(v2_dir, input_root / "rel_v2")

    admin_token = "adm-t3-sec"
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))

    with start_uvicorn_app(app, lifespan="on") as base_url:
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            # 1. Unauthenticated /auth/verify responses
            assert client.get("/auth/verify", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 307
            assert client.get("/auth/verify", headers={"Accept": "application/json"}, follow_redirects=False).status_code == 401
            assert (
                client.get(
                    "/auth/verify",
                    headers={"Accept": "text/html", "X-Forwarded-Uri": "/_stcore/stream"},
                    follow_redirects=False,
                ).status_code
                == 401
            )

            # 2. Login (remember=False -> 12h cookie, security headers, HMAC domain separation)
            login_res = client.post(
                "/login",
                data={"username": "alice", "password": scrypt_pwd},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert login_res.status_code == 303
            for hdr, expected in auth.SECURITY_HEADERS.items():
                assert login_res.headers.get(hdr.lower()) == expected
            set_cookie = login_res.headers["set-cookie"]
            assert "HttpOnly" in set_cookie
            assert "SameSite=lax" in set_cookie
            assert "Secure" in set_cookie
            assert f"Max-Age={auth.SHORT_SESSION_DURATION}" in set_cookie

            cookie_val = login_res.cookies[auth.COOKIE_NAME]
            derived = hmac.new(admin_token.encode("utf-8"), b"hmrec:session-secret:v1", hashlib.sha256).digest()
            assert auth.verify_session_token(cookie_val, admin_token.encode("utf-8")) is None
            assert auth.verify_session_token(cookie_val, derived) == "alice"

            assert client.get("/auth/verify", cookies={auth.COOKIE_NAME: cookie_val}).status_code == 200
            basic_legacy = "Basic " + base64.b64encode(f"legacy:{legacy_pwd}".encode("utf-8")).decode("ascii")
            assert client.get("/auth/verify", headers={"Authorization": basic_legacy}).status_code == 200

            # 3. Shopper credentials rejected on admin routes
            for path, body in (
                ("/admin/releases/prepare", {"directory": "rel_v2"}),
                ("/admin/releases/activate", {"release_id": v2_id}),
                ("/admin/releases/rollback", {}),
                (f"/admin/streams/{sid}/refresh", {"data_as_of": "2020-09-09"}),
            ):
                assert client.post(path, json=body, cookies={auth.COOKIE_NAME: cookie_val}).status_code == 403
                assert client.post(path, json=body, headers={"Authorization": basic_legacy}).status_code == 403

            # 4. Admin bearer prepares & activates v2-*
            adm_hdr = {"Authorization": f"Bearer {admin_token}"}
            assert client.post("/admin/releases/prepare", headers=adm_hdr, json={"directory": "rel_v2"}).status_code == 200
            assert client.post("/admin/releases/activate", headers=adm_hdr, json={"release_id": v2_id}).status_code == 200
            assert isinstance(fake_registry.snapshot(), two_stage_cls)

            # 5. Brute-force rate-limit (IPv6 XFF) isolated from /recommend and /health/ready
            xff = {"X-Forwarded-For": "2001:db8::cafe"}
            for _ in range(auth.MAX_LOGIN_ATTEMPTS):
                bad = client.post(
                    "/login",
                    data={"username": "alice", "password": "wrong-password"},
                    headers={"Content-Type": "application/x-www-form-urlencoded", **xff},
                    follow_redirects=False,
                )
                assert bad.status_code == 200
            blocked = client.post(
                "/login",
                data={"username": "alice", "password": "wrong-password"},
                headers={"Content-Type": "application/x-www-form-urlencoded", **xff},
                follow_redirects=False,
            )
            assert blocked.status_code == 429

            assert client.get("/health/ready").status_code == 200
            for payload in (
                {"customer_id": "cust_0000", "k": 6, "mode": "balanced"},
                {"customer_id": "cust_0000", "k": 6, "mode": "discovery"},
                {
                    "customer_id": "anon-sec",
                    "k": 6,
                    "mode": "balanced",
                    "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
                },
            ):
                rec = client.post("/recommend", json=payload)
                assert rec.status_code == 200
                assert rec.json()["strategy"] == "two_stage_lgbm_v2"


# ---------------------------------------------------------------------------
# T08: F_SEC x F_UI x F_CAT x F_IMG — Caddyfile, /auth/verify, Logout, Catalog & Images
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_sec_ui_cat_img_authenticated_gateway_caddyfile_and_storefront_routes(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _import_two_stage_state()
    scripts_dir = str(REPO_ROOT / "scripts")
    monkeypatch.syspath_prepend(scripts_dir)
    server_gateway = importlib.import_module("server_gateway")

    gw_root = tmp_path / "gw_root"
    gw_root.mkdir(parents=True, exist_ok=True)
    with patch.object(server_gateway, "run", return_value="$2a$14$" + ("A" * 53)):
        server_gateway.configure(gw_root, "hm.example.com", "alice", password="AliceStrongPass2026!")

    gw_json = gw_root / ".runtime" / "server" / "gateway.json"
    caddyfile_path = gw_root / ".runtime" / "server" / "Caddyfile"
    assert stat.S_IMODE(gw_json.stat().st_mode) == 0o600
    assert json.loads(gw_json.read_text(encoding="utf-8"))["password_hashes"]["alice"].startswith("scrypt$")

    caddy_txt = caddyfile_path.read_text(encoding="utf-8")
    assert "path /login /logout" in caddy_txt
    assert "reverse_proxy api:8000" in caddy_txt
    assert "forward_auth api:8000" in caddy_txt
    assert "uri /auth/verify" in caddy_txt
    assert "reverse_proxy ui:8501" in caddy_txt
    assert "header_up -Authorization" in caddy_txt

    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(gw_json))
    with auth._HASH_CACHE_LOCK:
        auth._HASH_CACHE.clear()
    assert list(auth.load_user_hashes().keys()) == ["alice"]

    with patch.object(server_gateway, "run", return_value="$2a$14$" + ("B" * 53)):
        server_gateway.configure(gw_root, "hm.example.com", "bob", password="BobStrongPassword2026!")
    st = gw_json.stat()
    os.utime(gw_json, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert set(auth.load_user_hashes().keys()) == {"alice", "bob"}

    v2_dir, v2_id = synthetic_v2_release
    monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(synthetic_image_zip))
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(tmp_path / "t8_img_cache"))

    input_root = tmp_path / "input_t8"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_dir, input_root / "rel_v2")
    admin_token = "adm-t3-t8"
    app_srv = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))

    with start_uvicorn_app(app_srv, lifespan="on") as base_url:
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            adm_hdr = {"Authorization": f"Bearer {admin_token}"}
            assert client.post("/admin/releases/prepare", headers=adm_hdr, json={"directory": "rel_v2"}).status_code == 200
            assert client.post("/admin/releases/activate", headers=adm_hdr, json={"release_id": v2_id}).status_code == 200

            assert (
                client.get(
                    "/auth/verify",
                    headers={"Accept": "text/html", "X-Forwarded-Uri": "/_stcore/health"},
                    follow_redirects=False,
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/auth/verify",
                    headers={"Accept": "text/html", "X-Forwarded-Uri": "/_stcore/stream"},
                    follow_redirects=False,
                ).status_code
                == 401
            )
            html_unauth = client.get("/auth/verify", headers={"Accept": "text/html"}, follow_redirects=False)
            assert html_unauth.status_code == 307
            assert html_unauth.headers["location"] == "/login"

            login = client.post(
                "/login",
                data={"username": "bob", "password": "BobStrongPassword2026!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert login.status_code == 303
            assert f"Max-Age={auth.SHORT_SESSION_DURATION}" in login.headers["set-cookie"]
            cookie_val = login.cookies[auth.COOKIE_NAME]
            assert client.get("/auth/verify", cookies={auth.COOKIE_NAME: cookie_val}).status_code == 200

            cat = client.get("/catalog", params={"q": "Dress", "index_group_name": "Ladieswear"})
            assert cat.status_code == 200
            assert cat.json()["items"]
            assert all(_TEN_DIGIT_RE.fullmatch(it["article_id"]) for it in cat.json()["items"])

            img = client.get("/articles/0000000000/image")
            assert img.status_code == 200
            assert img.headers["content-type"] == "image/jpeg"

            logout = client.get("/logout", cookies={auth.COOKIE_NAME: cookie_val}, follow_redirects=False)
            assert logout.status_code == 303
            assert logout.headers["location"] == "/login"
            assert "Max-Age=0" in logout.headers["set-cookie"]

            after_logout = client.get(
                "/auth/verify",
                headers={"Accept": "text/html"},
                follow_redirects=False,
            )
            assert after_logout.status_code == 307

    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    with ui_apptest_session(adapter, default_timeout=25) as ui_app:
        assert not ui_app.exception
        assert len(ui_app.get("image")) > 0
        rendered = visible_text(ui_app)
        assert 'href="/logout"' in rendered or "Cerrar sesión" in rendered


# ---------------------------------------------------------------------------
# T09: F_QUAL x F_PKG x F_REC — quality-v2 Gates & Live TwoStageState Eval Chain
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_qual_pkg_rec_quality_v2_gates_and_v2_package_serving_chain(
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    source_commit_env: str,
) -> None:
    v2_eval = importlib.import_module("hm_recommender.v2.evaluate")
    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    verify_v2 = getattr(v2_pkg, "verify_v2_release", None)
    assert callable(verify_v2)

    q2_path = REPO_ROOT / "configs" / "quality" / "quality-v2.json"
    assert q2_path.is_file(), "Expected configs/quality/quality-v2.json"
    q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    assert q2.get("protocol") == "quality-v2"
    raw_folds = q2.get("folds", {})
    if isinstance(raw_folds, list):
        if raw_folds and all(isinstance(item, dict) for item in raw_folds):
            folds = {
                str(item.get("fold_id") or item.get("id") or item.get("name")): (
                    item.get("cutoff") or item.get("date")
                )
                for item in raw_folds
            }
        elif len(raw_folds) == 3 and all(isinstance(item, str) for item in raw_folds):
            folds = {"V1": raw_folds[0], "V2": raw_folds[1], "V3": raw_folds[2]}
        else:
            folds = {}
    else:
        folds = {
            str(k): (v.get("cutoff") if isinstance(v, dict) else v)
            for k, v in dict(raw_folds).items()
        }
    assert folds == {"V1": "2020-08-26", "V2": "2020-09-02", "V3": "2020-09-09"}
    test_cutoff = q2.get("test_cutoff") or (q2.get("descriptive_test_window") or {}).get("cutoff")
    assert test_cutoff == "2020-09-16"
    selection = q2.get("selection") or q2.get("gates") or q2
    assert selection.get("min_segment_users", q2.get("min_segment_users")) == 1000
    max_seg_loss = selection.get(
        "max_segment_loss",
        selection.get("max_segment_relative_loss", q2.get("max_segment_loss", q2.get("max_segment_relative_loss"))),
    )
    assert max_seg_loss is not None and float(max_seg_loss) <= 0.02

    gate_fn = None
    for attr in (
        "passes_quality_gates",
        "check_quality_gates",
        "evaluate_quality_gates",
        "accepts_v2_summary",
        "check_benchmark_gates",
        "is_eligible_candidate",
    ):
        cand = getattr(v2_eval, attr, None)
        if callable(cand):
            gate_fn = cand
            break
    assert callable(gate_fn), "Expected public gate function in hm_recommender.v2.evaluate"
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015) is True
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015, mean_map_at_12=0.02544361) is False
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015, discovery_map_at_12=0.006437) is False
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015, test_2020_09_16_map_at_12=0.02628451) is False
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.0201) is False

    v2_dir, v2_id = synthetic_v2_release
    assert verify_v2(v2_dir).release_id == v2_id

    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)
    with http_client:
        rec = http_client.post("/recommend", json={"customer_id": "cust_0000", "k": 12, "mode": "balanced"})
        assert rec.status_code == 200
        body = rec.json()
        assert body["strategy"] == "two_stage_lgbm_v2"
        ranked_aids = [it["article_id"] for it in body["items"]]
        assert ranked_aids

    compute_metrics = getattr(v2_eval, "compute_buyer_metrics", None)
    assert callable(compute_metrics), "Expected compute_buyer_metrics in hm_recommender.v2.evaluate"
    res = compute_metrics(ranked_aids, {ranked_aids[0]}, ks=(200, 300))
    assert isinstance(res, dict)
    assert float(res["map_at_12"]) == 1.0
    assert float(res["ndcg_at_12"]) == 1.0
    assert float(res["recall_at_200"]) == 1.0
    assert float(res["recall_at_300"]) == 1.0


# ---------------------------------------------------------------------------
# T10: F_QUAL x F_SEC — Docs Links, Host Unittest, No Hardcoded Paths & Frozen v1
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_qual_sec_docs_links_host_tests_and_no_hardcoded_secrets() -> None:
    links_proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_docs_links.py"), "--root", str(REPO_ROOT), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert links_proc.returncode == 0, links_proc.stderr
    report = json.loads(links_proc.stdout)
    assert report["broken_count"] == 0
    assert report["files_checked"] >= 42

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": "/tmp"}
    host_proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/host", "-v"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = host_proc.stdout + "\n" + host_proc.stderr
    assert host_proc.returncode == 0, combined
    ran_match = re.search(r"Ran (\d+) tests?", combined)
    assert ran_match is not None and int(ran_match.group(1)) >= 23
    assert re.search(r"\bOK\b", combined)

    for sec_path in (
        REPO_ROOT / "src" / "hm_recommender" / "api" / "auth.py",
        REPO_ROOT / "src" / "hm_recommender" / "api" / "app.py",
        REPO_ROOT / "scripts" / "server_gateway.py",
    ):
        assert "/data/hm_project" not in sec_path.read_text(encoding="utf-8")

    assert sha256(REPO_ROOT / "configs" / "quality" / "quality-v1.json") == (
        "d4383b6bfd899e0be8abfee7e28ee42533a9121d0565fdd6795d8fdf86e3e500"
    )
    expected_migrations = {
        "001_bootstrap.sql": "04e94dace91a944eb46b8dc14de0482596a0f6f7a25261f0420f1db1b07b9b83",
        "002_releases.sql": "a1bec8206c237a41b81cdabc54f46a4b2e7f784a8be766174b62558b20a7925d",
        "003_events.sql": "3f95e9807c27dc9dd8403e9215c763305fdbd36d53ba6ca31ec7c836f331e71a",
        "004_pipeline.sql": "ec4698f41fe3055be8d5a4085029effc370f925102499c2ab40e932b4275dda3",
    }
    for fname, digest_hex in expected_migrations.items():
        assert sha256(MIGRATIONS_DIR / fname) == digest_hex

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "tests/host" in readme and "check_docs_links.py" in readme

    doc_v2 = REPO_ROOT / "docs" / "recommender-v2.md"
    assert doc_v2.is_file(), "Expected docs/recommender-v2.md"
    doc_txt = doc_v2.read_text(encoding="utf-8").lower()
    assert "quality-v2" in doc_txt and "40 ms" in doc_txt


# ---------------------------------------------------------------------------
# T11: F_QUAL x F_UI x F_CAT x F_IMG — Localization, 10-Digit IDs, O(1) ImageCache & Docs
# ---------------------------------------------------------------------------


@pytest.mark.tier3
def test_t3_qual_ui_cat_img_storefront_localization_10digit_ids_and_doc_contract(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_v2 = REPO_ROOT / "docs" / "recommender-v2.md"
    assert doc_v2.is_file(), "Expected docs/recommender-v2.md"
    assert "v2" in (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert sha256(REPO_ROOT / "configs" / "quality" / "quality-v1.json") == (
        "d4383b6bfd899e0be8abfee7e28ee42533a9121d0565fdd6795d8fdf86e3e500"
    )
    assert "^h-[a-f0-9]{64}$" in (MIGRATIONS_DIR / "002_releases.sql").read_text(encoding="utf-8")

    # AST guard: zero top-level hm_recommender.v2 or recommend_v2 imports in tests/e2e/*.py
    for py_file in sorted((REPO_ROOT / "tests" / "e2e").glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    assert not alias.name.startswith("hm_recommender.v2")
                    assert alias.name != "hm_recommender.api.recommend_v2"
            elif isinstance(stmt, ast.ImportFrom):
                mod = stmt.module or ""
                assert not mod.startswith("hm_recommender.v2")
                assert mod != "hm_recommender.api.recommend_v2"

    v2_dir, _ = synthetic_v2_release
    monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(synthetic_image_zip))
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(tmp_path / "t11_img_cache"))
    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)

    with http_client:
        cat = http_client.get("/catalog", params={"q": "Dress", "index_group_name": "Ladieswear"})
        assert cat.status_code == 200
        items = cat.json()["items"]
        assert items and all(_TEN_DIGIT_RE.fullmatch(it["article_id"]) for it in items)

        infolist_calls = 0
        getinfo_calls = 0
        orig_infolist = zipfile.ZipFile.infolist
        orig_getinfo = zipfile.ZipFile.getinfo

        def _spy_infolist(self: zipfile.ZipFile):
            nonlocal infolist_calls
            infolist_calls += 1
            return orig_infolist(self)

        def _spy_getinfo(self: zipfile.ZipFile, name: str):
            nonlocal getinfo_calls
            getinfo_calls += 1
            return orig_getinfo(self, name)

        with monkeypatch.context() as mp:
            mp.setattr(zipfile.ZipFile, "infolist", _spy_infolist)
            mp.setattr(zipfile.ZipFile, "getinfo", _spy_getinfo)
            img = http_client.get(f"/articles/{items[0]['article_id']}/image")
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/jpeg"
        assert infolist_calls == 0 and getinfo_calls >= 1

    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    with ui_apptest_session(adapter, default_timeout=25) as app:
        assert not app.exception
        assert len(app.get("image")) > 0
        initial_rendered = visible_text(app)
        assert "Recomendaciones de vestuario" in initial_rendered
        assert re.search(r"\b[0-9]{10}\b", initial_rendered)
        assert not re.search(r"ART\.\s*[0-9]{8}\b", initial_rendered)

        profile_sb = find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None
        assert select_option_matching(profile_sb, r"comprador frecuente") is not None
        app.run()
        assert not app.exception
        assert len(app.get("image")) > 0
        rendered = visible_text(app)
        assert "Recomendaciones de vestuario" in rendered
        assert re.search(r"\b[0-9]{10}\b", rendered)
        assert not re.search(r"ART\.\s*[0-9]{8}\b", rendered)
        assert _V2_BADGE_RE.search(rendered), "Expected explainable v2 reason badge in UI"


# ---------------------------------------------------------------------------
# T12: F_PKG x F_REC x F_SEC x F_QUAL (Postgres Parity)
# ---------------------------------------------------------------------------


@pytest.mark.tier3
@pytest.mark.requires_postgres
def test_t3_pkg_rec_sec_qual_postgres_v2_lifecycle_and_events(
    postgres_serving_env: Any,
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
) -> None:
    two_stage_cls = _import_two_stage_state()
    client, registry, connection, _schema, tmp_path = postgres_serving_env
    v2_dir, v2_id = synthetic_v2_release
    h_id = verify_history_release(synthetic_h_release).release_id

    with connection() as conn:
        constraints = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'serving_releases'::regclass"
        ).fetchall()
        assert any("(h|v2)-[a-f0-9]{64}" in row[0] for row in constraints)
        indexes = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'interaction_events'"
        ).fetchall()
        assert len(indexes) >= 1

    input_root = tmp_path / "input"
    shutil.copytree(synthetic_h_release, input_root / "rel_h")
    shutil.copytree(v2_dir, input_root / "rel_v2")

    assert client.post("/admin/releases/prepare", json={"directory": "rel_v2"}).status_code == 403
    assert (
        client.post(
            "/admin/releases/prepare",
            headers={"Authorization": "Bearer wrong-token"},
            json={"directory": "rel_v2"},
        ).status_code
        == 403
    )

    headers = {"Authorization": "Bearer local-test-token"}
    assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"}).status_code == 200
    assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"}).status_code == 200

    assert client.post("/admin/releases/activate", headers=headers, json={"release_id": h_id}).status_code == 200
    assert isinstance(registry.snapshot(), HistoryState)

    assert client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200
    assert isinstance(registry.snapshot(), two_stage_cls)

    for payload in (
        {"customer_id": "cust_0000", "k": 6, "mode": "balanced"},
        {"customer_id": "cust_0000", "k": 6, "mode": "discovery"},
        {
            "customer_id": "anon-pg-t3",
            "k": 6,
            "mode": "balanced",
            "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
        },
    ):
        rec = client.post("/recommend", json=payload)
        assert rec.status_code == 200
        assert rec.json()["release_id"] == v2_id
        assert rec.json()["strategy"] == "two_stage_lgbm_v2"

    rb = client.post("/admin/releases/rollback", headers=headers)
    assert rb.status_code == 200
    assert rb.json()["release_id"] == h_id
    with connection() as conn:
        active_id, prev_id = conn.execute("SELECT active_id, previous_id FROM serving_pointer WHERE singleton").fetchone()
    assert active_id == h_id
    assert prev_id == v2_id
