"""Self-check tests verifying the E2E harness, markers, and h-*/v2-* fixtures inside Docker jobs."""

from __future__ import annotations

import ast
import base64
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import lightgbm as lgb
import numpy as np
import polars as pl
import pytest
import scipy.sparse as sp

from hm_recommender.api.app import create_app
from hm_recommender.api.auth import COOKIE_NAME, SHORT_SESSION_DURATION, verify_session_token
from hm_recommender.data.io import sha256
from hm_recommender.releases.package import verify_history_release
from hm_recommender.ui.images import PLACEHOLDER, ImageCache
from tests.e2e.harness import (
    MIGRATIONS_DIR,
    REPO_ROOT,
    UI_APP_PATH,
    FakeEventRegistry,
    InMemoryApiAdapter,
    _find_spec_safe,
    _try_delegate_v2_release,
    build_synthetic_dataset,
    build_synthetic_image_zip,
    build_synthetic_v2_release,
    measure_p95_ms,
    resolve_artifacts_dir,
    resolve_hm_v1_dir,
    ui_apptest_session,
    visible_text,
)


@pytest.mark.selfcheck
def test_selfcheck_repo_root_and_hm_v1_visibility() -> None:
    assert (REPO_ROOT / "src" / "hm_recommender").is_dir()
    assert (REPO_ROOT / "tests" / "e2e").is_dir()
    assert MIGRATIONS_DIR.is_dir()
    assert UI_APP_PATH.is_file()

    hm_v1 = resolve_hm_v1_dir()
    assert hm_v1 is not None, "Expected processed hm-v1 dataset at /data/processed/hm-v1 or repo data/"
    manifest = json.loads((hm_v1 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["articles"] == 105542
    assert manifest["counts"]["customers"] == 1371980
    assert manifest["counts"]["transactions"] == 31788324

    artifacts_dir = resolve_artifacts_dir()
    if artifacts_dir is not None:
        assert artifacts_dir.is_dir()


@pytest.mark.selfcheck
def test_selfcheck_markers_registered_under_strict_markers(request: pytest.FixtureRequest) -> None:
    registered = {line.split(":", 1)[0].strip() for line in request.config.getini("markers")}
    for expected in ("tier1", "tier2", "tier3", "tier4", "selfcheck", "requires_postgres"):
        assert expected in registered


@pytest.mark.selfcheck
def test_selfcheck_synthetic_h_release_and_fastapi_serving(
    synthetic_h_release: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    manifest = verify_history_release(synthetic_h_release)
    assert re.fullmatch(r"^h-[a-f0-9]{64}$", manifest.release_id)

    input_root = tmp_path / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    staged_dir = input_root / "rel_h"
    shutil.copytree(synthetic_h_release, staged_dir)

    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(input_root))
    headers = {"Authorization": "Bearer local-test-token"}
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503

        prep = client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"})
        assert prep.status_code == 200
        assert prep.json()["release_id"] == manifest.release_id

        act = client.post("/admin/releases/activate", headers=headers, json={"release_id": manifest.release_id})
        assert act.status_code == 200
        assert client.get("/health/ready").json()["release_id"] == manifest.release_id

        rec = client.post("/recommend", json={"customer_id": "cust_0000", "k": 6})
        assert rec.status_code == 200
        body = rec.json()
        assert len(body["items"]) == 6
        assert all(re.fullmatch(r"^[0-9]{10}$", item["article_id"]) for item in body["items"])

        cat = client.get("/catalog", params={"offset": 0, "limit": 12})
        assert cat.status_code == 200
        assert cat.json()["total"] == 40
        assert len(cat.json()["items"]) == 12


@pytest.mark.selfcheck
def test_selfcheck_image_zip_and_cache_lifecycle(tmp_path: Path) -> None:
    aids = ["0000000000", "0000000001", "0000000002"]
    corrupt_id = "0000000002"
    zip_path = build_synthetic_image_zip(tmp_path / "imgs.zip", aids, corrupt_ids=[corrupt_id])

    cache_dir = tmp_path / "cache"
    # Each synthetic 2x2 JPEG is ~632 bytes; max_bytes=1500 holds 2 files (~1264B) and evicts on 3rd (~1896B)
    cache = ImageCache(zip_path, cache_dir, max_bytes=1500)

    data0, mime0 = cache.read("0000000000")
    assert mime0 == "image/jpeg"
    assert data0.startswith(b"\xff\xd8") and data0.endswith(b"\xff\xd9")

    data1, mime1 = cache.read("0000000001")
    assert mime1 == "image/jpeg"
    assert len(list(cache_dir.glob("*.jpg"))) == 2

    # Missing, corrupt, and non-10-digit IDs fall back to SVG placeholder
    for bad_id in ("0000000999", corrupt_id, "123", "../escape"):
        body, mime = cache.read(bad_id)
        assert mime == "image/svg+xml"
        assert body == PLACEHOLDER

    # Eviction triggered cleanly by a 3rd valid image
    zip_path_3 = build_synthetic_image_zip(tmp_path / "imgs3.zip", ["0000000000", "0000000001", "0000000003"])
    cache2 = ImageCache(zip_path_3, tmp_path / "cache2", max_bytes=1500)
    assert cache2.read("0000000000")[1] == "image/jpeg"
    assert cache2.read("0000000001")[1] == "image/jpeg"
    assert cache2.read("0000000003")[1] == "image/jpeg"
    assert len(list((tmp_path / "cache2").glob("*.jpg"))) == 2


@pytest.mark.selfcheck
def test_selfcheck_ui_apptest_mock_transport_smoke(in_memory_api) -> None:
    with ui_apptest_session(in_memory_api, default_timeout=25) as app:
        assert not app.exception
        profile_opts = app.selectbox(key="profile").options
        assert "Nuevo visitante" in profile_opts
        assert len(app.get("image")) > 0
        rendered = visible_text(app)
        assert "Recomendado para ti" in rendered
        assert in_memory_api.state.release_id not in rendered


@pytest.mark.selfcheck
def test_selfcheck_gateway_config_and_auth_portal(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    scrypt_pwd = "ScryptSecretPass2026!"
    legacy_pwd = "LegacySha256Pass2026!"
    cfg_path = gateway_config_factory({"alice": scrypt_pwd, "legacy": legacy_pwd}, hash_mode="scrypt")
    # Also inject a legacy sha256 user to verify transition compatibility
    cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg_data["password_hashes"]["legacy"] = hashlib.sha256(legacy_pwd.encode("utf-8")).hexdigest()
    cfg_path.write_text(json.dumps(cfg_data), encoding="utf-8")

    admin_token = "e2e-portal-admin-token"
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(tmp_path))
    client = TestClient(app)

    assert client.get("/login").status_code == 200

    res = client.post(
        "/login",
        data={"username": "alice", "password": scrypt_pwd},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert f"Max-Age={SHORT_SESSION_DURATION}" in res.headers["set-cookie"]
    session_cookie = res.cookies[COOKIE_NAME]
    derived_secret = hmac.new(admin_token.encode("utf-8"), b"hmrec:session-secret:v1", hashlib.sha256).digest()
    assert verify_session_token(session_cookie, derived_secret) == "alice"

    client.cookies.set(COOKIE_NAME, session_cookie)
    assert client.get("/auth/verify").status_code == 200
    client.cookies.clear()

    basic_legacy = "Basic " + base64.b64encode(f"legacy:{legacy_pwd}".encode("utf-8")).decode("ascii")
    assert client.get("/auth/verify", headers={"Authorization": basic_legacy}).status_code == 200

    assert client.get("/auth/verify", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 307
    assert client.get("/auth/verify", headers={"Accept": "application/json"}).status_code == 401
    assert client.get("/logout", follow_redirects=False).status_code == 303


@pytest.mark.selfcheck
def test_selfcheck_synthetic_v2_release_uvicorn_and_p95_timer(
    synthetic_v2_release: tuple[Path, str],
    uvicorn_server: tuple[str, httpx.Client],
) -> None:
    release_dir, release_id = synthetic_v2_release
    assert re.fullmatch(r"^v2-[a-f0-9]{64}$", release_id)
    assert release_dir.name == release_id

    expected_files = {
        "manifest.json",
        "rules.json",
        "validation-reference.json",
        "model.txt",
        "catalog.parquet",
        "item_stats.parquet",
        "user_stats.parquet",
        "user_affinities.parquet",
        "segment_popularity.parquet",
        "histories.parquet",
        "cooc.npz",
        "content_tfidf.npz",
        "svd_v.npy",
        "svd_sigma.npy",
    }
    assert {p.name for p in release_dir.iterdir()} == expected_files

    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["release_id"] == release_id
    assert manifest["strategy"] == "two_stage_lgbm_v2"
    for entry in manifest["files"].values():
        fpath = release_dir / entry["path"]
        assert fpath.stat().st_size == entry["size_bytes"]
        assert sha256(fpath) == entry["sha256"]

    catalog_cols = set(pl.read_parquet(release_dir / "catalog.parquet").columns)
    assert {
        "article_id",
        "product_code",
        "prod_name",
        "product_type_name",
        "product_group_name",
        "colour_group_name",
        "graphical_appearance_name",
        "index_group_name",
        "detail_desc",
        "first_seen",
    }.issubset(catalog_cols)

    booster = lgb.Booster(model_file=str(release_dir / "model.txt"))
    assert booster.num_trees() >= 1
    assert isinstance(sp.load_npz(release_dir / "cooc.npz"), sp.csr_matrix)
    assert isinstance(sp.load_npz(release_dir / "content_tfidf.npz"), sp.csr_matrix)
    svd = np.load(release_dir / "svd_v.npy")
    assert svd.dtype == np.float32 and svd.ndim == 2 and 1 <= svd.shape[1] <= 64
    svd_sigma = np.load(release_dir / "svd_sigma.npy")
    assert (
        svd_sigma.dtype == np.float32
        and svd_sigma.ndim == 1
        and svd_sigma.shape == (svd.shape[1],)
        and np.isfinite(svd_sigma).all()
        and (svd_sigma >= 0.0).all()
    )
    if svd_sigma.shape[0] >= 2:
        assert np.all(np.diff(svd_sigma) <= 1e-6)

    with pytest.raises(ValueError):
        measure_p95_ms(lambda: None, iterations=0)
    p95 = measure_p95_ms(lambda: sum(range(100)), iterations=20, warmup=2)
    assert isinstance(p95, float) and p95 >= 0.0

    _, http_client = uvicorn_server
    assert http_client.get("/health/live").status_code == 200
    assert http_client.get("/health/ready").status_code == 503


@pytest.mark.selfcheck
def test_selfcheck_synthetic_dataset_temporal_isolation_and_v2_user_stats(
    tmp_path: Path,
    synthetic_v2_release: tuple[Path, str],
) -> None:
    cutoff = date(2020, 9, 9)
    _, meta = build_synthetic_dataset(tmp_path / "ds_check", cutoff=cutoff)
    tx_df: pl.DataFrame = meta["transactions_df"]

    future_buyer = meta["future_only_buyer"]
    older_buyer = meta["older_buyer"]
    cold_start_buyer = meta["cold_start_buyer"]
    assert cold_start_buyer == meta["user_ids"][-1]

    future_pre = tx_df.filter((pl.col("customer_id") == future_buyer) & (pl.col("t_dat") < cutoff))
    future_post = tx_df.filter((pl.col("customer_id") == future_buyer) & (pl.col("t_dat") >= cutoff))
    assert future_pre.height == 0
    assert future_post.height >= 1

    older_all = tx_df.filter(pl.col("customer_id") == older_buyer)
    older_recent_28d = tx_df.filter(
        (pl.col("customer_id") == older_buyer)
        & (pl.col("t_dat") >= cutoff - timedelta(days=28))
        & (pl.col("t_dat") < cutoff)
    )
    assert older_all.height >= 1
    assert older_recent_28d.height == 0
    assert older_all.filter(pl.col("t_dat") >= cutoff).height == 0

    cold_rows = tx_df.filter(pl.col("customer_id") == cold_start_buyer)
    assert cold_rows.height == 0

    v2_dir, _ = synthetic_v2_release
    retrieval_mod = importlib.import_module("hm_recommender.v2.retrieval")
    user_stats = pl.read_parquet(v2_dir / "user_stats.parquet")
    assert set(user_stats["age_bin"].to_list()).issubset(set(retrieval_mod.AGE_BINS))
    future_row = user_stats.filter(pl.col("customer_id") == future_buyer).to_dicts()[0]
    cold_row = user_stats.filter(pl.col("customer_id") == cold_start_buyer).to_dicts()[0]
    older_row = user_stats.filter(pl.col("customer_id") == older_buyer).to_dicts()[0]
    pref_col = "pref_index_group" if "pref_index_group" in user_stats.columns else "preferred_index_group"
    assert future_row["user_count180"] == 0
    assert future_row[pref_col] in (None, "")
    assert cold_row["user_count180"] == 0
    assert cold_row[pref_col] in (None, "")
    assert older_row["user_count180"] >= 1
    assert older_row["user_count28"] == 0
    assert bool(older_row[pref_col])


@pytest.mark.selfcheck
def test_selfcheck_in_memory_adapter_v2_streams_discovery_and_swap(
    synthetic_h_release: Path,
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

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["release_id"] == v2_id

    # /recommend for cold-start user with preferences
    rec_pref = client.post(
        "/recommend",
        json={
            "customer_id": "cust_0007",
            "k": 6,
            "mode": "balanced",
            "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
        },
    )
    assert rec_pref.status_code == 200
    pref_body = rec_pref.json()
    assert pref_body["release_id"] == v2_id
    assert pref_body["fallback"] is True
    assert len(pref_body["items"]) == 6
    assert pref_body["items"][0]["reason"] == "popular_for_segment"

    # /streams/{id}/recommend: view does NOT exclude in mode='discovery', purchase DOES
    streams = client.get("/streams").json()["items"]
    assert len(streams) == 1
    sid = streams[0]["stream_id"]
    cutoff = date.fromisoformat(streams[0]["data_as_of"])
    ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
    viewed_article = "0000000018"
    purchased_article = "0000000022"
    headers = {"Authorization": "Bearer local-test-token"}

    ev_view = client.post(
        "/events",
        json={
            "event_id": str(uuid4()),
            "stream_id": sid,
            "release_id": v2_id,
            "customer_id": "cust_0007",
            "article_id": viewed_article,
            "kind": "view",
            "origin": "synthetic",
            "event_time": ev_time,
        },
    )
    assert ev_view.status_code == 201
    assert client.post(f"/admin/streams/{sid}/refresh", headers=headers, json={}).status_code == 200

    disc_after_view = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
    )
    assert disc_after_view.status_code == 200
    disc_view_body = disc_after_view.json()
    assert disc_view_body["fallback"] is False
    disc_view_ids = [item["article_id"] for item in disc_view_body["items"]]
    assert viewed_article in disc_view_ids

    # Check max 1 variant per product_code in discovery mode
    pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in disc_view_ids]
    assert len(pcodes) == len(set(pcodes))

    # Now record a purchase and verify discovery excludes purchased_article while still keeping viewed_article
    ev_buy = client.post(
        "/events",
        json={
            "event_id": str(uuid4()),
            "stream_id": sid,
            "release_id": v2_id,
            "customer_id": "cust_0007",
            "article_id": purchased_article,
            "kind": "purchase",
            "origin": "synthetic",
            "event_time": ev_time,
        },
    )
    assert ev_buy.status_code == 201
    assert client.post(f"/admin/streams/{sid}/refresh", headers=headers, json={}).status_code == 200

    disc_after_buy = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
    )
    assert disc_after_buy.status_code == 200
    disc_buy_ids = [item["article_id"] for item in disc_after_buy.json()["items"]]
    assert purchased_article not in disc_buy_ids
    assert viewed_article in disc_buy_ids

    # swap_release to h-* and back to v2-*
    h_id = adapter.swap_release(synthetic_h_release)
    assert re.fullmatch(r"^h-[a-f0-9]{64}$", h_id)
    assert client.get("/health/ready").json()["release_id"] == h_id
    assert client.post("/recommend", json={"customer_id": "cust_0000", "k": 4}).json()["release_id"] == h_id

    swapped_v2_id = adapter.swap_release(v2_dir)
    assert swapped_v2_id == v2_id
    assert client.get("/health/ready").json()["release_id"] == v2_id


@pytest.mark.selfcheck
def test_selfcheck_cold_start_mixed_live_signals_discovery(
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

    assert adapter.catalog_by_id["0000000018"]["index_group_name"] == "Menswear"
    assert adapter.catalog_by_id["0000000024"]["index_group_name"] == "Ladieswear"

    streams = client.get("/streams").json()["items"]
    sid = streams[0]["stream_id"]
    cutoff = date.fromisoformat(streams[0]["data_as_of"])
    ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
    headers = {"Authorization": "Bearer local-test-token"}

    for aid, kind in (
        ("0000000018", "view"),
        ("0000000024", "click"),
        ("0000000028", "purchase"),
    ):
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

    assert client.post(f"/admin/streams/{sid}/refresh", headers=headers, json={}).status_code == 200

    disc = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
    )
    assert disc.status_code == 200
    disc_ids = [item["article_id"] for item in disc.json()["items"]]
    assert len(disc_ids) == 12
    assert "0000000018" in disc_ids
    assert "0000000028" not in disc_ids


@pytest.mark.selfcheck
def test_selfcheck_delegate_v2_release_repeat_cleanup_and_spec_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = ModuleType("hm_recommender.v2.package")
    stub.__spec__ = None
    with patch.dict(sys.modules, {"hm_recommender.v2.package": stub}):
        assert _find_spec_safe("hm_recommender.v2.package") is None

    ds_root = tmp_path / "ds"
    ds, _ = build_synthetic_dataset(ds_root, num_articles=12, num_users=4)
    rel_root = tmp_path / "releases"
    rel_root.mkdir(parents=True, exist_ok=True)
    rid = "v2-" + ("c" * 64)
    seen_commits: list[str | None] = []
    verified_dirs: list[Path] = []

    def fake_build_v2_release(dataset, data_as_of: date, output_dir: Path):
        seen_commits.append(os.environ.get("HMREC_SOURCE_COMMIT"))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "manifest.json").write_text(json.dumps({"release_id": rid}), encoding="utf-8")
        return SimpleNamespace(release_id=rid)

    def fake_verify_v2_release(directory: Path):
        verified_dirs.append(directory)
        return SimpleNamespace(release_id=rid)

    pkg_mod = ModuleType("hm_recommender.v2.package")
    pkg_mod.build_v2_release = fake_build_v2_release  # type: ignore[attr-defined]
    pkg_mod.verify_v2_release = fake_verify_v2_release  # type: ignore[attr-defined]
    parent_mod = ModuleType("hm_recommender.v2")
    parent_mod.package = pkg_mod  # type: ignore[attr-defined]

    # Pre-seed stale staging directory to verify pre-call cleanup as well
    stale = rel_root / "_staged_v2_pkg"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "stale.txt").write_text("stale", encoding="utf-8")

    with (
        patch.dict(sys.modules, {"hm_recommender.v2": parent_mod, "hm_recommender.v2.package": pkg_mod}),
        patch("tests.e2e.harness._find_spec_safe", return_value=object()),
    ):
        first = _try_delegate_v2_release(ds, date(2020, 9, 9), rel_root)
        assert first == (rel_root / rid, rid)
        assert not (rel_root / "_staged_v2_pkg").exists()

        second = _try_delegate_v2_release(ds, date(2020, 9, 9), rel_root)
        assert second == (rel_root / rid, rid)
        assert not (rel_root / "_staged_v2_pkg").exists()
        assert len(verified_dirs) == 2

        monkeypatch.delenv("HMREC_SOURCE_COMMIT", raising=False)
        delegated_root = tmp_path / "delegated_env_check"
        out_dir, out_id = build_synthetic_v2_release(delegated_root, num_articles=12, num_users=4)
        assert out_id == rid
        assert out_dir == delegated_root / rid
        assert not (delegated_root / "_staged_v2_pkg").exists()
        assert seen_commits[-1] == "a" * 40
        assert "HMREC_SOURCE_COMMIT" not in os.environ


