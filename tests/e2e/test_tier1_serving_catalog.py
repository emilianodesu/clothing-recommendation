"""Tier 1 isolated happy-path E2E tests for F_PKG, F_REC, F_CAT, and F_IMG.

IMPORTANT: Zero top-level ``import hm_recommender.v2...`` or
``import hm_recommender.api.recommend_v2`` statements are allowed here.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import time as time_mod
from typing import Any
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
import lightgbm as lgb
import numpy as np
import polars as pl
import pytest
import scipy.sparse as sp

from hm_recommender.api.app import create_app
from hm_recommender.api.recommend import HistoryState, load_state
from hm_recommender.api.schemas import ActivateRequest
from hm_recommender.data.io import sha256
from hm_recommender.releases.package import verify_history_release
from hm_recommender.ui.images import PLACEHOLDER, ImageCache
from tests.e2e.harness import (
    MIGRATIONS_DIR,
    FakeEventRegistry,
    InMemoryApiAdapter,
    build_synthetic_dataset,
    build_synthetic_image_zip,
    build_synthetic_v2_release,
)


def _prepare_and_activate_h(
    fake_registry: FakeEventRegistry,
    h_release_dir: Path,
    tmp_path: Path,
    *,
    admin_token: str = "local-test-token",
    image_zip: Path | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[TestClient, str]:
    input_root = tmp_path / f"input_{uuid4().hex[:8]}"
    input_root.mkdir(parents=True, exist_ok=True)
    staged = input_root / "rel_h"
    shutil.copytree(h_release_dir, staged)
    if image_zip is not None and monkeypatch is not None:
        monkeypatch.setenv("HMREC_IMAGE_ARCHIVE", str(image_zip))
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_token}"}
    prep = client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"})
    assert prep.status_code == 200
    rid = prep.json()["release_id"]
    act = client.post("/admin/releases/activate", headers=headers, json={"release_id": rid})
    assert act.status_code == 200
    return client, rid


def _prepare_and_activate_v2(
    fake_registry: FakeEventRegistry,
    v2_release_dir: Path,
    tmp_path: Path,
    *,
    admin_token: str = "local-test-token",
    enable_events: bool = False,
) -> tuple[TestClient, str, str | None]:
    rec_v2 = importlib.import_module("hm_recommender.api.recommend_v2")
    two_stage_cls = getattr(rec_v2, "TwoStageState", None)
    assert isinstance(two_stage_cls, type), "hm_recommender.api.recommend_v2 must expose TwoStageState"

    stream_id: str | None = None
    if enable_events:
        stream_id = fake_registry.enable_in_memory_events()

    input_root = tmp_path / f"input_v2_{uuid4().hex[:8]}"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v2_release_dir, input_root / "rel_v2")
    app = create_app(fake_registry, admin_token=admin_token, input_root=str(input_root))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_token}"}
    prep = client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"})
    assert prep.status_code == 200, f"Failed to prepare v2-* release on create_app: {prep.text}"
    rid = prep.json()["release_id"]
    act = client.post("/admin/releases/activate", headers=headers, json={"release_id": rid})
    assert act.status_code == 200, f"Failed to activate v2-* release on create_app: {act.text}"
    assert isinstance(fake_registry.snapshot(), two_stage_cls), (
        f"Expected active snapshot to be TwoStageState, got {type(fake_registry.snapshot())}"
    )
    return client, rid, stream_id


# ---------------------------------------------------------------------------
# A1. F_PKG — Portable v2-* Package & h-* Compatibility (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
def test_t1_pkg_v2_portable_12_file_package_and_verification(
    synthetic_v2_release: tuple[Path, str],
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
        assert fpath.is_file()
        assert fpath.stat().st_size == entry["size_bytes"]
        assert sha256(fpath) == entry["sha256"]
        if entry["rows"] is not None:
            assert pl.read_parquet(fpath).height == entry["rows"]

    catalog_df = pl.read_parquet(release_dir / "catalog.parquet")
    required_catalog_cols = {
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
    }
    assert required_catalog_cols.issubset(set(catalog_df.columns))

    booster = lgb.Booster(model_file=str(release_dir / "model.txt"))
    assert booster.num_trees() >= 1
    assert isinstance(sp.load_npz(release_dir / "cooc.npz"), sp.csr_matrix)
    assert isinstance(sp.load_npz(release_dir / "content_tfidf.npz"), sp.csr_matrix)
    svd = np.load(release_dir / "svd_v.npy")
    assert svd.dtype == np.float32 and svd.ndim == 2 and svd.shape[0] == catalog_df.height and 1 <= svd.shape[1] <= 64
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

    v2_pkg = importlib.import_module("hm_recommender.v2.package")
    verifier = getattr(v2_pkg, "verify_v2_release", None)
    assert callable(verifier), "hm_recommender.v2.package must expose verify_v2_release"
    verified_manifest = verifier(release_dir)
    assert verified_manifest.release_id == release_id


@pytest.mark.tier1
def test_t1_pkg_h_release_build_verify_and_api_parity(
    synthetic_h_release: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    manifest = verify_history_release(synthetic_h_release)
    assert re.fullmatch(r"^h-[a-f0-9]{64}$", manifest.release_id)
    expected_state = load_state(synthetic_h_release)

    client, rid = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)
    with client:
        assert rid == manifest.release_id
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["release_id"] == rid

        rec = client.post("/recommend", json={"customer_id": "cust_0000", "k": 6})
        assert rec.status_code == 200
        body = rec.json()
        assert body["release_id"] == rid
        assert body["strategy"] == "recent_then_popular"
        assert body["fallback"] is False
        assert len(body["items"]) == 6
        assert all(it["reason"] in {"recent_purchase", "popular_7d"} for it in body["items"])

        direct = expected_state.recommend(
            importlib.import_module("hm_recommender.api.schemas").RecommendationRequest(
                customer_id="cust_0000", k=6
            )
        )
        assert [it["article_id"] for it in body["items"]] == [it.article_id for it in direct.items]


@pytest.mark.tier1
def test_t1_pkg_prepare_activate_rollback_v2_to_h(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    v2_dir, v2_id = synthetic_v2_release
    h_manifest = verify_history_release(synthetic_h_release)
    h_id = h_manifest.release_id

    fake_registry.open()
    try:
        pub_h = fake_registry.prepare_release(synthetic_h_release)
        pub_v2 = fake_registry.prepare_release(v2_dir)
        assert pub_h["release_id"] == h_id
        assert pub_v2["release_id"] == v2_id

        fake_registry.activate_release(h_id)
        assert fake_registry.snapshot().release_id == h_id
        fake_registry.activate_release(v2_id)
        assert fake_registry.snapshot().release_id == v2_id
        rolled = fake_registry.rollback()
        assert rolled == h_id
        assert fake_registry.snapshot().release_id == h_id
    finally:
        fake_registry.close()

    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    client = adapter.client()
    assert client.get("/health/ready").json()["release_id"] == v2_id
    assert adapter.swap_release(synthetic_h_release) == h_id
    assert client.get("/health/ready").json()["release_id"] == h_id
    assert adapter.swap_release(v2_dir) == v2_id
    assert client.get("/health/ready").json()["release_id"] == v2_id

    assert ActivateRequest(release_id=v2_id).release_id == v2_id
    rec_v2 = importlib.import_module("hm_recommender.api.recommend_v2")
    two_stage_cls = getattr(rec_v2, "TwoStageState", None)
    assert isinstance(two_stage_cls, type), "hm_recommender.api.recommend_v2 must expose TwoStageState"

    http_reg = FakeEventRegistry(tmp_path / "http_reg")
    input_root = tmp_path / "http_input"
    input_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(synthetic_h_release, input_root / "rel_h")
    shutil.copytree(v2_dir, input_root / "rel_v2")
    app = create_app(http_reg, admin_token="local-test-token", input_root=str(input_root))
    headers = {"Authorization": "Bearer local-test-token"}
    with TestClient(app) as http_client:
        assert http_client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"}).status_code == 200
        assert http_client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"}).status_code == 200
        assert http_client.post("/admin/releases/activate", headers=headers, json={"release_id": h_id}).status_code == 200
        assert isinstance(http_reg.snapshot(), HistoryState)
        assert http_client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200
        assert isinstance(http_reg.snapshot(), two_stage_cls)
        assert http_client.get("/health/ready").json()["release_id"] == v2_id
        rb = http_client.post("/admin/releases/rollback", headers=headers)
        assert rb.status_code == 200
        assert rb.json()["release_id"] == h_id
        assert isinstance(http_reg.snapshot(), HistoryState)
        assert http_client.get("/health/ready").json()["release_id"] == h_id


@pytest.mark.tier1
def test_t1_pkg_v2_temporal_isolation_strictly_before_cutoff(
    tmp_path: Path,
    source_commit_env: str,
) -> None:
    cutoff = date(2020, 9, 9)
    _, meta = build_synthetic_dataset(tmp_path / "ds_iso", cutoff=cutoff)
    v2_dir, _ = build_synthetic_v2_release(tmp_path / "v2_iso", data_as_of=cutoff)

    catalog_df = pl.read_parquet(v2_dir / "catalog.parquet")
    histories_df = pl.read_parquet(v2_dir / "histories.parquet")
    user_stats_df = pl.read_parquet(v2_dir / "user_stats.parquet")

    assert catalog_df["first_seen"].max() < cutoff
    if histories_df.height > 0:
        assert histories_df["last_purchase"].max() < cutoff

    future_buyer = meta["future_only_buyer"]
    older_buyer = meta["older_buyer"]

    pref_col = "pref_index_group" if "pref_index_group" in user_stats_df.columns else "preferred_index_group"
    assert histories_df.filter(pl.col("customer_id") == future_buyer).height == 0
    future_row = user_stats_df.filter(pl.col("customer_id") == future_buyer).to_dicts()[0]
    assert future_row["user_count180"] == 0
    assert future_row[pref_col] in (None, "")

    older_row = user_stats_df.filter(pl.col("customer_id") == older_buyer).to_dicts()[0]
    assert older_row["user_count180"] >= 1
    assert older_row["user_count28"] == 0
    assert bool(older_row[pref_col])


@pytest.mark.tier1
def test_t1_pkg_005_v2_migration_ddl_and_constraints() -> None:
    ddl_path = MIGRATIONS_DIR / "005_v2_releases.sql"
    assert ddl_path.is_file(), "Expected migrations/005_v2_releases.sql for M3 v2-* schema support"
    ddl = ddl_path.read_text(encoding="utf-8")
    assert "serving_releases" in ddl
    assert "(h|v2)-[a-f0-9]{64}" in ddl
    assert "interaction_events" in ddl
    assert "CREATE INDEX" in ddl.upper()


@pytest.mark.tier1
@pytest.mark.requires_postgres
def test_t1_pkg_postgres_v2_and_h_prepare_activate_rollback(
    postgres_serving_env: Any,
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
) -> None:
    client, _registry, connection, _schema, tmp_path = postgres_serving_env
    v2_dir, v2_id = synthetic_v2_release
    h_id = verify_history_release(synthetic_h_release).release_id

    input_root = tmp_path / "input"
    shutil.copytree(synthetic_h_release, input_root / "rel_h")
    shutil.copytree(v2_dir, input_root / "rel_v2")
    headers = {"Authorization": "Bearer local-test-token"}

    prep_h = client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_h"})
    assert prep_h.status_code == 200
    assert prep_h.json()["release_id"] == h_id

    prep_v2 = client.post("/admin/releases/prepare", headers=headers, json={"directory": "rel_v2"})
    assert prep_v2.status_code == 200
    assert prep_v2.json()["release_id"] == v2_id

    assert client.post("/admin/releases/activate", headers=headers, json={"release_id": h_id}).status_code == 200
    assert client.get("/health/ready").json()["release_id"] == h_id

    assert client.post("/admin/releases/activate", headers=headers, json={"release_id": v2_id}).status_code == 200
    assert client.get("/health/ready").json()["release_id"] == v2_id

    with connection() as conn:
        ids = {row[0] for row in conn.execute("SELECT release_id FROM serving_releases").fetchall()}
        active_id, prev_id = conn.execute("SELECT active_id, previous_id FROM serving_pointer WHERE singleton").fetchone()
    assert {h_id, v2_id}.issubset(ids)
    assert active_id == v2_id
    assert prev_id == h_id

    rb = client.post("/admin/releases/rollback", headers=headers)
    assert rb.status_code == 200
    assert rb.json()["release_id"] == h_id
    assert client.get("/health/ready").json()["release_id"] == h_id


# ---------------------------------------------------------------------------
# A2. F_REC — Real-Time /recommend & /streams/{id}/recommend (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
def test_t1_rec_v2_balanced_vs_discovery_and_variant_dedup(
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
    hist_df = pl.read_parquet(v2_dir / "histories.parquet")
    purchased_cust0 = set(hist_df.filter(pl.col("customer_id") == "cust_0000")["article_id"].to_list())

    for client in (adapter.client(),):
        bal = client.post("/recommend", json={"customer_id": "cust_0000", "k": 12, "mode": "balanced"})
        assert bal.status_code == 200
        bal_body = bal.json()
        assert bal_body["release_id"] == v2_id
        assert bal_body["strategy"] == "two_stage_lgbm_v2"
        assert bal_body["fallback"] is False
        assert len(bal_body["items"]) == 12
        assert [it["rank"] for it in bal_body["items"]] == list(range(1, 13))
        assert all(re.fullmatch(r"^[0-9]{10}$", it["article_id"]) for it in bal_body["items"])

        disc = client.post("/recommend", json={"customer_id": "cust_0000", "k": 12, "mode": "discovery"})
        assert disc.status_code == 200
        disc_body = disc.json()
        assert disc_body["release_id"] == v2_id
        assert 1 <= len(disc_body["items"]) <= 12
        disc_ids = [it["article_id"] for it in disc_body["items"]]
        assert purchased_cust0.isdisjoint(disc_ids)
        pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in disc_ids]
        assert len(pcodes) == len(set(pcodes))

    # Real create_app + TwoStageState verification
    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)
    with http_client:
        bal_real = http_client.post("/recommend", json={"customer_id": "cust_0000", "k": 12, "mode": "balanced"})
        assert bal_real.status_code == 200
        bal_real_body = bal_real.json()
        assert bal_real_body["release_id"] == v2_id
        assert bal_real_body["strategy"] == "two_stage_lgbm_v2"
        assert bal_real_body["fallback"] is False
        assert len(bal_real_body["items"]) == 12

        disc_real = http_client.post("/recommend", json={"customer_id": "cust_0000", "k": 12, "mode": "discovery"})
        assert disc_real.status_code == 200
        disc_real_body = disc_real.json()
        assert disc_real_body["release_id"] == v2_id
        disc_real_ids = [it["article_id"] for it in disc_real_body["items"]]
        assert 1 <= len(disc_real_ids) <= 12
        assert purchased_cust0.isdisjoint(disc_real_ids)
        real_pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in disc_real_ids]
        assert len(real_pcodes) == len(set(real_pcodes))


@pytest.mark.tier1
def test_t1_rec_v2_cold_start_routing_and_preferences(
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
    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            for cid in ("cust_0007", "new-anon-user"):
                bare = client.post("/recommend", json={"customer_id": cid, "k": 8})
                assert bare.status_code == 200
                bare_body = bare.json()
                assert bare_body["fallback"] is True
                assert len(bare_body["items"]) == 8
                assert all(it["reason"] in {"popular_for_segment", "popular_7d"} for it in bare_body["items"])

                pref = client.post(
                    "/recommend",
                    json={
                        "customer_id": cid,
                        "k": 8,
                        "preferences": {"age_bin": "<25", "index_group_name": "Menswear"},
                    },
                )
                assert pref.status_code == 200
                pref_body = pref.json()
                assert pref_body["fallback"] is True
                assert len(pref_body["items"]) == 8
                assert pref_body["items"][0]["reason"] == "popular_for_segment"
                assert adapter.catalog_by_id[pref_body["items"][0]["article_id"]]["index_group_name"] == "Menswear"


@pytest.mark.tier1
def test_t1_rec_v2_live_stream_weights_and_refresh(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    retrieval_mod = importlib.import_module("hm_recommender.v2.retrieval")
    assert retrieval_mod.EVENT_KIND_WEIGHTS == {"purchase": 1.0, "click": 0.55, "view": 0.25}

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
    headers = {"Authorization": "Bearer local-test-token"}

    for aid, kind in (("0000000018", "view"), ("0000000024", "click"), ("0000000028", "purchase")):
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

    ref = client.post(f"/admin/streams/{sid}/refresh", headers=headers, json={"data_as_of": cutoff.isoformat()})
    assert ref.status_code == 200
    assert ref.json()["processed_events"] == 3

    disc = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
    )
    assert disc.status_code == 200
    disc_body = disc.json()
    assert disc_body["fallback"] is False
    disc_ids = [it["article_id"] for it in disc_body["items"]]
    assert "0000000028" not in disc_ids
    assert "0000000018" in disc_ids
    assert "0000000024" in disc_ids

    bal = client.post(
        f"/streams/{sid}/recommend",
        json={"customer_id": "cust_0007", "k": 12, "mode": "balanced"},
    )
    assert bal.status_code == 200
    bal_ids = [it["article_id"] for it in bal.json()["items"]]
    assert "0000000028" in bal_ids
    assert "0000000024" in bal_ids
    assert "0000000018" in bal_ids
    assert bal_ids.index("0000000028") < bal_ids.index("0000000024") < bal_ids.index("0000000018")

    # Real create_app + TwoStageState live /events & /streams/{id}/recommend verification
    http_client, _, real_sid = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path, enable_events=True)
    assert real_sid is not None
    with http_client:
        for aid, kind in (("0000000018", "view"), ("0000000024", "click"), ("0000000028", "purchase")):
            ev_res = http_client.post(
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
            )
            assert ev_res.status_code == 201, f"Expected 201 on create_app /events for v2-*, got {ev_res.status_code}: {ev_res.text}"

        ref_meta = fake_registry.refresh_in_memory_stream(real_sid, cutoff)
        assert ref_meta["processed_events"] == 3

        real_disc = http_client.post(
            f"/streams/{real_sid}/recommend",
            json={"customer_id": "cust_0007", "k": 12, "mode": "discovery"},
        )
        assert real_disc.status_code == 200, f"create_app /streams/{{id}}/recommend failed: {real_disc.text}"
        real_disc_body = real_disc.json()
        assert real_disc_body["fallback"] is False
        real_disc_ids = [it["article_id"] for it in real_disc_body["items"]]
        assert "0000000028" not in real_disc_ids
        assert "0000000024" in real_disc_ids
        assert "0000000018" in real_disc_ids

        real_bal = http_client.post(
            f"/streams/{real_sid}/recommend",
            json={"customer_id": "cust_0007", "k": 12, "mode": "balanced"},
        )
        assert real_bal.status_code == 200
        real_bal_ids = [it["article_id"] for it in real_bal.json()["items"]]
        assert "0000000028" in real_bal_ids
        assert "0000000024" in real_bal_ids
        assert "0000000018" in real_bal_ids


@pytest.mark.tier1
def test_t1_rec_v2_explainable_reasons_contract(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    retrieval_mod = importlib.import_module("hm_recommender.v2.retrieval")
    expected_reasons = set(retrieval_mod.EXPLAINABLE_REASONS)
    assert expected_reasons == {
        "recent_purchase",
        "same_product_variant",
        "frequently_bought_together",
        "latent_style_match",
        "content_similar",
        "popular_for_segment",
        "popular_7d",
    }

    v2_dir, v2_id = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
        admin_token="local-test-token",
    )
    client = adapter.client()

    # (a) returning customer
    ret = client.post("/recommend", json={"customer_id": "cust_0000", "k": 12}).json()
    assert {it["reason"] for it in ret["items"]}.issubset(expected_reasons)
    assert any(
        it["reason"] in {
            "recent_purchase",
            "same_product_variant",
            "frequently_bought_together",
            "latent_style_match",
            "content_similar",
        }
        for it in ret["items"]
    )

    # (b) cold-start with live click/view
    streams = client.get("/streams").json()["items"]
    sid = streams[0]["stream_id"]
    cutoff = date.fromisoformat(streams[0]["data_as_of"])
    ev_time = datetime.combine(cutoff - timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()
    client.post(
        "/events",
        json={
            "event_id": str(uuid4()),
            "stream_id": sid,
            "release_id": v2_id,
            "customer_id": "cust_0007",
            "article_id": "0000000018",
            "kind": "click",
            "origin": "synthetic",
            "event_time": ev_time,
        },
    )
    client.post(
        f"/admin/streams/{sid}/refresh",
        headers={"Authorization": "Bearer local-test-token"},
        json={"data_as_of": cutoff.isoformat()},
    )
    live_res = client.post(f"/streams/{sid}/recommend", json={"customer_id": "cust_0007", "k": 6}).json()
    assert live_res["fallback"] is False
    assert {it["reason"] for it in live_res["items"]}.issubset(expected_reasons)

    # (c) cold-start with preferences
    pref_res = client.post(
        "/recommend",
        json={
            "customer_id": "anon-pref-user",
            "k": 6,
            "preferences": {"age_bin": "35-44", "index_group_name": "Ladieswear"},
        },
    ).json()
    assert {it["reason"] for it in pref_res["items"]}.issubset(expected_reasons)
    assert pref_res["items"][0]["reason"] == "popular_for_segment"

    # (d) bare cold-start
    bare_res = client.post("/recommend", json={"customer_id": "anon-bare-user", "k": 6}).json()
    assert {it["reason"] for it in bare_res["items"]}.issubset(expected_reasons)
    assert bare_res["items"][0]["reason"] == "popular_7d"

    # Real create_app + TwoStageState contract verification
    http_client, _, _ = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path)
    with http_client:
        real_ret = http_client.post("/recommend", json={"customer_id": "cust_0000", "k": 12})
        assert real_ret.status_code == 200
        real_ret_body = real_ret.json()
        assert {it["reason"] for it in real_ret_body["items"]}.issubset(expected_reasons)
        assert any(
            it["reason"] in {
                "recent_purchase",
                "same_product_variant",
                "frequently_bought_together",
                "latent_style_match",
                "content_similar",
            }
            for it in real_ret_body["items"]
        )

        real_pref = http_client.post(
            "/recommend",
            json={
                "customer_id": "anon-pref-user",
                "k": 6,
                "preferences": {"age_bin": "35-44", "index_group_name": "Ladieswear"},
            },
        )
        assert real_pref.status_code == 200
        assert real_pref.json()["items"][0]["reason"] == "popular_for_segment"

        real_bare = http_client.post("/recommend", json={"customer_id": "anon-bare-user", "k": 6})
        assert real_bare.status_code == 200
        assert real_bare.json()["items"][0]["reason"] == "popular_7d"


@pytest.mark.tier1
def test_t1_rec_h_backward_compatibility_and_filters(
    synthetic_h_release: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)
    with client:
        rec = client.post("/recommend", json={"customer_id": "cust_0000", "k": 5})
        assert rec.status_code == 200
        body = rec.json()
        assert body["strategy"] == "recent_then_popular"
        assert len(body["items"]) == 5
        assert all(it["reason"] in {"recent_purchase", "popular_7d"} for it in body["items"])

        filtered = client.post(
            "/recommend",
            json={"customer_id": "cust_0000", "k": 10, "filters": {"colour_group_name": "Black"}},
        )
        assert filtered.status_code == 200
        filtered_ids = [it["article_id"] for it in filtered.json()["items"]]
        assert filtered_ids
        details = client.post("/articles/details", json={"article_ids": filtered_ids}).json()["items"]
        assert all(d["colour_group_name"] == "Black" for d in details)

        unseen = client.post("/recommend", json={"customer_id": "never_seen_before", "k": 5})
        assert unseen.status_code == 200
        unseen_body = unseen.json()
        assert unseen_body["fallback"] is True
        assert all(it["reason"] == "popular_7d" for it in unseen_body["items"])


@pytest.mark.tier1
def test_t1_rec_stream_metadata_and_idempotent_event_ingestion(
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
        admin_token="local-test-token",
    )
    client = adapter.client()
    streams = client.get("/streams").json()["items"]
    sid = streams[0]["stream_id"]
    init_rev = streams[0]["revision"]
    cutoff = date.fromisoformat(streams[0]["data_as_of"])
    ev_time = datetime.combine(cutoff - timedelta(days=1), time(10), tzinfo=timezone.utc).isoformat()

    payload = {
        "event_id": str(uuid4()),
        "stream_id": sid,
        "release_id": v2_id,
        "customer_id": "cust_0007",
        "article_id": "0000000005",
        "kind": "click",
        "origin": "synthetic",
        "event_time": ev_time,
    }
    first = client.post("/events", json=payload)
    assert first.status_code == 201
    assert first.json()["status"] == "created"

    replay = client.post("/events", json=payload)
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"

    ref = client.post(
        f"/admin/streams/{sid}/refresh",
        headers={"Authorization": "Bearer local-test-token"},
        json={"data_as_of": cutoff.isoformat()},
    )
    assert ref.status_code == 200
    ref_body = ref.json()
    assert ref_body["revision"] == init_rev + 1
    assert ref_body["processed_events"] == 1

    rec = client.post(f"/streams/{sid}/recommend", json={"customer_id": "cust_0007", "k": 6})
    assert rec.status_code == 200
    rec_body = rec.json()
    for field in ("stream_id", "revision", "received_before", "published_at", "mode"):
        assert field in rec_body
    assert rec_body["stream_id"] == sid
    assert rec_body["revision"] == init_rev + 1

    # Real create_app + TwoStageState /events & /streams/{id}/recommend verification
    http_client, _, real_sid = _prepare_and_activate_v2(fake_registry, v2_dir, tmp_path, enable_events=True)
    assert real_sid is not None
    with http_client:
        real_payload = {
            "event_id": str(uuid4()),
            "stream_id": real_sid,
            "release_id": v2_id,
            "customer_id": "cust_0007",
            "article_id": "0000000005",
            "kind": "click",
            "origin": "synthetic",
            "event_time": ev_time,
        }
        r_first = http_client.post("/events", json=real_payload)
        assert r_first.status_code == 201 and r_first.json()["status"] == "created"
        r_dup = http_client.post("/events", json=real_payload)
        assert r_dup.status_code == 200 and r_dup.json()["status"] == "duplicate"

        real_ref = fake_registry.refresh_in_memory_stream(real_sid, cutoff)
        assert real_ref["processed_events"] == 1

        r_rec = http_client.post(f"/streams/{real_sid}/recommend", json={"customer_id": "cust_0007", "k": 6})
        assert r_rec.status_code == 200
        r_rec_body = r_rec.json()
        for field in ("stream_id", "revision", "received_before", "published_at", "mode"):
            assert field in r_rec_body
        assert r_rec_body["stream_id"] == real_sid
        assert r_rec_body["revision"] == real_ref["revision"]


# ---------------------------------------------------------------------------
# A3. F_CAT — /catalog Search, Filter, Pagination & 10-Digit IDs (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
def test_t1_cat_default_and_disjoint_pagination(
    synthetic_h_release: Path,
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v2_dir, v2_id = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    http_client, h_id = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)

    with http_client:
        for client, expected_rid, is_real_app in ((adapter.client(), v2_id, False), (http_client, h_id, True)):
            default_res = client.get("/catalog")
            assert default_res.status_code == 200
            default_body = default_res.json()
            assert default_body["release_id"] == expected_rid
            assert default_body["total"] == 40
            assert len(default_body["items"]) == 12
            assert isinstance(default_body["filters"], dict)

            parquet_reads = 0
            orig_scan = pl.scan_parquet
            orig_read = pl.read_parquet

            def _counting_scan(*args: Any, **kwargs: Any):
                nonlocal parquet_reads
                parquet_reads += 1
                return orig_scan(*args, **kwargs)

            def _counting_read(*args: Any, **kwargs: Any):
                nonlocal parquet_reads
                parquet_reads += 1
                return orig_read(*args, **kwargs)

            with monkeypatch.context() as mp:
                if is_real_app:
                    mp.setattr(pl, "scan_parquet", _counting_scan)
                    mp.setattr(pl, "read_parquet", _counting_read)
                page1 = client.get("/catalog", params={"offset": 0, "limit": 12}).json()
                page2 = client.get("/catalog", params={"offset": 12, "limit": 12}).json()
            if is_real_app:
                assert parquet_reads == 0, (
                    f"Expected /catalog to serve subsequent pages from in-memory cache, got {parquet_reads} parquet reads"
                )
            ids1 = [it["article_id"] for it in page1["items"]]
            ids2 = [it["article_id"] for it in page2["items"]]
            assert len(ids1) == 12 and len(ids2) == 12
            assert set(ids1).isdisjoint(set(ids2))


@pytest.mark.tier1
def test_t1_cat_text_search_q(
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
            res_dress = client.get("/catalog", params={"q": "Dress", "limit": 48})
            assert res_dress.status_code == 200
            body_dress = res_dress.json()
            assert 0 < body_dress["total"] < 40
            assert len(body_dress["items"]) == body_dress["total"]
            assert all(
                any("dress" in str(v).lower() for v in item.values() if v is not None)
                for item in body_dress["items"]
            )

            res_id = client.get("/catalog", params={"q": "0000000005"})
            assert res_id.status_code == 200
            body_id = res_id.json()
            assert body_id["total"] == 1
            assert [it["article_id"] for it in body_id["items"]] == ["0000000005"]


@pytest.mark.tier1
def test_t1_cat_index_group_name_filter(
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
    h_client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)
    with h_client:
        h_res = h_client.get("/catalog", params={"index_group_name": "Ladieswear", "limit": 48})
        assert h_res.status_code == 200
        h_body = h_res.json()
        assert h_body["total"] == 0
        assert h_body["items"] == []

    v2_registry = FakeEventRegistry(tmp_path / "serving_registry_v2")
    http_client, _, _ = _prepare_and_activate_v2(v2_registry, v2_dir, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            res = client.get("/catalog", params={"index_group_name": "Ladieswear", "limit": 48})
            assert res.status_code == 200
            body = res.json()
            assert 0 < body["total"] < 40
            assert len(body["items"]) == body["total"]
            assert all(it["index_group_name"] == "Ladieswear" for it in body["items"])


@pytest.mark.tier1
def test_t1_cat_combined_q_and_index_group_name_paginated(
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
    h_client, _ = _prepare_and_activate_h(fake_registry, synthetic_h_release, tmp_path)
    with h_client:
        h_res = h_client.get(
            "/catalog",
            params={"q": "White", "index_group_name": "Menswear", "offset": 0, "limit": 4},
        )
        assert h_res.status_code == 200
        h_body = h_res.json()
        assert h_body["total"] == 0
        assert h_body["items"] == []

    v2_registry = FakeEventRegistry(tmp_path / "serving_registry_v2")
    http_client, _, _ = _prepare_and_activate_v2(v2_registry, v2_dir, tmp_path)

    with http_client:
        for client in (adapter.client(), http_client):
            res = client.get(
                "/catalog",
                params={"q": "White", "index_group_name": "Menswear", "offset": 0, "limit": 4},
            )
            assert res.status_code == 200
            body = res.json()
            assert body["total"] == 5
            assert len(body["items"]) == 4
            assert body["total"] > len(body["items"])
            for item in body["items"]:
                assert item["index_group_name"] == "Menswear"
                assert any("white" in str(v).lower() for v in item.values() if v is not None)

            page2 = client.get(
                "/catalog",
                params={"q": "White", "index_group_name": "Menswear", "offset": 4, "limit": 4},
            )
            assert page2.status_code == 200
            page2_body = page2.json()
            assert page2_body["total"] == 5
            assert len(page2_body["items"]) == 1
            assert page2_body["items"][0]["index_group_name"] == "Menswear"
            assert any("white" in str(v).lower() for v in page2_body["items"][0].values() if v is not None)
            page1_ids = {it["article_id"] for it in body["items"]}
            assert page2_body["items"][0]["article_id"] not in page1_ids


@pytest.mark.tier1
def test_t1_cat_10_digit_ids_preserved_and_articles_details(
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
            cat = client.get("/catalog", params={"offset": 0, "limit": 40})
            assert cat.status_code == 200
            aids = [it["article_id"] for it in cat.json()["items"]]
            assert "0000000000" in aids and "0000000001" in aids
            assert all(re.fullmatch(r"^[0-9]{10}$", aid) for aid in aids)

            det = client.post("/articles/details", json={"article_ids": ["0000000000", "0000000001"]})
            assert det.status_code == 200
            det_items = det.json()["items"]
            assert [it["article_id"] for it in det_items] == ["0000000000", "0000000001"]
            assert all("product_type_name" in it and "colour_group_name" in it for it in det_items)


# ---------------------------------------------------------------------------
# A4. F_IMG — ImageCache O(1) & /articles/{id}/image (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
def test_t1_img_cache_extracts_valid_jpeg_and_o1_getinfo_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article_ids = [f"{i:010d}" for i in range(1000)]
    zip_path = build_synthetic_image_zip(tmp_path / "large_imgs.zip", article_ids)
    cache_dir = tmp_path / "img_cache"
    cache = ImageCache(zip_path, cache_dir)

    body1, mime1 = cache.read("0000000005")
    assert mime1 == "image/jpeg"
    assert body1.startswith(b"\xff\xd8") and body1.endswith(b"\xff\xd9")
    assert len(list(cache_dir.glob("*.jpg"))) == 1

    # Second read hits disk cache without opening ZIP
    def _fail_zip(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Expected disk cache hit without reopening ZIP")

    with monkeypatch.context() as mp:
        mp.setattr(zipfile, "ZipFile", _fail_zip)
        body2, mime2 = cache.read("0000000005")
    assert mime2 == "image/jpeg"
    assert body2 == body1

    # O(1) lookup check on cache miss: ZipFile.getinfo() must be used instead of linear infolist() scan
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
        body3, mime3 = cache.read("0000000500")
    assert mime3 == "image/jpeg"
    assert body3.startswith(b"\xff\xd8") and body3.endswith(b"\xff\xd9")
    assert infolist_calls == 0, f"Expected O(1) lookup without infolist() scan, saw {infolist_calls} calls"
    assert getinfo_calls >= 1, "Expected ZipFile.getinfo() to be called on cache miss"


@pytest.mark.tier1
def test_t1_img_10_digit_leading_zero_preservation(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    with zipfile.ZipFile(synthetic_image_zip) as zf:
        expected_bytes = zf.read("images/000/0000000000.jpg")

    cache = ImageCache(synthetic_image_zip, e2e_image_cache_dir / "direct")
    cached_bytes, cached_mime = cache.read("0000000000")
    assert cached_mime == "image/jpeg"
    assert cached_bytes == expected_bytes

    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir / "adapter",
    )
    res = adapter.client().get("/articles/0000000000/image")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == expected_bytes


@pytest.mark.tier1
def test_t1_img_http_endpoint_serves_jpeg_from_real_app(
    synthetic_h_release: Path,
    synthetic_image_zip: Path,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with zipfile.ZipFile(synthetic_image_zip) as zf:
        expected_bytes = zf.read("images/000/0000000003.jpg")

    client, _ = _prepare_and_activate_h(
        fake_registry,
        synthetic_h_release,
        tmp_path,
        image_zip=synthetic_image_zip,
        monkeypatch=monkeypatch,
    )
    with client:
        res = client.get("/articles/0000000003/image")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.content == expected_bytes


@pytest.mark.tier1
def test_t1_img_lru_eviction_preserves_recently_accessed(tmp_path: Path) -> None:
    zip_path = build_synthetic_image_zip(
        tmp_path / "lru_imgs.zip",
        ["0000000000", "0000000001", "0000000002"],
    )
    cache_dir = tmp_path / "lru_cache"
    cache = ImageCache(zip_path, cache_dir, max_bytes=1500)

    assert cache.read("0000000000")[1] == "image/jpeg"
    time_mod.sleep(0.02)
    assert cache.read("0000000001")[1] == "image/jpeg"
    time_mod.sleep(0.02)

    # Refresh 0000000000 so 0000000001 becomes the LRU victim
    assert cache.read("0000000000")[1] == "image/jpeg"
    time_mod.sleep(0.02)
    assert cache.read("0000000002")[1] == "image/jpeg"

    jpg_files = list(cache_dir.glob("*.jpg"))
    assert len(jpg_files) == 2
    assert sum(p.stat().st_size for p in jpg_files) <= 1500

    # Verify 0000000000 survived in cache while 0000000001 was evicted
    assert len({p.name for p in jpg_files}) == 2
    stat_zip = zip_path.stat()
    import hashlib

    def _key_for(aid: str) -> str:
        return hashlib.sha256(
            f"{zip_path.resolve()}:{stat_zip.st_size}:{stat_zip.st_mtime_ns}:{aid}".encode()
        ).hexdigest() + ".jpg"

    assert (cache_dir / _key_for("0000000000")).is_file()
    assert (cache_dir / _key_for("0000000002")).is_file()
    assert not (cache_dir / _key_for("0000000001")).exists()


@pytest.mark.tier1
def test_t1_img_missing_member_and_none_archive_svg_fallback(
    synthetic_v2_release: tuple[Path, str],
    tmp_path: Path,
) -> None:
    none_cache = ImageCache(None, tmp_path / "none_cache")
    body, mime = none_cache.read("0000000000")
    assert mime == "image/svg+xml"
    assert body == PLACEHOLDER
    assert b"<svg " in body

    # Catalog has 0000000000..0000000039, but ZIP omits 0000000039
    partial_zip = build_synthetic_image_zip(
        tmp_path / "partial.zip",
        [f"{i:010d}" for i in range(39)],
    )
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=partial_zip,
        image_cache_dir=tmp_path / "adapter_cache",
    )
    res = adapter.client().get("/articles/0000000039/image")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/svg+xml"
    assert res.content == PLACEHOLDER
