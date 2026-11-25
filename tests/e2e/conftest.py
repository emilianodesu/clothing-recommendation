"""Shared E2E pytest marker registration, gating, and synthetic/Postgres fixtures.

IMPORTANT: Zero top-level ``import hm_recommender.v2...`` statements allowed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
import uuid

from fastapi.testclient import TestClient
import httpx
import pytest

from hm_recommender.api.app import create_app
from tests.e2e.harness import (
    MIGRATIONS_DIR,
    REPO_ROOT,
    FakeEventRegistry,
    InMemoryApiAdapter,
    build_gateway_config,
    build_synthetic_h_release,
    build_synthetic_image_zip,
    build_synthetic_v2_release,
    start_uvicorn_app,
)


def pytest_configure(config: pytest.Config) -> None:
    """Register E2E tier and environment markers without modifying pyproject.toml."""
    for spec in (
        "tier1: Tier 1 isolated happy-path E2E tests",
        "tier2: Tier 2 boundary and corner-case E2E tests",
        "tier3: Tier 3 cross-feature pairwise combination E2E tests",
        "tier4: Tier 4 real-world shopper and operator E2E scenarios",
        "selfcheck: E2E harness and h-* baseline sanity checks",
        "requires_postgres: E2E tests requiring live PostgreSQL (HMREC_SERVING_TESTS=1)",
    ):
        config.addinivalue_line("markers", spec)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip Postgres-backed tests cleanly unless HMREC_SERVING_TESTS=1."""
    if os.environ.get("HMREC_SERVING_TESTS") == "1":
        return
    skip_pg = pytest.mark.skip(reason="requires isolated PostgreSQL (HMREC_SERVING_TESTS=1)")
    for item in items:
        if "requires_postgres" in item.keywords:
            item.add_marker(skip_pg)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def e2e_image_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache_dir = tmp_path / "image_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HMREC_IMAGE_CACHE", str(cache_dir))
    return cache_dir


@pytest.fixture
def source_commit_env(monkeypatch: pytest.MonkeyPatch) -> str:
    commit = "a" * 40
    monkeypatch.setenv("HMREC_SOURCE_COMMIT", commit)
    return commit


@pytest.fixture
def synthetic_h_release(tmp_path: Path, source_commit_env: str) -> Path:
    return build_synthetic_h_release(tmp_path / "h_release")


@pytest.fixture
def synthetic_v2_release(tmp_path: Path, source_commit_env: str) -> tuple[Path, str]:
    return build_synthetic_v2_release(tmp_path / "v2_release")


@pytest.fixture
def synthetic_image_zip(tmp_path: Path) -> Path:
    article_ids = [f"{i:010d}" for i in range(40)]
    return build_synthetic_image_zip(tmp_path / "images.zip", article_ids)


@pytest.fixture
def fake_registry(tmp_path: Path, e2e_image_cache_dir: Path) -> FakeEventRegistry:
    return FakeEventRegistry(tmp_path / "serving_registry")


@pytest.fixture
def in_memory_api(
    synthetic_h_release: Path,
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> InMemoryApiAdapter:
    return InMemoryApiAdapter(
        synthetic_h_release,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
        admin_token="local-test-token",
    )


@pytest.fixture
def fastapi_client(fake_registry: FakeEventRegistry, tmp_path: Path):
    input_root = tmp_path / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(input_root))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def uvicorn_server(fake_registry: FakeEventRegistry, tmp_path: Path):
    input_root = tmp_path / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    app = create_app(fake_registry, admin_token="local-test-token", input_root=str(input_root))
    with start_uvicorn_app(app, lifespan="on") as base_url:
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            yield base_url, client


@pytest.fixture
def gateway_config_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _build(
        users: dict[str, str],
        *,
        domain: str = "hm.test",
        hash_mode: Literal["auto", "sha256", "scrypt"] = "auto",
        filename: str = "gateway.json",
    ) -> Path:
        cfg = build_gateway_config(
            tmp_path,
            users,
            domain=domain,
            hash_mode=hash_mode,
            filename=filename,
        )
        monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(cfg))
        return cfg

    return _build


@pytest.fixture
def postgres_serving_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if os.environ.get("HMREC_SERVING_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL (HMREC_SERVING_TESTS=1)")

    from hm_recommender import database
    from hm_recommender.releases.registry import ReleaseRegistry

    schema = f"test_e2e_{uuid.uuid4().hex}"
    with database.connect() as conn:
        conn.execute(f"CREATE SCHEMA {schema}")

    def connection():
        conn = database.connect()
        conn.execute(f"SET search_path TO {schema}")
        conn.commit()
        return conn

    try:
        with connection() as conn:
            for name in ("002_releases.sql", "003_events.sql", "005_v2_releases.sql"):
                sql_path = MIGRATIONS_DIR / name
                if sql_path.is_file():
                    conn.execute(sql_path.read_text(encoding="utf-8"))

        input_root = tmp_path / "input"
        input_root.mkdir(parents=True, exist_ok=True)
        image_cache = tmp_path / "image_cache"
        image_cache.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HMREC_IMAGE_CACHE", str(image_cache))
        registry = ReleaseRegistry(tmp_path / "registry", connection)
        app = create_app(registry, admin_token="local-test-token", input_root=str(input_root))
        with TestClient(app) as client:
            yield client, registry, connection, schema, tmp_path
    finally:
        with database.connect() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
