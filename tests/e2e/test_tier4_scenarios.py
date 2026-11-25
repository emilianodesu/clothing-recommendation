"""Tier 4 real-world shopper and operator end-to-end scenarios.

IMPORTANT: Zero top-level ``import hm_recommender.v2...`` or
``import hm_recommender.api.recommend_v2`` statements are allowed here.
"""

from __future__ import annotations

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
import threading
import time as time_mod
from typing import Any
from unittest.mock import patch
from uuid import uuid4
import zipfile
import zlib

from fastapi.testclient import TestClient
import httpx
import polars as pl
import pytest

from hm_recommender.api import auth
from hm_recommender.api.app import create_app
from hm_recommender.api.recommend import HistoryState
from hm_recommender.data.io import sha256
from hm_recommender.releases.package import verify_history_release
from hm_recommender.ui.images import PLACEHOLDER, ImageCache
from tests.e2e.harness import (
    MIGRATIONS_DIR,
    REPO_ROOT,
    FakeEventRegistry,
    InMemoryApiAdapter,
    build_synthetic_image_zip,
    count_catalog_calls,
    find_buttons,
    find_control,
    make_recording_transport,
    resolve_artifacts_dir,
    resolve_hm_v1_dir,
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
# Scenario 1: Anonymous Cold-Start Onboarding -> View/Click/Purchase -> Discovery
# ---------------------------------------------------------------------------


@pytest.mark.tier4
def test_t4_s1_anonymous_cold_start_onboarding_live_signals_and_discovery(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    retrieval_mod = importlib.import_module("hm_recommender.v2.retrieval")
    assert retrieval_mod.EVENT_KIND_WEIGHTS == {"purchase": 1.0, "click": 0.55, "view": 0.25}
    assert "<25" in retrieval_mod.AGE_BINS

    v2_dir, v2_id = synthetic_v2_release
    cat_df = pl.read_parquet(v2_dir / "catalog.parquet")
    catalog_by_id = {row["article_id"]: row for row in cat_df.to_dicts()}

    # 1. Real create_app + TwoStageState contract verification
    http_client, _, real_sid = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path, enable_events=True)
    assert real_sid is not None
    with http_client:
        anon_pref = http_client.post(
            "/recommend",
            json={
                "customer_id": "anon-s1-shopper",
                "k": 12,
                "mode": "balanced",
                "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
            },
        )
        assert anon_pref.status_code == 200
        pref_body = anon_pref.json()
        assert pref_body["release_id"] == v2_id
        assert pref_body["strategy"] == "two_stage_lgbm_v2"
        assert pref_body["fallback"] is True
        assert pref_body["items"][0]["reason"] == "popular_for_segment"
        assert catalog_by_id[pref_body["items"][0]["article_id"]]["index_group_name"] == "Menswear"

        cat_res = http_client.get(
            "/catalog",
            params={"q": "Dress", "index_group_name": "Ladieswear", "limit": 12},
        )
        assert cat_res.status_code == 200
        cat_items = cat_res.json()["items"]
        assert cat_items
        assert all(it["index_group_name"] == "Ladieswear" for it in cat_items)
        assert all("dress" in json.dumps(it).lower() for it in cat_items)
        assert all(_TEN_DIGIT_RE.fullmatch(it["article_id"]) for it in cat_items)

        cutoff = date(2020, 9, 9)
        ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
        for aid, kind in (("0000000018", "view"), ("0000000024", "click"), ("0000000028", "purchase")):
            ev = http_client.post(
                "/events",
                json={
                    "event_id": str(uuid4()),
                    "stream_id": real_sid,
                    "release_id": v2_id,
                    "customer_id": "anon-s1-shopper",
                    "article_id": aid,
                    "kind": kind,
                    "origin": "synthetic",
                    "event_time": ev_time,
                },
            )
            assert ev.status_code == 201
        ref_meta = fake_registry.refresh_in_memory_stream(real_sid, cutoff)
        assert ref_meta["processed_events"] == 3

        bal = http_client.post(
            f"/streams/{real_sid}/recommend",
            json={"customer_id": "anon-s1-shopper", "k": 12, "mode": "balanced"},
        )
        assert bal.status_code == 200
        bal_body = bal.json()
        assert bal_body["fallback"] is False
        bal_ids = [it["article_id"] for it in bal_body["items"]]
        assert {"0000000018", "0000000024", "0000000028"}.issubset(set(bal_ids))

        disc = http_client.post(
            f"/streams/{real_sid}/recommend",
            json={"customer_id": "anon-s1-shopper", "k": 12, "mode": "discovery"},
        )
        assert disc.status_code == 200
        disc_body = disc.json()
        assert disc_body["fallback"] is False
        disc_ids = [it["article_id"] for it in disc_body["items"]]
        assert "0000000028" not in disc_ids
        assert "0000000024" in disc_ids
        assert "0000000018" in disc_ids
        disc_pcodes = [catalog_by_id[aid]["product_code"] for aid in disc_ids]
        assert len(disc_pcodes) == len(set(disc_pcodes))

    # 2. Full Streamlit AppTest cold-start shopper journey
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        assert count_catalog_calls(calls) == 1
        init_txt = visible_text(app)
        assert re.search(r"\b[0-9]{10}\b", init_txt)
        assert not re.search(r"ART\.\s*[0-9]{8}\b", init_txt)

        profile_sb = find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None
        assert select_option_matching(profile_sb, r"nuevo visitante") is not None
        app.run()

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
        assert pref_calls[-1]["response_json"]["fallback"] is True
        assert adapter.catalog_by_id[pref_calls[-1]["response_json"]["items"][0]["article_id"]]["index_group_name"] == "Menswear"
        assert v2_id not in visible_text(app)

        search_input = find_control(
            app,
            ("text_input",),
            label_re=r"buscar|búsqueda|palabra|catálogo|search",
            key_re=r"search|query|catalog_q|^q$",
        )
        assert search_input is not None
        calls.clear()
        search_input.input("T-shirt").run()
        assert not app.exception
        assert any(c["method"] == "GET" and c["path"] == "/catalog" and c["params"].get("q") == "T-shirt" for c in calls)
        search_input = find_control(
            app,
            ("text_input",),
            label_re=r"buscar|búsqueda|palabra|catálogo|search",
            key_re=r"search|query|catalog_q|^q$",
        )
        if search_input is not None:
            search_input.input("").run()

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
        buy_btns[0].click().run()
        assert not app.exception

        assert {"view", "click", "purchase"}.issubset({e["kind"] for e in adapter.store.events_log})
        purchased_aid = [e for e in adapter.store.events_log if e["kind"] == "purchase"][-1]["article_id"]
        assert re.search(r"actividad en (?:esta|la) sesión|vistos|favoritos|comprados", visible_text(app), re.I)

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

        ui_disc_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("mode") == "discovery"
        ]
        assert ui_disc_calls
        ui_disc_ids = [it["article_id"] for it in (ui_disc_calls[-1]["response_json"] or {}).get("items", [])]
        assert purchased_aid not in ui_disc_ids
        ui_pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in ui_disc_ids]
        assert len(ui_pcodes) == len(set(ui_pcodes))