@pytest.mark.selfcheck
def test_selfcheck_visible_text_includes_header_and_text() -> None:
    fake_app = SimpleNamespace(
        title=[SimpleNamespace(value="T_TITLE")],
        header=[SimpleNamespace(value="T_HEADER")],
        subheader=[SimpleNamespace(value="T_SUBHEADER")],
        text=[SimpleNamespace(value="T_TEXT")],
        caption=[],
        markdown=[],
        info=[],
        success=[],
        warning=[],
        error=[],
    )
    rendered = visible_text(fake_app)  # type: ignore[arg-type]
    for expected in ("T_TITLE", "T_HEADER", "T_SUBHEADER", "T_TEXT"):
        assert expected in rendered


@pytest.mark.selfcheck
def test_selfcheck_zero_top_level_v2_imports_in_e2e_suite() -> None:
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
                    ), f"Forbidden top-level v2 import {alias.name!r} in {py_file.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not (
                    mod == "hm_recommender.v2" or mod.startswith("hm_recommender.v2.")
                ), f"Forbidden top-level v2 from-import {mod!r} in {py_file.name}"
                if mod == "hm_recommender":
                    assert all(alias.name != "v2" for alias in node.names), (
                        f"Forbidden top-level 'from hm_recommender import v2' in {py_file.name}"
                    )
