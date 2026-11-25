"""Tier 2 boundary, corner-case, and error-handling E2E tests across all 7 features.

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
import subprocess
import sys
import time as time_mod
from typing import Any
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
import httpx
import polars as pl
import pytest

from hm_recommender.api import auth
from hm_recommender.api.app import create_app
from hm_recommender.api.schemas import ActivateRequest
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.quality.contracts import digest, load_config
from hm_recommender.releases.package import release_identity, verify_history_release
from hm_recommender.ui.images import PLACEHOLDER, ImageCache
from tests.e2e.harness import (
    MIGRATIONS_DIR,
    REPO_ROOT,
    FakeEventRegistry,
    InMemoryApiAdapter,
    build_synthetic_dataset,
    build_synthetic_image_zip,
    build_synthetic_v2_release,
    ui_apptest_session,
    visible_text,
)

# ---------------------------------------------------------------------------
# Local Opaque-Box UI Helpers (zero top-level v2 imports)
# ---------------------------------------------------------------------------


def _find_control(
    app: Any,
    kinds: tuple[str, ...],
    *,
    label_re: str | None = None,
    key_re: str | None = None,
    option_re: str | None = None,
    exclude_key_re: str | None = None,
    exclude_label_re: str | None = None,
) -> Any | None:
    candidates: list[tuple[Any, str, str, list[str]]] = []
    for kind in kinds:
        try:
            widgets = getattr(app, kind)
        except Exception:
            continue
        for w in widgets:
            label_val = str(getattr(w, "label", "") or "")
            key_val = str(getattr(w, "key", "") or "")
            if exclude_key_re and re.search(exclude_key_re, key_val, re.I):
                continue
            if exclude_label_re and re.search(exclude_label_re, label_val, re.I):
                continue
            options_val = [str(o) for o in (getattr(w, "options", None) or [])]
            candidates.append((w, label_val, key_val, options_val))

    if key_re:
        for w, _label_val, key_val, _options_val in candidates:
            if re.search(key_re, key_val, re.I):
                return w
    if label_re:
        for w, label_val, _key_val, _options_val in candidates:
            if re.search(label_re, label_val, re.I):
                return w
    if option_re:
        for w, _label_val, _key_val, options_val in candidates:
            if any(re.search(option_re, opt, re.I) for opt in options_val):
                return w
    return None


def _find_buttons(
    app: Any,
    *,
    label_re: str | None = None,
    key_re: str | None = None,
) -> list[Any]:
    matches: list[Any] = []
    for btn in getattr(app, "button", []):
        label_val = str(getattr(btn, "label", "") or "")
        key_val = str(getattr(btn, "key", "") or "")
        if label_re and re.search(label_re, label_val, re.I):
            matches.append(btn)
        elif key_re and re.search(key_re, key_val, re.I):
            matches.append(btn)
    return matches


def _select_option_matching(ctrl: Any, pattern: str) -> str | None:
    options = list(getattr(ctrl, "options", []) or [])
    for opt in options:
        if re.search(pattern, str(opt), re.I):
            if hasattr(ctrl, "select"):
                ctrl.select(opt)
            elif hasattr(ctrl, "set_value"):
                ctrl.set_value(opt)
            return str(opt)
    return None


def _prepare_and_activate_h(
    fake_registry: FakeEventRegistry,
    h_release_dir: Path,
    tmp_path: Path,
    *,
    admin_token: str = "local-test-token",
) -> tuple[TestClient, str]:
    input_root = tmp_path / f"input_{uuid4().hex[:8]}"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(h_release_dir, input_root / "rel_h")
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_token}"}
    prep = client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"})
    assert prep.status_code == 200
    rid = prep.json()["release_id"]
    act = client.post("/admin/releases/activate", headers=headers, json={"release_id": rid})
    assert act.status_code == 200
    return client, rid


# ---------------------------------------------------------------------------
# B1. F_PKG — Package Boundaries & Errors (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier2
def test_t2_pkg_tampered_sha256_rejected(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "tamper_input"
    input_root.mkdir(parents=True, exist_ok=True)

    tampered_h = input_root / "tampered_h"
    shutil.copytree(synthetic_h_release, tampered_h)
    cat_h = tampered_h / "catalog.parquet"
    raw_h = bytearray(cat_h.read_bytes())
    raw_h[-1] ^= 0xFF
    cat_h.write_bytes(bytes(raw_h))

    with pytest.raises(ValueError):
        verify_history_release(tampered_h)

    tampered_h_json = input_root / "tampered_h_json"
    shutil.copytree(synthetic_h_release, tampered_h_json)
    rules_h = tampered_h_json / "rules.json"
    rules_h.write_text(rules_h.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        verify_history_release(tampered_h_json)
    with pytest.raises(ValueError):
        fake_registry.prepare_release(tampered_h_json)

    app = create_app(fake_registry, admin_token="adm-tamper", input_root=str(input_root))
    with TestClient(app) as client:
        res = client.post(
            "/admin/releases/prepare",
            headers={"Authorization": "Bearer adm-tamper"},
            json={"directory": "tampered_h_json"},
        )
        assert res.status_code == 400

    v2_dir, _ = synthetic_v2_release
    tampered_v2 = tmp_path / "tampered_v2"
    shutil.copytree(v2_dir, tampered_v2)
    model_v2 = tampered_v2 / "model.txt"
    model_v2.write_text(model_v2.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    manifest_v2 = json.loads((tampered_v2 / "manifest.json").read_text(encoding="utf-8"))
    assert sha256(model_v2) != manifest_v2["files"]["model"]["sha256"]

    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    with pytest.raises(ValueError):
        v2_pkg.verify_v2_release(tampered_v2)


@pytest.mark.tier2
def test_t2_pkg_invalid_release_ids_rejected(
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    bad_ids = [
        "v1-" + ("a" * 64),
        "v3-" + ("a" * 64),
        "h-" + ("A" * 64),
        "v2-" + ("A" * 64),
        "h-" + ("a" * 63),
        "h-" + ("a" * 65),
        "v2-" + ("g" * 64),
        "../" + ("a" * 64),
        "",
    ]
    for bad in bad_ids:
        with pytest.raises(ValueError, match="Invalid release ID"):
            fake_registry._directory(bad)

    assert fake_registry._directory("h-" + ("a" * 64)).name == "h-" + ("a" * 64)
    assert fake_registry._directory("v2-" + ("b" * 64)).name == "v2-" + ("b" * 64)

    app = create_app(fake_registry, admin_token="adm-rid", input_root=str(tmp_path))
    headers = {"Authorization": "Bearer adm-rid"}
    with TestClient(app) as client:
        for bad in bad_ids:
            res = client.post("/admin/releases/activate", headers=headers, json={"release_id": bad})
            assert res.status_code == 422

        assert ActivateRequest(release_id="v2-" + ("b" * 64)).release_id == "v2-" + ("b" * 64)
        res_v2 = client.post(
            "/admin/releases/activate",
            headers=headers,
            json={"release_id": "v2-" + ("b" * 64)},
        )
        assert res_v2.status_code == 400


@pytest.mark.tier2
def test_t2_pkg_missing_extra_or_symlink_files_rejected(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "mut_input"
    input_root.mkdir(parents=True, exist_ok=True)
    app = create_app(fake_registry, admin_token="adm-mut", input_root=str(input_root))
    headers = {"Authorization": "Bearer adm-mut"}

    # Missing file in h-*
    h_missing = input_root / "h_missing"
    shutil.copytree(synthetic_h_release, h_missing)
    (h_missing / "rules.json").unlink()
    with pytest.raises(ValueError):
        verify_history_release(h_missing)

    # Extra stray file in h-*
    h_extra = input_root / "h_extra"
    shutil.copytree(synthetic_h_release, h_extra)
    (h_extra / "stray.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_history_release(h_extra)

    # Symlink inside h-*
    h_symlink = input_root / "h_symlink"
    shutil.copytree(synthetic_h_release, h_symlink)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (h_symlink / "stray_link.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        verify_history_release(h_symlink)

    with TestClient(app) as client:
        for dirname in ("h_missing", "h_extra", "h_symlink"):
            res = client.post("/admin/releases/prepare", headers=headers, json={"directory": dirname})
            assert res.status_code == 400

    # v2-* mutations
    v2_dir, _ = synthetic_v2_release
    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    for mut_name, fn in (
        ("v2_missing", lambda d: (d / "model.txt").unlink()),
        ("v2_extra", lambda d: (d / "stray.txt").write_text("x", encoding="utf-8")),
        ("v2_symlink", lambda d: (d / "stray_link.txt").symlink_to(outside)),
    ):
        v2_mut = tmp_path / mut_name
        shutil.copytree(v2_dir, v2_mut)
        fn(v2_mut)
        with pytest.raises(ValueError):
            v2_pkg.verify_v2_release(v2_mut)


@pytest.mark.tier2
def test_t2_pkg_admin_auth_traversal_and_lifecycle_errors(
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "admin_input"
    input_root.mkdir(parents=True, exist_ok=True)
    app = create_app(fake_registry, admin_token="adm-secret-2026", input_root=str(input_root))
    good_headers = {"Authorization": "Bearer adm-secret-2026"}
    bad_headers = {"Authorization": "Bearer wrong-secret"}

    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 503

        for hdr in ({}, bad_headers):
            assert client.post("/admin/releases/prepare", headers=hdr, json={"directory": "x"}).status_code == 403
            assert (
                client.post(
                    "/admin/releases/activate",
                    headers=hdr,
                    json={"release_id": "h-" + ("a" * 64)},
                ).status_code
                == 403
            )
            assert client.post("/admin/releases/rollback", headers=hdr).status_code == 403
            assert (
                client.post(
                    f"/admin/streams/{uuid4()}/refresh",
                    headers=hdr,
                    json={"data_as_of": "2020-09-09"},
                ).status_code
                == 403
            )

        for bad_dir in ("/etc", "../outside"):
            res = client.post("/admin/releases/prepare", headers=good_headers, json={"directory": bad_dir})
            assert res.status_code == 400

        unprep = client.post(
            "/admin/releases/activate",
            headers=good_headers,
            json={"release_id": "h-" + ("0" * 64)},
        )
        assert unprep.status_code == 400

        rb = client.post("/admin/releases/rollback", headers=good_headers)
        assert rb.status_code == 400


@pytest.mark.tier2
def test_t2_pkg_temporal_leakage_and_row_count_mismatch_rejected(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    tmp_path: Path,
) -> None:
    # 1. Row count mismatch in h-* (update sha256/size_bytes + release_id, keep stale rows)
    h_row_mismatch = tmp_path / "h_row_mismatch"
    shutil.copytree(synthetic_h_release, h_row_mismatch)
    cat_df = pl.read_parquet(h_row_mismatch / "catalog.parquet")
    cat_df.head(5).write_parquet(h_row_mismatch / "catalog.parquet", compression="zstd", row_group_size=65536)
    man_h = json.loads((h_row_mismatch / "manifest.json").read_text(encoding="utf-8"))
    man_h["files"]["catalog"]["sha256"] = sha256(h_row_mismatch / "catalog.parquet")
    man_h["files"]["catalog"]["size_bytes"] = (h_row_mismatch / "catalog.parquet").stat().st_size
    man_h["release_id"] = release_identity(man_h)
    atomic_json(h_row_mismatch / "manifest.json", man_h)
    with pytest.raises(ValueError, match="row count mismatch"):
        verify_history_release(h_row_mismatch)

    # 2. Temporal leakage (first_seen == data_as_of) in h-*
    h_leak = tmp_path / "h_leak"
    shutil.copytree(synthetic_h_release, h_leak)
    man_leak = json.loads((h_leak / "manifest.json").read_text(encoding="utf-8"))
    cutoff = date.fromisoformat(man_leak["data_as_of"])
    leaked_cat = pl.read_parquet(h_leak / "catalog.parquet").with_columns(pl.lit(cutoff).alias("first_seen"))
    leaked_cat.write_parquet(h_leak / "catalog.parquet", compression="zstd", row_group_size=65536)
    man_leak["files"]["catalog"]["sha256"] = sha256(h_leak / "catalog.parquet")
    man_leak["files"]["catalog"]["size_bytes"] = (h_leak / "catalog.parquet").stat().st_size
    man_leak["release_id"] = release_identity(man_leak)
    atomic_json(h_leak / "manifest.json", man_leak)
    with pytest.raises(ValueError, match="timestamps"):
        verify_history_release(h_leak)

    # 3. v2-* row-count mismatch and temporal leakage checks via verify_v2_release
    v2_dir, _ = synthetic_v2_release
    v2_pkg = importlib.import_module("hm_recommender.v2.package")

    def _v2_release_identity(payload: dict[str, Any]) -> str:
        return "v2-" + digest({k: v for k, v in payload.items() if k != "release_id"})

    # 3a. Row count mismatch in v2-* (truncate catalog.parquet, update sha256/size_bytes/release_id, keep stale rows)
    v2_row_mismatch = tmp_path / "v2_row_mismatch"
    shutil.copytree(v2_dir, v2_row_mismatch)
    v2_cat_df = pl.read_parquet(v2_row_mismatch / "catalog.parquet")
    v2_cat_df.head(5).write_parquet(v2_row_mismatch / "catalog.parquet", compression="zstd", row_group_size=65536)
    man_v2_rows = json.loads((v2_row_mismatch / "manifest.json").read_text(encoding="utf-8"))
    man_v2_rows["files"]["catalog"]["sha256"] = sha256(v2_row_mismatch / "catalog.parquet")
    man_v2_rows["files"]["catalog"]["size_bytes"] = (v2_row_mismatch / "catalog.parquet").stat().st_size
    man_v2_rows["release_id"] = _v2_release_identity(man_v2_rows)
    atomic_json(v2_row_mismatch / "manifest.json", man_v2_rows)
    with pytest.raises(ValueError, match=r"(?i)row"):
        v2_pkg.verify_v2_release(v2_row_mismatch)

    # 3b. Temporal leakage (first_seen == data_as_of) in v2-* with valid sha256/size_bytes/release_id
    v2_leak = tmp_path / "v2_leak"
    shutil.copytree(v2_dir, v2_leak)
    v2_man = json.loads((v2_leak / "manifest.json").read_text(encoding="utf-8"))
    v2_cutoff = date.fromisoformat(v2_man["data_as_of"])
    v2_cat = pl.read_parquet(v2_leak / "catalog.parquet").with_columns(pl.lit(v2_cutoff).alias("first_seen"))
    v2_cat.write_parquet(v2_leak / "catalog.parquet", compression="zstd", row_group_size=65536)
    v2_man["files"]["catalog"]["sha256"] = sha256(v2_leak / "catalog.parquet")
    v2_man["files"]["catalog"]["size_bytes"] = (v2_leak / "catalog.parquet").stat().st_size
    v2_man["release_id"] = _v2_release_identity(v2_man)
    atomic_json(v2_leak / "manifest.json", v2_man)
    with pytest.raises(ValueError, match=r"(?i)timestamp|cutoff|leak|data_as_of|first_seen|temporal"):
        v2_pkg.verify_v2_release(v2_leak)

    # 3c. Temporal leakage in histories.parquet (last_purchase == data_as_of) with valid sha256/size_bytes/release_id
    v2_leak_hist = tmp_path / "v2_leak_hist"
    shutil.copytree(v2_dir, v2_leak_hist)
    v2_hist_man = json.loads((v2_leak_hist / "manifest.json").read_text(encoding="utf-8"))
    v2_hist_cutoff = date.fromisoformat(v2_hist_man["data_as_of"])
    v2_hist_raw = pl.read_parquet(v2_leak_hist / "histories.parquet")
    cnt_col = "count_180" if "count_180" in v2_hist_raw.columns else "frequency"
    v2_hist = v2_hist_raw.with_columns(pl.lit(v2_hist_cutoff).alias("last_purchase")).sort(
        ["customer_id", "last_purchase", cnt_col, "article_id"],
        descending=[False, True, True, False],
    )
    v2_hist.write_parquet(v2_leak_hist / "histories.parquet", compression="zstd", row_group_size=65536)
    v2_hist_man["files"]["histories"]["sha256"] = sha256(v2_leak_hist / "histories.parquet")
    v2_hist_man["files"]["histories"]["size_bytes"] = (v2_leak_hist / "histories.parquet").stat().st_size
    v2_hist_man["release_id"] = _v2_release_identity(v2_hist_man)
    atomic_json(v2_leak_hist / "manifest.json", v2_hist_man)
    with pytest.raises(ValueError, match=r"(?i)timestamp|cutoff|leak|data_as_of|last_purchase|window|temporal"):
        v2_pkg.verify_v2_release(v2_leak_hist)


# ---------------------------------------------------------------------------
# B2. F_REC — Recommendation Boundaries & Errors (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier2
def test_t2_rec_k_and_customer_id_validation_boundaries(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    http_client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            r1 = client.post("/recommend", json={"customer_id": "cust_0000", "k": 1})
            assert r1.status_code == 200 and len(r1.json()["items"]) == 1

            r12 = client.post("/recommend", json={"customer_id": "cust_0000", "k": 12})
            assert r12.status_code == 200 and len(r12.json()["items"]) <= 12

            r_max_id = client.post("/recommend", json={"customer_id": "x" * 128, "k": 1})
            assert r_max_id.status_code == 200

            for bad_payload in (
                {"customer_id": "cust_0000", "k": 0},
                {"customer_id": "cust_0000", "k": 13},
                {"customer_id": "cust_0000", "k": -1},
                {"customer_id": "cust_0000", "k": "5"},
                {"customer_id": "cust_0000", "k": True},
                {"customer_id": "", "k": 5},
                {"customer_id": "a b", "k": 5},
                {"customer_id": "x" * 129, "k": 5},
                {"customer_id": "cust_0000", "k": 5, "extra_field": 1},
            ):
                bad = client.post("/recommend", json=bad_payload)
                assert bad.status_code == 422, f"Expected 422 for {bad_payload!r}, got {bad.status_code}"


@pytest.mark.tier2
def test_t2_rec_invalid_mode_preferences_and_empty_filter_match(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    synthetic_h_release: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    client = adapter.client()

    for bad in (
        {"customer_id": "cust_0000", "k": 5, "mode": "exploit"},
        {"customer_id": "cust_0000", "k": 5, "preferences": "Menswear"},
        {"customer_id": "cust_0000", "k": 5, "preferences": {"size": "M"}},
    ):
        res = client.post("/recommend", json=bad)
        assert res.status_code == 422

    empty_match = client.post(
        "/recommend",
        json={"customer_id": "cust_0000", "k": 5, "filters": {"colour_group_name": "NeonInvisible"}},
    )
    assert empty_match.status_code == 200
    assert empty_match.json()["items"] == []

    # Unconditional real create_app validation with active v2-* release (mode & preferences)
    rec_v2 = importlib.import_module("hm_recommender.api.recommend_v2")
    assert hasattr(rec_v2, "TwoStageState")
    schemas_mod = importlib.import_module("hm_recommender.api.schemas")
    assert "mode" in schemas_mod.RecommendationRequest.model_fields
    assert "preferences" in schemas_mod.RecommendationRequest.model_fields

    v2_input = tmp_path / "v2_invalid_input"
    v2_input.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_dir, v2_input / "rel_v2")
    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(v2_input))
    headers = {"Authorization": "Bearer local-test-token"}
    with TestClient(app) as http_client:
        prep = http_client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"})
        assert prep.status_code == 200
        v2_rid = prep.json()["release_id"]
        act = http_client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_rid})
        assert act.status_code == 200

        for bad_real in (
            {"customer_id": "cust_0000", "k": 5, "mode": "exploit"},
            {"customer_id": "cust_0000", "k": 5, "preferences": "Menswear"},
            {"customer_id": "cust_0000", "k": 5, "preferences": {"size": "M"}},
        ):
            assert http_client.post("/recommend", json=bad_real).status_code == 422

        res_empty = http_client.post(
            "/recommend",
            json={"customer_id": "cust_0000", "k": 5, "filters": {"colour_group_name": "NeonInvisible"}},
        )
        assert res_empty.status_code == 200 and res_empty.json()["items"] == []


@pytest.mark.tier2
def test_t2_rec_stream_and_event_conflict_error_boundaries(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, v2_id = synthetic_v2_release
    h_id = verify_history_release(synthetic_h_release).release_id
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    client = adapter.client()
    streams = client.get("/streams").json()["items"]
    sid = streams[0]["stream_id"]
    cutoff = date.fromisoformat(streams[0]["data_as_of"])
    ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()

    # Unknown and malformed stream_id -> 404
    assert client.post(f"/streams/{uuid4()}/recommend", json={"customer_id": "cust_0000", "k": 5}).status_code == 404
    assert client.post("/streams/not-a-uuid/recommend", json={"customer_id": "cust_0000", "k": 5}).status_code == 404

    # Stale stream belonging to another release -> 409
    stale_sid = str(uuid4())
    stale_stream = adapter.store.streams[sid]
    adapter.store.streams[stale_sid] = type(stale_stream)(
        stream_id=stale_sid,
        release_id=h_id,
        mode=stale_stream.mode,
        data_as_of=stale_stream.data_as_of,
        revision=stale_stream.revision,
        received_before=stale_stream.received_before,
        published_at=stale_stream.published_at,
        processed_events=0,
        popularity=stale_stream.popularity,
        live_histories=stale_stream.live_histories,
        live_purchases=stale_stream.live_purchases,
        live_weights=stale_stream.live_weights,
        live_events=stale_stream.live_events,
    )
    assert client.post(f"/streams/{stale_sid}/recommend", json={"customer_id": "cust_0000", "k": 5}).status_code == 409

    # Wrong release_id / origin -> 409
    base_ev = {
        "event_id": str(uuid4()),
        "stream_id": sid,
        "release_id": v2_id,
        "customer_id": "cust_0007",
        "article_id": "0000000005",
        "kind": "click",
        "origin": "synthetic",
        "event_time": ev_time,
    }
    assert client.post("/events", json={**base_ev, "release_id": "v2-" + ("0" * 64)}).status_code == 409
    assert client.post("/events", json={**base_ev, "origin": "simulation"}).status_code == 409

    # Mutated replay of same event_id -> 409
    assert client.post("/events", json=base_ev).status_code == 201
    assert client.post("/events", json={**base_ev, "article_id": "0000000006"}).status_code == 409

    # Unknown article_id -> 422; invalid kind -> 422
    assert client.post("/events", json={**base_ev, "event_id": str(uuid4()), "article_id": "9999999999"}).status_code == 422
    assert client.post("/events", json={**base_ev, "event_id": str(uuid4()), "kind": "share"}).status_code == 422


@pytest.mark.tier2
def test_t2_rec_refresh_auth_clock_and_post_cutoff_isolation(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, v2_id = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
        admin_token="local-test-token",
    )
    client = adapter.client()
    streams = client.get("/streams").json()["items"]
    sid = streams[0]["stream_id"]
    cutoff = date.fromisoformat(streams[0]["data_as_of"])
    good_headers = {"Authorization": "Bearer local-test-token"}

    # Wrong token -> 403
    assert (
        client.post(
            f"/admin/streams/{sid}/refresh",
            headers={"Authorization": "Bearer wrong-token"},
            json={"data_as_of": cutoff.isoformat()},
        ).status_code
        == 403
    )

    # Backwards clock -> 400
    earlier = (cutoff - timedelta(days=1)).isoformat()
    assert client.post(f"/admin/streams/{sid}/refresh", headers=good_headers, json={"data_as_of": earlier}).status_code == 400

    # Event at exact cutoff (event_time >= cutoff) must be ingested (201) but ignored on refresh
    at_cutoff = datetime.combine(cutoff, time(0, 0), tzinfo=timezone.utc).isoformat()
    ev = client.post(
        "/events",
        json={
            "event_id": str(uuid4()),
            "stream_id": sid,
            "release_id": v2_id,
            "customer_id": "cust_0007",
            "article_id": "0000000018",
            "kind": "purchase",
            "origin": "synthetic",
            "event_time": at_cutoff,
        },
    )
    assert ev.status_code == 201

    ref = client.post(f"/admin/streams/{sid}/refresh", headers=good_headers, json={"data_as_of": cutoff.isoformat()})
    assert ref.status_code == 200
    assert ref.json()["processed_events"] == 0

    rec = client.post(f"/streams/{sid}/recommend", json={"customer_id": "cust_0007", "k": 12})
    assert rec.status_code == 200
    assert rec.json()["fallback"] is True


@pytest.mark.tier2
def test_t2_rec_unready_503_and_future_only_buyer_cold_start(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    app = create_app(fake_registry, admin_token="adm-unready", input_root=str(tmp_path))
    with TestClient(app) as unready_client:
        assert unready_client.post("/recommend", json={"customer_id": "cust_0000", "k": 5}).status_code == 503

    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    res = adapter.client().post("/recommend", json={"customer_id": "cust_0003", "k": 12})
    assert res.status_code == 200
    body = res.json()
    assert body["fallback"] is True
    assert all(it["reason"] in {"popular_for_segment", "popular_7d"} for it in body["items"])


@pytest.mark.tier2
def test_t2_rec_discovery_returns_fewer_than_k_when_unique_pcodes_exhausted(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, v2_id = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
        admin_token="local-test-token",
    )
    client = adapter.client()
    streams = client.get("/streams").json()["items"]
    sid = streams[0]["stream_id"]
    cutoff = date.fromisoformat(streams[0]["data_as_of"])
    ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()

    # 40 articles = 20 product_codes (2 colorways each).
    # Purchase BOTH colorways of 12 distinct product_codes -> only 8 unique product_codes remain.
    purchased_ids: set[str] = set()
    for aid in sorted(adapter.catalog_by_id)[:24]:
        purchased_ids.add(aid)
        res = client.post(
            "/events",
            json={
                "event_id": str(uuid4()),
                "stream_id": sid,
                "release_id": v2_id,
                "customer_id": "cust_0007",
                "article_id": aid,
                "kind": "purchase",
                "origin": "synthetic",
                "event_time": ev_time,
            },
        )
        assert res.status_code == 201

    assert (
        client.post(
            f"/admin/streams/{sid}/refresh",
            headers={"Authorization": "Bearer local-test-token"},
            json={"data_as_of": cutoff.isoformat()},
        ).status_code
        == 200
    )

    disc = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
    )
    assert disc.status_code == 200
    disc_ids = [it["article_id"] for it in disc.json()["items"]]
    assert 1 <= len(disc_ids) <= 8
    assert purchased_ids.isdisjoint(disc_ids)
    pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in disc_ids]
    assert len(pcodes) == len(set(pcodes))


# ---------------------------------------------------------------------------
# B3. F_CAT — Catalog Boundaries & Errors (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier2
def test_t2_cat_pagination_bva(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    http_client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            r_min = client.get("/catalog", params={"offset": 0, "limit": 1})
            assert r_min.status_code == 200 and len(r_min.json()["items"]) == 1

            r_max = client.get("/catalog", params={"offset": 0, "limit": 48})
            assert r_max.status_code == 200 and len(r_max.json()["items"]) == 40

            r_high_off = client.get("/catalog", params={"offset": 10_000_000, "limit": 12})
            assert r_high_off.status_code == 200
            assert r_high_off.json()["items"] == []
            assert r_high_off.json()["total"] == 40

            for bad_params in (
                {"offset": 0, "limit": 0},
                {"offset": 0, "limit": 49},
                {"offset": 0, "limit": "abc"},
                {"offset": -1, "limit": 12},
                {"offset": 10_000_001, "limit": 12},
            ):
                assert client.get("/catalog", params=bad_params).status_code == 422


@pytest.mark.tier2
def test_t2_cat_search_q_empty_unmatched_and_regex_chars(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    http_client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            for empty_q in ("", "   "):
                res = client.get("/catalog", params={"q": empty_q})
                assert res.status_code == 200 and res.json()["total"] == 40

            for miss_q in ("xyzzynotfound", ".*+", "[broken(", "(a+)+"):
                res = client.get("/catalog", params={"q": miss_q})
                assert res.status_code == 200
                assert res.json()["total"] == 0
                assert res.json()["items"] == []


@pytest.mark.tier2
def test_t2_cat_unknown_index_group_and_empty_intersection(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    http_client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            for params in (
                {"index_group_name": "Spacewear"},
                {"q": "Dress", "index_group_name": "Menswear"},
            ):
                res = client.get("/catalog", params=params)
                assert res.status_code == 200
                body = res.json()
                assert body["total"] == 0
                assert body["items"] == []
                assert "release_id" in body and isinstance(body["filters"], dict)


@pytest.mark.tier2
def test_t2_cat_articles_details_boundaries(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    http_client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            assert client.post("/articles/details", json={"article_ids": ["0000000000"]}).status_code == 200
            ids_48 = [f"{i % 40:010d}" for i in range(48)]
            assert client.post("/articles/details", json={"article_ids": ids_48}).status_code == 200

            unknown = client.post("/articles/details", json={"article_ids": ["9999999999"]})
            assert unknown.status_code == 200 and unknown.json()["items"] == []

            for bad in (
                {"article_ids": []},
                {"article_ids": [f"{i:010d}" for i in range(49)]},
            ):
                assert client.post("/articles/details", json=bad).status_code == 422

        for bad_strict in (
            {"article_ids": ["abc"]},
            {"article_ids": ["0000000000"], "extra": 1},
        ):
            assert http_client.post("/articles/details", json=bad_strict).status_code == 422


@pytest.mark.tier2
def test_t2_cat_unready_service_503(
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    app = create_app(fake_registry, admin_token="adm-cat-503", input_root=str(tmp_path))
    with TestClient(app) as client:
        assert client.get("/catalog").status_code == 503
        assert client.post("/articles/details", json={"article_ids": ["0000000000"]}).status_code == 503


# ---------------------------------------------------------------------------
# B4. F_IMG — ImageCache & Image Endpoint Boundaries (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier2
def test_t2_img_max_bytes_and_symlink_dir_boundaries(tmp_path: Path) -> None:
    for bad_max in (0, -1, 512 * 1024**2 + 1):
        with pytest.raises(ValueError):
            ImageCache(None, tmp_path / f"dir_{bad_max}", max_bytes=bad_max)

    real_dir = tmp_path / "real_cache"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_cache"
    sym_dir.symlink_to(real_dir)
    with pytest.raises(ValueError):
        ImageCache(None, sym_dir, max_bytes=1024)

    c_min = ImageCache(None, tmp_path / "min_cache", max_bytes=1)
    assert c_min.max_bytes == 1
    c_max = ImageCache(None, tmp_path / "max_cache", max_bytes=512 * 1024**2)
    assert c_max.max_bytes == 512 * 1024**2


@pytest.mark.tier2
def test_t2_img_non_10_digit_and_path_traversal_ids(
    synthetic_image_zip: Path,
    tmp_path: Path,
) -> None:
    cache = ImageCache(synthetic_image_zip, tmp_path / "cache")
    for bad_id in (
        "000000001",
        "00000000001",
        "abcdefghij",
        "",
        "../0000000000",
        "000/0000000000",
    ):
        body, mime = cache.read(bad_id)
        assert mime == "image/svg+xml"
        assert body == PLACEHOLDER


@pytest.mark.tier2
def test_t2_img_corrupt_jpeg_bad_zip_missing_zip_and_encrypted(tmp_path: Path) -> None:
    corrupt_zip = build_synthetic_image_zip(
        tmp_path / "corrupt.zip",
        ["0000000000", "0000000001"],
        corrupt_ids=["0000000001"],
    )
    c1 = ImageCache(corrupt_zip, tmp_path / "c1")
    assert c1.read("0000000001") == (PLACEHOLDER, "image/svg+xml")

    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"NOT_A_ZIP_ARCHIVE_AT_ALL")
    c2 = ImageCache(bad_zip, tmp_path / "c2")
    assert c2.read("0000000000") == (PLACEHOLDER, "image/svg+xml")

    c3 = ImageCache(tmp_path / "missing.zip", tmp_path / "c3")
    assert c3.read("0000000000") == (PLACEHOLDER, "image/svg+xml")

    # Encrypted entry (flag_bits bit 0 set in both local and central headers)
    enc_zip = build_synthetic_image_zip(tmp_path / "enc.zip", ["0000000000"])
    raw = bytearray(enc_zip.read_bytes())
    lfh = raw.find(b"PK\x03\x04")
    cdh = raw.find(b"PK\x01\x02")
    assert lfh != -1 and cdh != -1
    raw[lfh + 6] |= 1
    raw[cdh + 8] |= 1
    enc_zip.write_bytes(bytes(raw))
    with zipfile.ZipFile(enc_zip) as zf:
        assert zf.getinfo("images/000/0000000000.jpg").flag_bits & 1
    c4 = ImageCache(enc_zip, tmp_path / "c4")
    assert c4.read("0000000000") == (PLACEHOLDER, "image/svg+xml")


@pytest.mark.tier2
def test_t2_img_oversized_member_and_symlink_cache_target(
    synthetic_image_zip: Path,
    tmp_path: Path,
) -> None:
    oversized_dir = tmp_path / "oversized_cache"
    c_small = ImageCache(synthetic_image_zip, oversized_dir, max_bytes=100)
    assert c_small.read("0000000000") == (PLACEHOLDER, "image/svg+xml")
    assert list(oversized_dir.glob("*.jpg")) == []

    sym_cache_dir = tmp_path / "sym_target_cache"
    sym_cache_dir.mkdir(parents=True, exist_ok=True)
    stat_zip = synthetic_image_zip.stat()
    key = hashlib.sha256(
        f"{synthetic_image_zip.resolve()}:{stat_zip.st_size}:{stat_zip.st_mtime_ns}:0000000000".encode()
    ).hexdigest()
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("do-not-overwrite", encoding="utf-8")
    (sym_cache_dir / f"{key}.jpg").symlink_to(sentinel)

    c_sym = ImageCache(synthetic_image_zip, sym_cache_dir, max_bytes=1024 * 1024)
    assert c_sym.read("0000000000") == (PLACEHOLDER, "image/svg+xml")
    assert sentinel.read_text(encoding="utf-8") == "do-not-overwrite"


@pytest.mark.tier2
def test_t2_img_http_unknown_article_404_and_unready_503(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    client = adapter.client()
    assert client.get("/articles/9999999999/image").status_code == 404
    assert client.get("/articles/000000001/image").status_code == 404

    unready_app = create_app(fake_registry, admin_token="adm-img", input_root=str(tmp_path))
    with TestClient(unready_app) as unready:
        assert unready.get("/articles/0000000000/image").status_code == 503


# ---------------------------------------------------------------------------
# B5. F_UI — Streamlit UI Boundaries & Error Handling (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier2
def test_t2_ui_empty_catalog_search_shows_graceful_empty_state(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    with ui_apptest_session(adapter, default_timeout=25) as app:
        assert not app.exception
        search_input = _find_control(
            app,
            ("text_input",),
            label_re=r"buscar|búsqueda|palabra|catálogo|search",
            key_re=r"search|query|catalog_q|^q$",
        )
        assert search_input is not None, "Expected catalog keyword search input in UI"
        search_input.input("zzznonexistent999").run()
        assert not app.exception
        rendered = visible_text(app)
        assert re.search(r"sin resultados|no encontramos|no hay prendas|ninguna prenda|vacío", rendered, re.I)


@pytest.mark.tier2
def test_t2_ui_corrupt_and_missing_images_render_placeholder_without_crash(
    synthetic_v2_release: tuple[Path, str],
    tmp_path: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    partial_corrupt_zip = build_synthetic_image_zip(
        tmp_path / "partial_corrupt.zip",
        [f"{i:010d}" for i in range(20)],
        corrupt_ids=["0000000000", "0000000001"],
    )
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=partial_corrupt_zip,
        image_cache_dir=tmp_path / "ui_img_cache",
    )
    with ui_apptest_session(adapter, default_timeout=25) as app:
        assert not app.exception
        assert _find_control(app, ("selectbox",), key_re=r"^profile$") is not None
        assert len(app.get("image")) > 0


@pytest.mark.tier2
def test_t2_ui_empty_streams_or_api_503_shows_graceful_error_banner(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )

    def _empty_streams(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/streams":
            return httpx.Response(200, json={"items": []})
        return adapter.handle(request)

    with ui_apptest_session(_empty_streams, default_timeout=25) as app_empty:
        assert not app_empty.exception
        assert re.search(r"preparando|vuelve en unos momentos|no disponible", visible_text(app_empty), re.I)

    def _api_503(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(503, json={"status": "unavailable"})
        return adapter.handle(request)

    with ui_apptest_session(_api_503, default_timeout=25) as app_down:
        assert not app_down.exception
        assert re.search(r"no pudimos cargar|recarga la página|disponible", visible_text(app_down), re.I)


@pytest.mark.tier2
def test_t2_ui_purchase_transient_503_retry_and_terminal_422_discard(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    fail_mode = {"status": 503}
    event_attempts = {"count": 0}

    def _flaky_events(request: httpx.Request) -> httpx.Response:
        if request.method.upper() == "POST" and request.url.path == "/events":
            event_attempts["count"] += 1
            if fail_mode["status"] == 503:
                fail_mode["status"] = 0
                return httpx.Response(503, json={"detail": "Transient DB blip"})
            if fail_mode["status"] == 422:
                return httpx.Response(422, json={"detail": "Invalid article"})
        return adapter.handle(request)

    with ui_apptest_session(_flaky_events, default_timeout=25) as app:
        assert not app.exception
        buy_btns = _find_buttons(app, label_re=r"comprar|simular compra", key_re=r"buy|purchase")
        assert buy_btns
        buy_btns[0].click().run()
        assert not app.exception

        retry_btn = _find_control(app, ("button",), key_re=r"^retry$", label_re=r"reintentar")
        assert retry_btn is not None and not retry_btn.disabled
        retry_btn.click().run()
        assert not app.exception
        assert any(e["kind"] == "purchase" for e in adapter.store.events_log)

        # Terminal 422 error disables retry and exposes discard button
        fail_mode["status"] = 422
        buy_btns_2 = _find_buttons(app, label_re=r"comprar|simular compra", key_re=r"buy|purchase")
        assert buy_btns_2
        buy_btns_2[0].click().run()
        assert not app.exception

        retry_btn_2 = _find_control(app, ("button",), key_re=r"^retry$", label_re=r"reintentar")
        assert retry_btn_2 is not None and retry_btn_2.disabled
        discard_btn = _find_control(app, ("button",), key_re=r"^discard_pending$", label_re=r"cerrar este intento")
        assert discard_btn is not None
        discard_btn.click().run()
        assert not app.exception
        assert _find_control(app, ("button",), key_re=r"^discard_pending$") is None


@pytest.mark.tier2
def test_t2_ui_discovery_mode_exhausted_or_zero_filter_matches_handled_cleanly(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )

    def _empty_discovery(request: httpx.Request) -> httpx.Response:
        if request.method.upper() == "POST" and request.url.path.endswith("/recommend"):
            body = json.loads(request.content.decode("utf-8"))
            if body.get("mode") == "discovery":
                return httpx.Response(
                    200,
                    json={
                        "request_id": str(uuid4()),
                        "release_id": adapter.state.release_id,
                        "strategy": "two_stage_lgbm_v2",
                        "features_timestamp": adapter.state.data_as_of.isoformat(),
                        "fallback": False,
                        "items": [],
                        "stream_id": adapter.store.default_stream_id,
                        "revision": 1,
                        "received_before": datetime.now(timezone.utc).isoformat(),
                        "published_at": datetime.now(timezone.utc).isoformat(),
                        "mode": "synthetic",
                    },
                )
        return adapter.handle(request)

    with ui_apptest_session(_empty_discovery, default_timeout=25) as app:
        assert not app.exception
        mode_ctrl = _find_control(
            app,
            ("radio", "segmented_control", "pills", "selectbox", "toggle"),
            label_re=r"modo|descubrimiento|equilibrado",
            key_re=r"^mode$|rec_mode|discovery",
            option_re=r"solo\s+descubrimiento|descubrimiento",
        )
        assert mode_ctrl is not None, "Expected mode control in UI"
        if hasattr(mode_ctrl, "options") and getattr(mode_ctrl, "options", None):
            _select_option_matching(mode_ctrl, r"solo\s+descubrimiento|descubrimiento")
        elif hasattr(mode_ctrl, "set_value"):
            mode_ctrl.set_value(True)
        app.run()
        assert not app.exception
        assert re.search(r"no encontramos|sin prendas|prueba otra", visible_text(app), re.I)


@pytest.mark.tier2
def test_t2_ui_switching_between_cold_start_and_demo_profile_resets_context(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    with ui_apptest_session(adapter, default_timeout=25) as app:
        assert not app.exception
        profile_sb = _find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None

        assert _select_option_matching(profile_sb, r"comprador frecuente") is not None
        app.run()
        assert not app.exception

        assert _select_option_matching(profile_sb, r"nuevo visitante") is not None
        app.run()
        assert not app.exception

        assert _select_option_matching(profile_sb, r"comprador frecuente") is not None
        app.run()
        assert not app.exception
        assert "cambiaron de posición" not in visible_text(app)


# ---------------------------------------------------------------------------
# B6. F_SEC — Security Portal & Gateway Boundaries (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier2
def test_t2_sec_rate_limiter_429_boundary_ipv6_and_spoofed_xff(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    gateway_config_factory({"alice": "StrongPass2026!"}, hash_mode="scrypt")
    app = create_app(fake_registry, admin_token="adm-rl", input_root=str(tmp_path))
    client = TestClient(app)

    for attempt in range(auth.MAX_LOGIN_ATTEMPTS):
        res = client.post(
            "/login",
            data={"username": "alice", "password": f"WrongPass{attempt}"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-For": "spoofed;drop, [2001:0db8::1]:8443",
            },
            follow_redirects=False,
        )
        assert res.status_code == 200

    # 9th attempt from canonicalized/unbracketed equivalent IPv6 even with valid password -> 429
    blocked = client.post(
        "/login",
        data={"username": "alice", "password": "StrongPass2026!"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": "2001:0db8:0000:0000:0000:0000:0000:0001",
        },
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    assert "Demasiados intentos" in blocked.text

    basic_valid = "Basic " + base64.b64encode(b"alice:StrongPass2026!").decode("ascii")
    blocked_basic = client.get(
        "/auth/verify",
        headers={"Authorization": basic_valid, "X-Forwarded-For": "[2001:db8::1]:9999"},
    )
    assert blocked_basic.status_code == 429

    # Unrelated IP still succeeds
    other_ip = client.post(
        "/login",
        data={"username": "alice", "password": "StrongPass2026!"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": "198.51.100.77",
        },
        follow_redirects=False,
    )
    assert other_ip.status_code == 303


@pytest.mark.tier2
def test_t2_sec_rate_limiter_max_tracked_ips_bounded_eviction_and_validation() -> None:
    assert auth.MAX_TRACKED_IPS == 10_000
    with pytest.raises(ValueError):
        auth.LoginRateLimiter(max_tracked_ips=0)

    limiter = auth.LoginRateLimiter()
    now = 1_000_000.0
    for i in range(auth.MAX_TRACKED_IPS + 100):
        limiter.record_failure(f"10.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255}", now=now)
    assert len(limiter._failures) == auth.MAX_TRACKED_IPS

    # Expired sweep precedes LRU eviction of live keys
    small = auth.LoginRateLimiter(max_attempts=3, window_seconds=60, max_tracked_ips=3)
    small.record_failure("192.0.2.1", now=100.0)  # will expire by t=200
    small.record_failure("192.0.2.2", now=180.0)
    small.record_failure("192.0.2.3", now=190.0)
    small.record_failure("192.0.2.4", now=200.0)
    assert set(small._failures.keys()) == {"192.0.2.2", "192.0.2.3", "192.0.2.4"}

    # With all live, oldest unused (192.0.2.2) is LRU-evicted
    small.is_blocked("192.0.2.3", now=205.0)  # touch 192.0.2.3? bucket has 1 failure -> moves to end
    small.record_failure("192.0.2.5", now=210.0)
    assert "192.0.2.2" not in small._failures
    assert "192.0.2.5" in small._failures


@pytest.mark.tier2
def test_t2_sec_oversized_login_body_and_credential_length_boundaries(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    gateway_config_factory({"alice": "BoundPass2026!"}, hash_mode="scrypt")
    app = create_app(fake_registry, admin_token="adm-body", input_root=str(tmp_path))
    client = TestClient(app)

    # Exact 4096 bytes vs 4097 bytes
    base_form = "username=alice&password=BoundPass2026!&pad="
    exact_body = (base_form + ("a" * (auth.MAX_LOGIN_BODY_BYTES - len(base_form)))).encode("ascii")
    assert len(exact_body) == auth.MAX_LOGIN_BODY_BYTES
    res_ok = client.post(
        "/login",
        content=exact_body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": "198.51.100.10"},
        follow_redirects=False,
    )
    assert res_ok.status_code == 303

    over_body = exact_body + b"z"
    assert len(over_body) == auth.MAX_LOGIN_BODY_BYTES + 1
    res_400 = client.post(
        "/login",
        content=over_body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": "198.51.100.11"},
        follow_redirects=False,
    )
    assert res_400.status_code == 400
    assert "Solicitud inválida" in res_400.text

    # Credential length boundaries
    max_u = "u" * 128
    max_p = "p" * 256
    hashes = {max_u: auth.hash_password(max_p), "u129": auth.hash_password("ok")}
    assert auth.verify_user_credentials(max_u, max_p, hashes) is True
    assert auth.verify_user_credentials("u" * 129, "ok", {"u" * 129: auth.hash_password("ok")}) is False
    assert auth.verify_user_credentials("u129", "p" * 257, {"u129": auth.hash_password("p" * 257)}) is False
    assert auth.verify_user_credentials("", "ok", hashes) is False
    assert auth.verify_user_credentials(max_u, "", hashes) is False


@pytest.mark.tier2
def test_t2_sec_malformed_scrypt_corrupt_missing_gateway_json_and_xss(
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with auth._HASH_CACHE_LOCK:
        auth._HASH_CACHE.clear()

    missing = tmp_path / "missing_gw.json"
    assert auth.load_user_hashes(missing) == {}

    corrupt = tmp_path / "corrupt_gw.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    assert auth.load_user_hashes(corrupt) == {}

    empty_hashes = tmp_path / "empty_gw.json"
    empty_hashes.write_text('{"password_hashes": {}}', encoding="utf-8")
    assert auth.load_user_hashes(empty_hashes) == {}

    # Malformed scrypt hashes fail closed
    salt_hex = "00" * 16
    dig_hex = "11" * 32
    bad_hashes = {
        "non_pow2": f"scrypt$n=1000$r=8$p=1${salt_hex}${dig_hex}",
        "huge_mem": f"scrypt$n=1048576$r=8$p=1${salt_hex}${dig_hex}",
        "short_salt": f"scrypt$n=16384$r=8$p=1$0011${dig_hex}",
        "bad_fields": "scrypt$n=16384$r=8",
    }
    bad_cfg = tmp_path / "bad_scrypt_gw.json"
    bad_cfg.write_text(json.dumps({"password_hashes": bad_hashes}), encoding="utf-8")
    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(bad_cfg))

    app = create_app(fake_registry, admin_token="adm-bad-scrypt", input_root=str(tmp_path))
    with TestClient(app) as client:
        for uname in bad_hashes:
            res = client.post(
                "/login",
                data={"username": uname, "password": "anything"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert res.status_code == 200
            assert "Usuario o contraseña incorrectos" in res.text

    rendered = auth.render_login_html('<script>alert("xss")</script>')
    assert "<script>alert" not in rendered
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in rendered


@pytest.mark.tier2
def test_t2_sec_tampered_expired_wrong_secret_cookies_fail_closed(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    gateway_config_factory({"alice": "CookieFailClosed2026!"}, hash_mode="scrypt")
    admin_secret = "real-admin-token"
    app = create_app(fake_registry, admin_token=admin_secret, input_root=str(tmp_path))
    client = TestClient(app)

    derived_secret = hmac.new(admin_secret.encode("utf-8"), b"hmrec:session-secret:v1", hashlib.sha256).digest()
    valid_tok = auth.create_session_token("alice", derived_secret, max_age=3600)
    payload_b64, sig = valid_tok.split(".", 1)
    tampered_tok = f"{payload_b64}.{'0' * len(sig)}"
    expired_tok = auth.create_session_token("alice", derived_secret, max_age=-10)
    wrong_secret_tok = auth.create_session_token("alice", b"wrong-secret-bytes", max_age=3600)

    for bad_tok in (tampered_tok, expired_tok, wrong_secret_tok, "malformed-cookie"):
        client.cookies.set(auth.COOKIE_NAME, bad_tok)
        assert client.get("/auth/verify", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 307
        assert client.get("/auth/verify", headers={"Accept": "application/json"}, follow_redirects=False).status_code == 401
        login_res = client.get("/login", follow_redirects=False)
        assert login_res.status_code == 200 and 'name="username"' in login_res.text


@pytest.mark.tier2
def test_t2_sec_no_hardcoded_hm_project_and_admin_path_traversal_rejected(
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    for rel_path in (
        REPO_ROOT / "src" / "hm_recommender" / "api" / "auth.py",
        REPO_ROOT / "src" / "hm_recommender" / "api" / "app.py",
        REPO_ROOT / "scripts" / "server_gateway.py",
    ):
        assert "/data/hm_project" not in rel_path.read_text(encoding="utf-8")

    input_root = tmp_path / "root"
    input_root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside_pkg"
    outside.mkdir(parents=True, exist_ok=True)
    (input_root / "sym_outside").symlink_to(outside)

    app = create_app(fake_registry, admin_token="adm-trav", input_root=str(input_root))
    headers = {"Authorization": "Bearer adm-trav"}
    with TestClient(app) as client:
        for bad_dir in ("/etc", "../outside_pkg", "sym_outside"):
            res = client.post("/admin/releases/prepare", headers=headers, json={"directory": bad_dir})
            assert res.status_code == 400
            assert res.json()["detail"] == "Invalid package directory"


# ---------------------------------------------------------------------------
# B7. F_QUAL — Quality, Docs Links, Temporal & Integrity Boundaries (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier2
def test_t2_qual_docs_links_detects_broken_and_ignores_fences_and_archives(tmp_path: Path) -> None:
    doc_root = tmp_path / "doc_repo"
    (doc_root / "docs" / "archive").mkdir(parents=True, exist_ok=True)
    (doc_root / "docs" / "target.md").write_text("# Target\n", encoding="utf-8")
    (doc_root / "README.md").write_text(
        "# Home\n"
        "[Valid](docs/target.md)\n"
        "[External](https://example.com)\n"
        "[Anchor](#sec)\n"
        "```markdown\n"
        "[IgnoredFence](nope.md)\n"
        "```\n",
        encoding="utf-8",
    )
    (doc_root / "docs" / "ok.md").write_text("[Broken](missing.md)\n", encoding="utf-8")
    (doc_root / "docs" / "archive" / "hist.md").write_text("[Old](old.md)\n", encoding="utf-8")

    script = REPO_ROOT / "scripts" / "check_docs_links.py"
    r_default = subprocess.run(
        [sys.executable, str(script), "--root", str(doc_root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r_default.returncode == 1
    d_default = json.loads(r_default.stdout)
    assert d_default["broken_count"] == 1
    assert d_default["broken_links"][0]["file"] == "docs/ok.md"

    r_arch = subprocess.run(
        [sys.executable, str(script), "--root", str(doc_root), "--include-archives", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r_arch.returncode == 1
    assert json.loads(r_arch.stdout)["broken_count"] == 2

    (doc_root / "docs" / "ok.md").write_text("[Fixed](target.md)\n", encoding="utf-8")
    r_fixed = subprocess.run(
        [sys.executable, str(script), "--root", str(doc_root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r_fixed.returncode == 0
    assert json.loads(r_fixed.stdout)["broken_count"] == 0


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


def _invoke_segment_or_summary_gate(
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


@pytest.mark.tier2
def test_t2_qual_quality_v2_cutoff_and_segment_threshold_boundaries() -> None:
    q2_path = REPO_ROOT / "configs" / "quality" / "quality-v2.json"
    assert q2_path.is_file(), "Expected configs/quality/quality-v2.json"
    q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    raw_test_cutoff = q2.get("test_cutoff") or (q2.get("descriptive_test_window") or {}).get("cutoff")
    assert raw_test_cutoff is not None
    test_cutoff = date.fromisoformat(str(raw_test_cutoff))
    assert test_cutoff == date(2020, 9, 16)
    raw_folds = q2.get("folds", {})
    if isinstance(raw_folds, list):
        if raw_folds and all(isinstance(item, dict) for item in raw_folds):
            folds_map = {
                str(item.get("fold_id") or item.get("id") or item.get("name")): (
                    item.get("cutoff") or item.get("date")
                )
                for item in raw_folds
            }
        elif len(raw_folds) == 3 and all(isinstance(item, str) for item in raw_folds):
            folds_map = {"V1": raw_folds[0], "V2": raw_folds[1], "V3": raw_folds[2]}
        else:
            folds_map = {}
    else:
        folds_map = {
            str(k): (v.get("cutoff") if isinstance(v, dict) else v)
            for k, v in dict(raw_folds).items()
        }
    for fold_name in ("V1", "V2", "V3"):
        fold_cutoff = date.fromisoformat(str(folds_map[fold_name]))
        assert fold_cutoff < test_cutoff
        assert fold_cutoff + timedelta(days=7) <= test_cutoff

    v2_eval = importlib.import_module("hm_recommender.v2.evaluate")
    selection = q2.get("selection") or q2.get("gates") or q2
    min_users = int(selection.get("min_segment_users", q2.get("min_segment_users")))
    raw_max_loss = selection.get(
        "max_segment_loss",
        selection.get("max_segment_relative_loss", q2.get("max_segment_loss", q2.get("max_segment_relative_loss"))),
    )
    assert raw_max_loss is not None
    max_loss = float(raw_max_loss)
    assert min_users == 1000 and max_loss == pytest.approx(0.02)

    compute_metrics = getattr(v2_eval, "compute_buyer_metrics", None)
    assert callable(compute_metrics), "Expected compute_buyer_metrics in hm_recommender.v2.evaluate"
    hit_res = compute_metrics(["0000000001"], {"0000000001"}, ks=(200, 300))
    miss_res = compute_metrics(["0000000999"], {"0000000001"}, ks=(200, 300))
    assert float(hit_res["map_at_12"]) > 0.0
    assert float(miss_res["map_at_12"]) == 0.0
    with pytest.raises(ValueError):
        compute_metrics(["0000000001"], set(), ks=(200, 300))

    gate_fn = None
    for attr in (
        "check_segment_regressions",
        "passes_segment_gates",
        "passes_quality_gates",
        "check_quality_gates",
        "evaluate_quality_gates",
        "is_eligible_candidate",
    ):
        cand = getattr(v2_eval, attr, None)
        if callable(cand):
            gate_fn = cand
            break
    assert callable(gate_fn), "Expected public segment/quality gate function in hm_recommender.v2.evaluate"

    assert _invoke_segment_or_summary_gate(gate_fn, users=999, rel_delta=-0.03) is True
    assert _invoke_segment_or_summary_gate(gate_fn, users=1000, rel_delta=-0.0199) is True
    assert _invoke_segment_or_summary_gate(gate_fn, users=1000, rel_delta=-0.02) is True
    assert _invoke_segment_or_summary_gate(gate_fn, users=1000, rel_delta=-0.0201) is False


@pytest.mark.tier2
def test_t2_qual_zero_temporal_leakage_at_exact_cutoff_boundary(
    tmp_path: Path,
    source_commit_env: str,
) -> None:
    cutoff = date(2020, 9, 9)
    _, meta = build_synthetic_dataset(tmp_path / "ds_exact_cutoff", cutoff=cutoff)
    tx_df: pl.DataFrame = meta["transactions_df"]
    future_buyer = meta["future_only_buyer"]
    assert tx_df.filter((pl.col("customer_id") == future_buyer) & (pl.col("t_dat") == cutoff)).height >= 1
    assert tx_df.filter((pl.col("customer_id") == future_buyer) & (pl.col("t_dat") < cutoff)).height == 0

    v2_dir, _ = build_synthetic_v2_release(tmp_path / "v2_exact_cutoff", data_as_of=cutoff)
    user_stats = pl.read_parquet(v2_dir / "user_stats.parquet")
    histories = pl.read_parquet(v2_dir / "histories.parquet")
    catalog = pl.read_parquet(v2_dir / "catalog.parquet")

    pref_col = "pref_index_group" if "pref_index_group" in user_stats.columns else "preferred_index_group"
    row = user_stats.filter(pl.col("customer_id") == future_buyer).to_dicts()[0]
    assert row["user_count180"] == 0
    assert row["user_count28"] == 0
    assert row[pref_col] in (None, "")
    assert histories.filter(pl.col("customer_id") == future_buyer).height == 0
    assert catalog["first_seen"].max() < cutoff


@pytest.mark.tier2
def test_t2_qual_benchmark_superiority_tie_and_regression_rejection() -> None:
    q2_path = REPO_ROOT / "configs" / "quality" / "quality-v2.json"
    assert q2_path.is_file(), "Expected configs/quality/quality-v2.json"
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
    assert callable(gate_fn), "Expected public benchmark gate function in hm_recommender.v2.evaluate"

    assert _invoke_segment_or_summary_gate(
        gate_fn,
        users=1000,
        rel_delta=-0.015,
        mean_map_at_12=0.0315,
        discovery_map_at_12=0.0095,
        test_2020_09_16_map_at_12=0.0320,
    ) is True
    assert _invoke_segment_or_summary_gate(
        gate_fn,
        users=1000,
        rel_delta=-0.015,
        mean_map_at_12=0.02544361,
    ) is False  # exact tie with RG
    assert _invoke_segment_or_summary_gate(
        gate_fn,
        users=1000,
        rel_delta=-0.015,
        mean_map_at_12=0.022200,
    ) is False  # below H
    assert _invoke_segment_or_summary_gate(
        gate_fn,
        users=1000,
        rel_delta=-0.015,
        discovery_map_at_12=0.006437,
    ) is False  # exact tie with P discovery
    assert _invoke_segment_or_summary_gate(
        gate_fn,
        users=1000,
        rel_delta=-0.015,
        test_2020_09_16_map_at_12=0.02628451,
    ) is False  # exact tie with H on 2020-09-16
    assert _invoke_segment_or_summary_gate(
        gate_fn,
        users=1000,
        rel_delta=-0.0201,
    ) is False  # segment regression > 2% with >= 1000 users


@pytest.mark.tier2
def test_t2_qual_e2e_suite_zero_top_level_v2_imports() -> None:
    def _walk_module_scope(stmts: list[ast.stmt]):
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield stmt
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list):
                    yield from _walk_module_scope(nested)
            handlers = getattr(stmt, "handlers", None)
            if isinstance(handlers, list):
                for h in handlers:
                    yield from _walk_module_scope(h.body)

    e2e_dir = REPO_ROOT / "tests" / "e2e"
    py_files = sorted(e2e_dir.glob("*.py"))
    assert py_files
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in _walk_module_scope(tree.body):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not (
                        alias.name == "hm_recommender.v2" or alias.name.startswith("hm_recommender.v2.")
                    ), f"Forbidden top-level import {alias.name!r} in {py_file.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not (
                    mod == "hm_recommender.v2" or mod.startswith("hm_recommender.v2.")
                ), f"Forbidden top-level from-import {mod!r} in {py_file.name}"
                if mod == "hm_recommender":
                    assert all(alias.name != "v2" for alias in node.names)


@pytest.mark.tier2
def test_t2_qual_frozen_v1_rejects_mutation_and_no_git_dependency(tmp_path: Path) -> None:
    q1_orig = json.loads((REPO_ROOT / "configs" / "quality" / "quality-v1.json").read_text(encoding="utf-8"))

    mut_cand = tmp_path / "q1_cand.json"
    mut_cand.write_text(json.dumps({**q1_orig, "candidates": 201}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(mut_cand)

    mut_fold = tmp_path / "q1_fold.json"
    bad_folds = dict(q1_orig["folds"])
    bad_folds["V3"] = "2020-09-10"
    mut_fold.write_text(json.dumps({**q1_orig, "folds": bad_folds}), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed"):
        load_config(mut_fold)

    mig_002 = (MIGRATIONS_DIR / "002_releases.sql").read_text(encoding="utf-8")
    assert "^h-[a-f0-9]{64}$" in mig_002
    assert "v2-" not in mig_002