# ---------------------------------------------------------------------------
# Scenario 2: Returning Repeat Shopper -> Variants/Co-Purchase -> Discovery Mode
# ---------------------------------------------------------------------------


@pytest.mark.tier4
def test_t4_s2_returning_repeat_shopper_variants_copurchase_and_discovery(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    retrieval_mod = importlib.import_module("hm_recommender.v2.retrieval")
    explainable_reasons = set(retrieval_mod.EXPLAINABLE_REASONS)

    v2_dir, _ = synthetic_v2_release
    hist_df = pl.read_parquet(v2_dir / "histories.parquet")
    cat_df = pl.read_parquet(v2_dir / "catalog.parquet")
    purchased_cust0 = set(hist_df.filter(pl.col("customer_id") == "cust_0000")["article_id"].to_list())
    pcode_by_id = dict(zip(cat_df["article_id"].to_list(), cat_df["product_code"].to_list()))
    assert purchased_cust0

    # 1. Real create_app + TwoStageState
    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)
    with http_client:
        bal = http_client.post("/recommend", json={"customer_id": "cust_0000", "k": 12, "mode": "balanced"})
        assert bal.status_code == 200
        bal_body = bal.json()
        assert bal_body["strategy"] == "two_stage_lgbm_v2"
        assert bal_body["fallback"] is False
        bal_items = bal_body["items"]
        assert len(bal_items) == 12
        assert [it["rank"] for it in bal_items] == list(range(1, 13))
        assert all(it["reason"] in explainable_reasons for it in bal_items)
        assert any(
            it["reason"]
            in {
                "recent_purchase",
                "same_product_variant",
                "frequently_bought_together",
                "latent_style_match",
                "content_similar",
            }
            for it in bal_items
        )
        bal_ids = [it["article_id"] for it in bal_items]

        det = http_client.post("/articles/details", json={"article_ids": bal_ids})
        assert det.status_code == 200
        assert len(det.json()["items"]) == len(bal_ids)
        assert all("product_type_name" in it and "colour_group_name" in it for it in det.json()["items"])

        disc = http_client.post("/recommend", json={"customer_id": "cust_0000", "k": 12, "mode": "discovery"})
        assert disc.status_code == 200
        disc_items = disc.json()["items"]
        assert 1 <= len(disc_items) <= 12
        disc_ids = [it["article_id"] for it in disc_items]
        assert purchased_cust0.isdisjoint(disc_ids)
        assert "recent_purchase" not in {it["reason"] for it in disc_items}
        disc_pcodes = [pcode_by_id[aid] for aid in disc_ids]
        assert len(disc_pcodes) == len(set(disc_pcodes))
        assert disc_ids != bal_ids

    # 2. Real Streamlit AppTest repeat shopper workflow
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
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

        bal_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("mode", "balanced") == "balanced"
        ]
        assert bal_calls
        assert bal_calls[-1]["response_json"]["fallback"] is False
        rendered_bal = visible_text(app)
        assert _V2_BADGE_RE.search(rendered_bal)

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

        ui_disc_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("mode") == "discovery"
        ]
        assert ui_disc_calls
        ui_disc_items = (ui_disc_calls[-1]["response_json"] or {}).get("items", [])
        assert ui_disc_items
        ui_disc_ids = [it["article_id"] for it in ui_disc_items]
        assert purchased_cust0.isdisjoint(ui_disc_ids)
        assert "recent_purchase" not in {it["reason"] for it in ui_disc_items}
        ui_pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in ui_disc_ids]
        assert len(ui_pcodes) == len(set(ui_pcodes))


# ---------------------------------------------------------------------------
# Scenario 3: Operator v2 Rollout Under Live Traffic & Zero-Downtime Rollback
# ---------------------------------------------------------------------------


@pytest.mark.tier4
def test_t4_s3_operator_v2_rollout_live_traffic_and_zero_downtime_rollback(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    verify_v2 = getattr(v2_pkg, "verify_v2_release", None)
    assert callable(verify_v2)
    two_stage_cls = _import_two_stage_state()

    v2_dir, v2_id = synthetic_v2_release
    assert verify_v2(v2_dir).release_id == v2_id
    h_id = verify_history_release(synthetic_h_release).release_id

    input_root = tmp_path / "input_ops"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(synthetic_h_release, input_root / "rel_h")
    shutil.copytree(v2_dir, input_root / "rel_v2")

    sid = fake_registry.enable_in_memory_events()
    admin_token = "ops-token-2026"
    adm_hdr = {"Authorization": f"Bearer {admin_token}"}
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))

    def _run_concurrent_traffic(base_url: str, action_fn) -> None:
        stop_event = threading.Event()
        errors: list[str] = []
        observed: list[tuple[str, str]] = []
        lock = threading.Lock()
        completed = 0

        def _worker() -> None:
            nonlocal completed
            with httpx.Client(base_url=base_url, trust_env=False, timeout=5.0) as t_client:
                while not stop_event.is_set():
                    try:
                        r_ready = t_client.get("/health/ready")
                        if r_ready.status_code != 200:
                            errors.append(f"/health/ready -> {r_ready.status_code}")
                        r_cat = t_client.get("/catalog", params={"offset": 0, "limit": 6})
                        if r_cat.status_code != 200:
                            errors.append(f"/catalog -> {r_cat.status_code}")
                        r_rec = t_client.post("/recommend", json={"customer_id": "cust_0000", "k": 6})
                        if r_rec.status_code != 200:
                            errors.append(f"/recommend -> {r_rec.status_code}")
                        else:
                            rb = r_rec.json()
                            with lock:
                                observed.append((rb["release_id"], rb["strategy"]))
                        with lock:
                            completed += 1
                    except Exception as exc:
                        errors.append(repr(exc))
                    time_mod.sleep(0.005)

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        try:
            deadline = time_mod.monotonic() + 5.0
            while completed < 5 and time_mod.monotonic() < deadline:
                time_mod.sleep(0.01)
            action_fn()
            target = completed + 20
            deadline2 = time_mod.monotonic() + 10.0
            while completed < target and time_mod.monotonic() < deadline2:
                time_mod.sleep(0.01)
        finally:
            stop_event.set()
            th.join(timeout=5.0)

        assert errors == [], f"Zero-downtime traffic errors: {errors}"
        assert completed >= 20
        for rel_id, strat in observed:
            if rel_id == h_id:
                assert strat == "recent_then_popular"
            elif rel_id == v2_id:
                assert strat == "two_stage_lgbm_v2"
            else:
                raise AssertionError(f"Unexpected release_id during swap: {rel_id}")

    with start_uvicorn_app(app, lifespan="on") as base_url:
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 503

            assert client.post("/admin/releases/prepare", headers=adm_hdr, json={"directory": "rel_h"}).status_code == 200
            assert client.post("/admin/releases/activate", headers=adm_hdr, json={"release_id": h_id}).status_code == 200
            assert client.get("/health/ready").json()["release_id"] == h_id
            assert client.post("/recommend", json={"customer_id": "cust_0000", "k": 6}).json()["strategy"] == "recent_then_popular"

            assert client.post("/admin/releases/prepare", headers=adm_hdr, json={"directory": "rel_v2"}).status_code == 200
            assert client.get("/health/ready").json()["release_id"] == h_id

            # Zero-downtime h-* -> v2-* upgrade under concurrent traffic
            def _activate_v2() -> None:
                res = client.post("/admin/releases/activate", headers=adm_hdr, json={"release_id": v2_id})
                assert res.status_code == 200

            _run_concurrent_traffic(base_url, _activate_v2)
            assert isinstance(fake_registry.snapshot(), two_stage_cls)
            assert client.get("/health/ready").json()["release_id"] == v2_id

            for payload in (
                {"customer_id": "cust_0000", "k": 6, "mode": "balanced"},
                {"customer_id": "cust_0000", "k": 6, "mode": "discovery"},
                {
                    "customer_id": "anon-ops",
                    "k": 6,
                    "mode": "balanced",
                    "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
                },
            ):
                r = client.post("/recommend", json=payload)
                assert r.status_code == 200
                assert r.json()["strategy"] == "two_stage_lgbm_v2"

            cutoff = date(2020, 9, 9)
            ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
            for aid, kind in (("0000000024", "click"), ("0000000028", "purchase")):
                assert (
                    client.post(
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
                    ).status_code
                    == 201
                )
            fake_registry.refresh_in_memory_stream(sid, cutoff)
            s_disc = client.post(
                f"/streams/{sid}/recommend",
                json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
            )
            assert s_disc.status_code == 200
            assert s_disc.json()["fallback"] is False
            assert "0000000028" not in [it["article_id"] for it in s_disc.json()["items"]]

            # Zero-downtime v2-* -> h-* rollback under concurrent traffic
            def _rollback_h() -> None:
                res = client.post("/admin/releases/rollback", headers=adm_hdr)
                assert res.status_code == 200
                assert res.json()["release_id"] == h_id

            _run_concurrent_traffic(base_url, _rollback_h)
            assert isinstance(fake_registry.snapshot(), HistoryState)
            assert client.get("/health/ready").json()["release_id"] == h_id
            assert client.get("/catalog").json()["release_id"] == h_id
            assert client.post("/recommend", json={"customer_id": "cust_0000", "k": 6}).json()["strategy"] == "recent_then_popular"

            # Stale v2-* event submission rejected cleanly with 409 (not 500)
            stale_ev = client.post(
                "/events",
                json={
                    "event_id": str(uuid4()),
                    "stream_id": sid,
                    "release_id": v2_id,
                    "customer_id": "cust_0007",
                    "article_id": "0000000024",
                    "kind": "view",
                    "origin": "synthetic",
                    "event_time": ev_time,
                },
            )
            assert stale_ev.status_code == 409

            metrics_txt = client.get("/metrics").text
            ok_m = re.search(r'hmrec_recommendations_total\{status="ok"\}\s+(\d+)', metrics_txt)
            unavail_m = re.search(r'hmrec_recommendations_total\{status="unavailable"\}\s+(\d+)', metrics_txt)
            assert ok_m is not None and int(ok_m.group(1)) > 0
            assert unavail_m is not None and int(unavail_m.group(1)) == 0


# ---------------------------------------------------------------------------
# Scenario 4: Multi-Session Authenticated Shopper Behind Security Portal
# ---------------------------------------------------------------------------


@pytest.mark.tier4
def test_t4_s4_multi_session_authenticated_shopper_portal_gateway_and_v2(
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

    gw_root = tmp_path / "gw_s4"
    gw_root.mkdir(parents=True, exist_ok=True)
    with patch.object(server_gateway, "run", return_value="$2a$14$" + ("A" * 53)):
        server_gateway.configure(gw_root, "hm.example.com", "alice", password="AliceStrongPassword2026!")

    gw_json = gw_root / ".runtime" / "server" / "gateway.json"
    caddyfile_path = gw_root / ".runtime" / "server" / "Caddyfile"
    assert stat.S_IMODE(gw_json.stat().st_mode) == 0o600
    gw_data = json.loads(gw_json.read_text(encoding="utf-8"))
    assert gw_data["password_hashes"]["alice"].startswith("scrypt$")

    caddy_txt = caddyfile_path.read_text(encoding="utf-8")
    for token in ("path /login /logout", "forward_auth api:8000", "uri /auth/verify", "header_up -Upgrade", "header_up -Authorization", "reverse_proxy ui:8501"):
        assert token in caddy_txt

    legacy_pwd = "LegacyShopperPass2026!"
    gw_data["password_hashes"]["legacy_user"] = hashlib.sha256(legacy_pwd.encode("utf-8")).hexdigest()
    gw_json.write_text(json.dumps(gw_data), encoding="utf-8")
    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(gw_json))
    with auth._HASH_CACHE_LOCK:
        auth._HASH_CACHE.clear()

    v2_dir, v2_id = synthetic_v2_release
    input_root = tmp_path / "input_s4"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_dir, input_root / "rel_v2")
    admin_token = "portal-admin-2026"
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))

    with start_uvicorn_app(app, lifespan="on") as base_url:
        with httpx.Client(base_url=base_url, trust_env=False) as ops:
            adm_hdr = {"Authorization": f"Bearer {admin_token}"}
            assert ops.post("/admin/releases/prepare", headers=adm_hdr, json={"directory": "rel_v2"}).status_code == 200
            assert ops.post("/admin/releases/activate", headers=adm_hdr, json={"release_id": v2_id}).status_code == 200

            assert ops.get("/auth/verify", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 307
            assert ops.get("/auth/verify", headers={"Accept": "application/json"}, follow_redirects=False).status_code == 401
            assert (
                ops.get(
                    "/auth/verify",
                    headers={"Accept": "text/html", "X-Forwarded-Uri": "/_stcore/stream"},
                    follow_redirects=False,
                ).status_code
                == 401
            )

        with (
            httpx.Client(base_url=base_url, trust_env=False) as client_a,
            httpx.Client(base_url=base_url, trust_env=False) as client_b,
            httpx.Client(base_url=base_url, trust_env=False) as client_c,
            httpx.Client(base_url=base_url, trust_env=False) as client_d,
        ):
            # Session 1: alice (remember=False -> 12h cookie, HMAC domain separation)
            get_login = client_a.get("/login")
            assert get_login.status_code == 200
            for hdr, expected in auth.SECURITY_HEADERS.items():
                assert get_login.headers.get(hdr.lower()) == expected

            res_a = client_a.post(
                "/login",
                data={"username": "alice", "password": "AliceStrongPassword2026!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert res_a.status_code == 303
            sc_a = res_a.headers["set-cookie"]
            assert f"Max-Age={auth.SHORT_SESSION_DURATION}" in sc_a
            assert "HttpOnly" in sc_a and "SameSite=lax" in sc_a and "Secure" in sc_a
            tok_a = res_a.cookies[auth.COOKIE_NAME]
            derived = hmac.new(admin_token.encode("utf-8"), b"hmrec:session-secret:v1", hashlib.sha256).digest()
            assert auth.verify_session_token(tok_a, admin_token.encode("utf-8")) is None
            assert auth.verify_session_token(tok_a, derived) == "alice"
            assert client_a.get("/auth/verify", cookies={auth.COOKIE_NAME: tok_a}).status_code == 200

            # Session 2: legacy_user (remember=True -> 30d + Basic Auth)
            res_b = client_b.post(
                "/login",
                data={"username": "legacy_user", "password": legacy_pwd, "remember": "1"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert res_b.status_code == 303
            assert f"Max-Age={auth.DEFAULT_SESSION_DURATION}" in res_b.headers["set-cookie"]
            tok_b = res_b.cookies[auth.COOKIE_NAME]
            assert client_b.get("/auth/verify", cookies={auth.COOKIE_NAME: tok_b}).status_code == 200
            basic_b = "Basic " + base64.b64encode(f"legacy_user:{legacy_pwd}".encode("utf-8")).decode("ascii")
            assert client_b.get("/auth/verify", headers={"Authorization": basic_b}).status_code == 200

            # Brute-force rate-limit on client_c does not block client_a / client_b or /recommend
            xff = {"X-Forwarded-For": "203.0.113.99"}
            for _ in range(auth.MAX_LOGIN_ATTEMPTS):
                assert (
                    client_c.post(
                        "/login",
                        data={"username": "alice", "password": "wrong-password"},
                        headers={"Content-Type": "application/x-www-form-urlencoded", **xff},
                        follow_redirects=False,
                    ).status_code
                    == 200
                )
            assert (
                client_c.post(
                    "/login",
                    data={"username": "alice", "password": "wrong-password"},
                    headers={"Content-Type": "application/x-www-form-urlencoded", **xff},
                    follow_redirects=False,
                ).status_code
                == 429
            )

            assert client_a.get("/auth/verify", cookies={auth.COOKIE_NAME: tok_a}).status_code == 200
            assert client_b.get("/auth/verify", cookies={auth.COOKIE_NAME: tok_b}).status_code == 200
            rec = client_a.post("/recommend", json={"customer_id": "cust_0000", "k": 6, "mode": "discovery"})
            assert rec.status_code == 200
            assert rec.json()["strategy"] == "two_stage_lgbm_v2"

            # Provision charlie live & verify stat-cache reload without restarting uvicorn
            with patch.object(server_gateway, "run", return_value="$2a$14$" + ("C" * 53)):
                server_gateway.configure(gw_root, "hm.example.com", "charlie", password="CharlieStrongPassword2026!")
            st = gw_json.stat()
            os.utime(gw_json, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            res_d = client_d.post(
                "/login",
                data={"username": "charlie", "password": "CharlieStrongPassword2026!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert res_d.status_code == 303

            # Logout invalidates Session 1
            logout_a = client_a.get("/logout", cookies={auth.COOKIE_NAME: tok_a}, follow_redirects=False)
            assert logout_a.status_code == 303
            assert logout_a.headers["location"] == "/login"
            assert "Max-Age=0" in logout_a.headers["set-cookie"]
            assert (
                client_a.get("/auth/verify", headers={"Accept": "text/html"}, follow_redirects=False).status_code
                == 307
            )

    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    with ui_apptest_session(adapter, default_timeout=25) as ui_app:
        assert not ui_app.exception
        rendered = visible_text(ui_app)
        assert 'href="/logout"' in rendered or "Cerrar sesión" in rendered


# ---------------------------------------------------------------------------
# Scenario 5: Image-Backed Storefront (O(1) ZIP, LRU Eviction, SVG & 10-Digit IDs)
# ---------------------------------------------------------------------------


@pytest.mark.tier4
def test_t4_s5_image_backed_storefront_o1_lru_svg_and_10digit_ids(
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _import_two_stage_state()
    v2_dir, _ = synthetic_v2_release

    partial_zip = build_synthetic_image_zip(
        tmp_path / "storefront_partial.zip",
        [f"{i:010d}" for i in range(39)],
        corrupt_ids=["0000000038"],
    )

    # 1. Direct ImageCache O(1) ZipFile.getinfo() & LRU eviction
    o1_cache = ImageCache(partial_zip, tmp_path / "o1_cache")
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
        b0, m0 = o1_cache.read("0000000000")
        b15, m15 = o1_cache.read("0000000015")
    assert m0 == "image/jpeg" and b0.startswith(b"\xff\xd8") and b0.endswith(b"\xff\xd9")
    assert m15 == "image/jpeg" and b15.startswith(b"\xff\xd8") and b15.endswith(b"\xff\xd9")
    assert infolist_calls == 0 and getinfo_calls >= 2

    lru_dir = tmp_path / "lru_cache"
    lru_cache = ImageCache(partial_zip, lru_dir, max_bytes=1500)
    assert lru_cache.read("0000000000")[1] == "image/jpeg"
    time_mod.sleep(0.02)
    assert lru_cache.read("0000000001")[1] == "image/jpeg"
    time_mod.sleep(0.02)
    assert lru_cache.read("0000000000")[1] == "image/jpeg"
    time_mod.sleep(0.02)
    assert lru_cache.read("0000000002")[1] == "image/jpeg"

    jpg_files = list(lru_dir.glob("*.jpg"))
    assert len(jpg_files) == 2
    assert sum(p.stat().st_size for p in jpg_files) <= 1500
    zip_stat = partial_zip.stat()

    def _cache_key(aid: str) -> str:
        return hashlib.sha256(
            f"{partial_zip.resolve()}:{zip_stat.st_size}:{zip_stat.st_mtime_ns}:{aid}".encode()
        ).hexdigest() + ".jpg"

    assert (lru_dir / _cache_key("0000000000")).is_file()
    assert (lru_dir / _cache_key("0000000002")).is_file()
    assert not (lru_dir / _cache_key("0000000001")).exists()

    # 2. Real create_app + v2-* + in-memory /catalog cache + image endpoints
    monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(partial_zip))
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(tmp_path / "api_img_cache"))
    monkeypatch.setenv("HMREC_IMAGE_CACHE_BYTES", "1500")

    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)
    with http_client:
        warm = http_client.get("/catalog", params={"offset": 0, "limit": 12})
        assert warm.status_code == 200

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
            cat_q = http_client.get("/catalog", params={"q": "Atelier", "index_group_name": "Ladieswear", "limit": 12})
            cat_p2 = http_client.get("/catalog", params={"offset": 12, "limit": 12})
        assert parquet_reads == 0
        assert cat_q.status_code == 200 and cat_p2.status_code == 200
        assert all(_TEN_DIGIT_RE.fullmatch(it["article_id"]) for it in cat_q.json()["items"])

        ok_img = http_client.get("/articles/0000000000/image")
        assert ok_img.status_code == 200
        assert ok_img.headers["content-type"] == "image/jpeg"
        assert ok_img.content.startswith(b"\xff\xd8") and ok_img.content.endswith(b"\xff\xd9")

        for bad_aid in ("0000000038", "0000000039"):
            fb_img = http_client.get(f"/articles/{bad_aid}/image")
            assert fb_img.status_code == 200
            assert fb_img.headers["content-type"] == "image/svg+xml"
            assert fb_img.content == PLACEHOLDER

    # Missing archive fallback on a fresh create_app
    monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(tmp_path / "nonexistent.zip"))
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(tmp_path / "api_missing_cache"))
    missing_reg = FakeEventRegistry(tmp_path / "missing_reg")
    missing_client, _, _ = _prepare_and_activate_v2(missing_reg, v2_dir, tmp_path)
    with missing_client:
        miss_img = missing_client.get("/articles/0000000000/image")
        assert miss_img.status_code == 200
        assert miss_img.headers["content-type"] == "image/svg+xml"
        assert miss_img.content == PLACEHOLDER

    # 3. Real UI AppTest storefront browsing with partial & missing archives
    adapter_partial = InMemoryApiAdapter(
        v2_dir,
        image_zip=partial_zip,
        image_cache_dir=tmp_path / "ui_partial_cache",
    )
    transport, calls = make_recording_transport(adapter_partial)
    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        assert count_catalog_calls(calls) == 1
        assert len(app.get("image")) > 0
        txt = visible_text(app)
        assert re.search(r"\b[0-9]{10}\b", txt)
        assert not re.search(r"ART\.\s*[0-9]{8}\b", txt)

        next_btns = find_buttons(app, key_re=r"btn_next_page|next_page", label_re=r"siguiente")
        assert next_btns
        calls.clear()
        next_btns[0].click().run()
        assert not app.exception
        cat_calls = [c for c in calls if c["method"] == "GET" and c["path"] == "/catalog"]
        assert len(cat_calls) <= 1
        if cat_calls:
            assert cat_calls[0]["params"].get("offset") == "12"

    adapter_none = InMemoryApiAdapter(
        v2_dir,
        image_zip=None,
        image_cache_dir=tmp_path / "ui_none_cache",
    )
    with ui_apptest_session(adapter_none, default_timeout=25) as app_none:
        assert not app_none.exception
        assert len(app_none.get("image")) > 0


# ---------------------------------------------------------------------------
# Scenario 6: End-to-End Release Acceptance Audit (Quality, Docs, Host, v1 Intact)
# ---------------------------------------------------------------------------


@pytest.mark.tier4
def test_t4_s6_release_acceptance_audit_quality_docs_host_and_v1_intact() -> None:
    # 1. quality-v2 config & hm_recommender.v2.evaluate gates + /artifacts results
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

    v2_eval = importlib.import_module("hm_recommender.v2.evaluate")
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
    assert callable(gate_fn)
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015) is True
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015, mean_map_at_12=0.02544361) is False
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015, discovery_map_at_12=0.006437) is False
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.015, test_2020_09_16_map_at_12=0.02628451) is False
    assert _invoke_summary_gate(gate_fn, users=1000, rel_delta=-0.0201) is False

    artifacts = resolve_artifacts_dir()
    assert artifacts is not None, "Expected /artifacts mount for release acceptance audit"
    candidates = [
        *(sorted((artifacts / "quality-v2").glob("*.json")) if (artifacts / "quality-v2").is_dir() else []),
        *(sorted((artifacts / "diagnostics").glob("*quality-v2*.json")) if (artifacts / "diagnostics").is_dir() else []),
        *(sorted((artifacts / "diagnostics").glob("*v2*.json")) if (artifacts / "diagnostics").is_dir() else []),
        *(sorted((artifacts / "serving").glob("v2-*/validation-reference.json")) if (artifacts / "serving").is_dir() else []),
        *(sorted((artifacts / "releases-v2").glob("v2-*/validation-reference.json")) if (artifacts / "releases-v2").is_dir() else []),
    ]
    assert candidates, "Expected quality-v2 benchmark JSON under /artifacts"
    valid_reports: list[dict[str, Any]] = []
    for cand in candidates:
        data = json.loads(cand.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("metrics_measured_for_this_package") is False:
            continue
        sum_v1_v3 = data.get("summary_v1_v3")
        cand_summary_v2 = sum_v1_v3.get("v2") if isinstance(sum_v1_v3, dict) else None
        summary_cand = data.get("summary", data)
        if isinstance(cand_summary_v2, dict) and "map_at_12" in cand_summary_v2:
            valid_reports.append(data)
        elif isinstance(summary_cand, dict) and ("mean_map_at_12" in summary_cand or "map_at_12" in summary_cand):
            valid_reports.append(data)
    report: dict[str, Any] | None = next(
        (
            r
            for r in valid_reports
            if r.get("protocol") == "quality-v2"
            or (isinstance(r.get("summary_v1_v3"), dict) and isinstance(r["summary_v1_v3"].get("v2"), dict))
            or (isinstance(r.get("summary"), dict) and r["summary"].get("protocol") == "quality-v2")
        ),
        valid_reports[0] if valid_reports else None,
    )
    assert report is not None
    if isinstance(report.get("summary_v1_v3"), dict) and isinstance(report["summary_v1_v3"].get("v2"), dict):
        v2_sum = report["summary_v1_v3"]["v2"]
        mean_map = float(v2_sum["map_at_12"])
        assert mean_map > 0.022225
        assert mean_map > 0.025444
        assert mean_map >= 0.031
        disc_map = float(v2_sum["discovery_map_at_12"])
        assert disc_map > 0.006437
        desc_test = report.get("descriptive_test_2020_09_16") or {}
        test_map = float(((desc_test.get("v2") or {}).get("metrics") or {}).get("map_at_12", 0.0))
        assert test_map > 0.026285
        gates = report.get("gates") or {}
        seg_checks = gates.get("segment_checks")
        assert isinstance(seg_checks, list) and len(seg_checks) > 0
        assert all(
            int(s["users"]) < 1000 or (float(s["relative_diff"]) >= -0.02 and s["passed"] is True)
            for s in seg_checks
        )
        assert (gates.get("checks") or {}).get("zero_segment_regressions") is True
        assert gates.get("all_required_gates_passed") is True
    else:
        summary = report.get("summary", report)
        mean_map = float(summary.get("mean_map_at_12", summary.get("map_at_12", 0.0)))
        assert mean_map > 0.022225
        assert mean_map > 0.025444
        assert mean_map >= 0.031
        disc_map = float(summary.get("discovery_map_at_12", summary.get("discovery_map", 0.0)))
        assert disc_map > 0.006437
        test_block = summary.get("test_2020_09_16", report.get("test_2020_09_16", {}))
        test_map = float(test_block.get("map_at_12", summary.get("test_map_at_12", 0.0)))
        assert test_map > 0.026285
        has_reg_field = False
        if "regressing_segments_over_2pct" in summary:
            assert summary["regressing_segments_over_2pct"] == []
            has_reg_field = True
        if "regressing_segments" in summary:
            assert summary["regressing_segments"] == []
            has_reg_field = True
        segs = summary.get("segments")
        if segs is None:
            segs = report.get("segments")
        if segs is not None:
            assert segs and all(
                int(s.get("users", s.get("buyers", 0))) < 1000
                or float(s.get("rel_delta", s.get("relative_delta", 0.0))) >= -0.02
                for s in segs
            )
            has_reg_field = True
        assert has_reg_field, "Expected 'segments' or 'regressing_segments*' in quality-v2 report"

    # 2. Portable v2-* package verification & 005 migration
    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    verify_v2 = getattr(v2_pkg, "verify_v2_release", None)
    assert callable(verify_v2)
    v2_dirs = sorted(
        p
        for p in (
            *(list((artifacts / "serving").glob("v2-*")) if (artifacts / "serving").is_dir() else []),
            *(list((artifacts / "releases-v2").glob("v2-*")) if (artifacts / "releases-v2").is_dir() else []),
        )
        if p.is_dir()
    )
    assert v2_dirs, "Expected portable v2-* package in /artifacts/serving or /artifacts/releases-v2"
    for v2_pkg_dir in v2_dirs:
        assert re.fullmatch(r"^v2-[a-f0-9]{64}$", v2_pkg_dir.name)
        assert verify_v2(v2_pkg_dir).release_id == v2_pkg_dir.name

    mig_005 = MIGRATIONS_DIR / "005_v2_releases.sql"
    assert mig_005.is_file()
    assert "(h|v2)-[a-f0-9]{64}" in mig_005.read_text(encoding="utf-8")

    # 3. Docs links, docs/recommender-v2.md & README.md
    links_proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_docs_links.py"), "--root", str(REPO_ROOT), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert links_proc.returncode == 0, links_proc.stderr
    link_report = json.loads(links_proc.stdout)
    assert link_report["broken_count"] == 0
    assert link_report["files_checked"] >= 42
    assert link_report["local_links_checked"] >= 340

    doc_v2 = REPO_ROOT / "docs" / "recommender-v2.md"
    assert doc_v2.is_file()
    doc_txt = doc_v2.read_text(encoding="utf-8").lower()
    for token in ("quality-v2", "v1", "v2", "v3", "2020-09-16", "40 ms"):
        assert token in doc_txt
    readme_txt = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for token in ("pytest tests/unit", "unittest discover -s tests/host", "check_docs_links.py", "v2"):
        assert token in readme_txt

    # 4. Host unittest suite
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

    # 5. Frozen v1, Q1-Q4, hm-v1 & entrega-academica-2026-10-24 intact
    assert sha256(REPO_ROOT / "configs" / "quality" / "quality-v1.json") == (
        "d4383b6bfd899e0be8abfee7e28ee42533a9121d0565fdd6795d8fdf86e3e500"
    )
    for fname, expected_digest in {
        "001_bootstrap.sql": "04e94dace91a944eb46b8dc14de0482596a0f6f7a25261f0420f1db1b07b9b83",
        "002_releases.sql": "a1bec8206c237a41b81cdabc54f46a4b2e7f784a8be766174b62558b20a7925d",
        "003_events.sql": "3f95e9807c27dc9dd8403e9215c763305fdbd36d53ba6ca31ec7c836f331e71a",
        "004_pipeline.sql": "ec4698f41fe3055be8d5a4085029effc370f925102499c2ab40e932b4275dda3",
    }.items():
        assert sha256(MIGRATIONS_DIR / fname) == expected_digest

    archive_root = REPO_ROOT / "docs" / "archive"
    for sub in ("evidence", "prompts", "legacy_scripts", "legacy_tests"):
        assert (archive_root / sub).is_dir() and any((archive_root / sub).iterdir())
    for key_file in (
        "local-quality-q1.json",
        "local-quality-q2.json",
        "local-quality-q3.json",
        "local-quality-q4.json",
        "quality-decision-mac24.json",
        "final-review-mac24.json",
    ):
        assert (archive_root / "evidence" / key_file).is_file()

    hm_v1 = resolve_hm_v1_dir()
    assert hm_v1 is not None
    hm_manifest = json.loads((hm_v1 / "manifest.json").read_text(encoding="utf-8"))
    assert hm_manifest["counts"]["articles"] == 105542
    assert hm_manifest["counts"]["customers"] == 1371980
    assert hm_manifest["counts"]["transactions"] == 31788324

    diag = artifacts / "diagnostics"
    for name in ("q1-full-V1-status.json", "q1-full-V2-status.json", "q1-full-V3-status.json", "q4-status.json"):
        assert (diag / name).is_file()
    assert json.loads((diag / "q4-status.json").read_text(encoding="utf-8"))["status"] == "passed"
    assert (artifacts / "quality-budget" / "quality-v1.json").is_file()
    assert (artifacts / ".final-test-once.json").is_file()
    assert (artifacts / ".final-test-continuation.json").is_file()
    assert len(list((artifacts / "final-private").glob("*/failure-private.log"))) >= 2
    assert list((artifacts / "serving").glob("h-*"))

    expected_commit = "5cf1b2a0f5a99363d63441322d72b5fe68d9766b"
    expected_tag_oid = "800fc1caa11d538cebb4a27ac55c897471ee03a8"
    dot_git = REPO_ROOT / ".git"
    if dot_git.exists():
        if shutil.which("git") is not None:
            tag_proc = subprocess.run(
                ["git", "rev-parse", "--verify", "entrega-academica-2026-10-24^{commit}"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            assert tag_proc.returncode == 0
            assert tag_proc.stdout.strip() == expected_commit
        else:
            git_dir = dot_git
            if git_dir.is_file():
                gitdir_line = git_dir.read_text(encoding="utf-8").strip()
                assert gitdir_line.startswith("gitdir:")
                raw_gd = Path(gitdir_line.split(":", 1)[1].strip())
                git_dir = raw_gd if raw_gd.is_absolute() else (REPO_ROOT / raw_gd).resolve()
            loose_tag = git_dir / "refs" / "tags" / "entrega-academica-2026-10-24"
            if loose_tag.is_file():
                ref_oid = loose_tag.read_text(encoding="utf-8").strip()
                if ref_oid != expected_commit:
                    obj_path = git_dir / "objects" / ref_oid[:2] / ref_oid[2:]
                    if obj_path.is_file():
                        raw_obj = zlib.decompress(obj_path.read_bytes())
                        header, _, body = raw_obj.partition(b"\x00")
                        assert header.startswith(b"tag "), f"Unexpected loose git object header: {header!r}"
                        obj_match = re.search(rb"^object ([0-9a-f]{40})$", body, re.MULTILINE)
                        assert obj_match is not None and obj_match.group(1).decode("ascii") == expected_commit
                    else:
                        assert ref_oid == expected_tag_oid, (
                            f"Loose tag entrega-academica-2026-10-24 points to {ref_oid!r}"
                        )
            else:
                packed_refs = git_dir / "packed-refs"
                assert packed_refs.is_file(), "Expected .git/packed-refs for entrega-academica-2026-10-24"
                lines = packed_refs.read_text(encoding="utf-8").splitlines()
                tag_oid: str | None = None
                peeled_oid: str | None = None
                for idx, line in enumerate(lines):
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == "refs/tags/entrega-academica-2026-10-24":
                        tag_oid = parts[0]
                        if idx + 1 < len(lines) and lines[idx + 1].startswith("^"):
                            peeled_oid = lines[idx + 1][1:].strip()
                        break
                assert tag_oid is not None, "Missing refs/tags/entrega-academica-2026-10-24 in .git/packed-refs"
                if peeled_oid is not None:
                    assert peeled_oid == expected_commit
                    assert tag_oid == expected_tag_oid
                else:
                    obj_path = git_dir / "objects" / tag_oid[:2] / tag_oid[2:]
                    if obj_path.is_file():
                        raw_obj = zlib.decompress(obj_path.read_bytes())
                        header, _, body = raw_obj.partition(b"\x00")
                        assert header.startswith(b"tag "), f"Unexpected git tag object header: {header!r}"
                        obj_match = re.search(rb"^object ([0-9a-f]{40})$", body, re.MULTILINE)
                        assert obj_match is not None and obj_match.group(1).decode("ascii") == expected_commit
                    else:
                        assert tag_oid in (expected_commit, expected_tag_oid)


# ---------------------------------------------------------------------------
# Scenario 7: Full-Stack Postgres Parity (h-* -> v2-* -> Catalog/Rec -> Rollback)
# ---------------------------------------------------------------------------


@pytest.mark.tier4
@pytest.mark.requires_postgres
def test_t4_s7_postgres_v2_release_events_and_h_rollback(
    postgres_serving_env: Any,
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
) -> None:
    two_stage_cls = _import_two_stage_state()
    client, registry, connection, _schema, tmp_path = postgres_serving_env
    v2_dir, v2_id = synthetic_v2_release
    h_id = verify_history_release(synthetic_h_release).release_id

    input_root = tmp_path / "input"
    shutil.copytree(synthetic_h_release, input_root / "rel_h")
    shutil.copytree(v2_dir, input_root / "rel_v2")
    headers = {"Authorization": "Bearer local-test-token"}

    assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"}).status_code == 200
    assert client.post("/admin/releases/activate", headers=headers, json={"release_id": h_id}).status_code == 200
    assert isinstance(registry.snapshot(), HistoryState)

    assert client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"}).status_code == 200
    assert client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200
    assert isinstance(registry.snapshot(), two_stage_cls)

    with connection() as conn:
        ids = {row[0] for row in conn.execute("SELECT release_id FROM serving_releases").fetchall()}
        active_id, prev_id = conn.execute("SELECT active_id, previous_id FROM serving_pointer WHERE singleton").fetchone()
    assert {h_id, v2_id}.issubset(ids)
    assert active_id == v2_id
    assert prev_id == h_id

    cat = client.get("/catalog", params={"q": "Dress", "index_group_name": "Ladieswear"})
    assert cat.status_code == 200
    assert cat.json()["release_id"] == v2_id
    assert all(it["index_group_name"] == "Ladieswear" for it in cat.json()["items"])

    for payload in (
        {"customer_id": "cust_0000", "k": 6, "mode": "balanced"},
        {"customer_id": "cust_0000", "k": 6, "mode": "discovery"},
        {
            "customer_id": "anon-pg-s7",
            "k": 6,
            "mode": "balanced",
            "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
        },
    ):
        rec = client.post("/recommend", json=payload)
        assert rec.status_code == 200
        assert rec.json()["release_id"] == v2_id
        assert rec.json()["strategy"] == "two_stage_lgbm_v2"

    # Real Postgres event stream lifecycle while v2_id is active
    from hm_recommender.events.aggregate import initialize_stream

    dataset_dir = v2_dir.parent / "_dataset_v2"
    assert (dataset_dir / "manifest.json").is_file(), f"Expected dataset dir at {dataset_dir}"
    sid = str(uuid4())
    init_meta = initialize_stream(dataset_dir, v2_dir, sid, mode="synthetic", connection=connection)
    assert init_meta["release_id"] == v2_id
    assert init_meta["revision"] == 1
    assert init_meta["processed_events"] == 0

    cutoff = date(2020, 9, 9)
    ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
    for aid, kind in (("0000000018", "view"), ("0000000024", "click"), ("0000000028", "purchase")):
        ev_res = client.post(
            "/events",
            json={
                "event_id": str(uuid4()),
                "stream_id": sid,
                "release_id": v2_id,
                "customer_id": "anon-pg-s7",
                "article_id": aid,
                "kind": kind,
                "origin": "synthetic",
                "event_time": ev_time,
            },
        )
        assert ev_res.status_code == 201

    ref_res = client.post(
        f"/admin/streams/{sid}/refresh",
        headers=headers,
        json={"data_as_of": cutoff.isoformat()},
    )
    assert ref_res.status_code == 200
    ref_body = ref_res.json()
    assert ref_body["processed_events"] == 3
    assert ref_body["revision"] == 2

    bal_stream = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "anon-pg-s7", "k": 12, "mode": "balanced"},
    )
    assert bal_stream.status_code == 200
    bal_body = bal_stream.json()
    assert bal_body["release_id"] == v2_id
    assert bal_body["strategy"] == "two_stage_lgbm_v2"
    assert bal_body["fallback"] is False
    assert bal_body["items"]
    assert all(_TEN_DIGIT_RE.fullmatch(it["article_id"]) for it in bal_body["items"])

    disc_stream = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "anon-pg-s7", "k": 12, "mode": "discovery"},
    )
    assert disc_stream.status_code == 200
    disc_body = disc_stream.json()
    assert disc_body["release_id"] == v2_id
    assert disc_body["strategy"] == "two_stage_lgbm_v2"
    assert disc_body["fallback"] is False
    assert disc_body["items"]
    disc_ids = [it["article_id"] for it in disc_body["items"]]
    assert "0000000028" not in disc_ids
    catalog_by_id = {row["article_id"]: row for row in pl.read_parquet(v2_dir / "catalog.parquet").to_dicts()}
    disc_pcodes = [catalog_by_id[aid]["product_code"] for aid in disc_ids]
    assert len(disc_pcodes) == len(set(disc_pcodes))

    rb = client.post("/admin/releases/rollback", headers=headers)
    assert rb.status_code == 200
    assert rb.json()["release_id"] == h_id
    with connection() as conn:
        active_after, _ = conn.execute("SELECT active_id, previous_id FROM serving_pointer WHERE singleton").fetchone()
    assert active_after == h_id
    h_rec = client.post("/recommend", json={"customer_id": "cust_0000", "k": 6})
    assert h_rec.status_code == 200
    assert h_rec.json()["release_id"] == h_id
    assert h_rec.json()["strategy"] == "recent_then_popular"

    stale_ev = client.post(
        "/events",
        json={
            "event_id": str(uuid4()),
            "stream_id": sid,
            "release_id": v2_id,
            "customer_id": "anon-pg-s7",
            "article_id": "0000000024",
            "kind": "view",
            "origin": "synthetic",
            "event_time": ev_time,
        },
    )
    assert stale_ev.status_code == 409

    stale_stream_rec = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "anon-pg-s7", "k": 6, "mode": "balanced"},
    )
    assert stale_stream_rec.status_code == 409
