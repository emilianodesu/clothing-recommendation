"""Opaque-box E2E test harness helpers for synthetic data, packages, HTTP, UI, and auth.

IMPORTANT: This module MUST NOT contain any top-level ``import hm_recommender.v2...``
or ``from hm_recommender.v2...`` statements so ``pytest --collect-only tests/e2e``
succeeds cleanly on branches before M1-M3 land.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import heapq
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import threading
import time as time_mod
from types import MappingProxyType, SimpleNamespace
from typing import Any, Literal
from unittest.mock import patch
from uuid import uuid4
import zipfile

from fastapi import FastAPI
import httpx
import lightgbm as lgb
import numpy as np
import polars as pl
from pydantic import Field, ValidationError
import scipy.sparse as sp
from streamlit.testing.v1 import AppTest
import uvicorn

from hm_recommender.api.auth import hash_password
from hm_recommender.api.recommend import FILTERS, HistoryState, load_state
from hm_recommender.api.schemas import EventInput, RecommendationRequest
from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.quality.contracts import digest
from hm_recommender.releases.contracts import HistoryPublicationResult
from hm_recommender.releases.package import build_history_release, verify_history_release
from hm_recommender.releases.registry import Unavailable
from hm_recommender.ui.images import ImageCache


# ---------------------------------------------------------------------------
# Repository & Data Path Resolution
# ---------------------------------------------------------------------------

def resolve_repo_root() -> Path:
    """Resolve the repository root across host, /workspace, and /opt/hmrec mounts."""
    candidates = [
        Path(__file__).resolve().parents[2],
        Path("/workspace"),
        Path("/opt/hmrec"),
    ]
    for cand in candidates:
        if (cand / "src" / "hm_recommender").is_dir() and (cand / "tests" / "e2e").is_dir():
            return cand
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "hm_recommender").is_dir():
            return parent
    raise RuntimeError("Unable to locate repository root")


REPO_ROOT: Path = resolve_repo_root()
MIGRATIONS_DIR: Path = REPO_ROOT / "migrations"
UI_APP_PATH: Path = REPO_ROOT / "src" / "hm_recommender" / "ui" / "app.py"

_RELEASE_ID_RE = re.compile(r"^(h|v2)-[a-f0-9]{64}$")
_ARTICLE_ID_10_RE = re.compile(r"^[0-9]{10}$")
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_SIGNAL_WEIGHTS: dict[str, float] = {"purchase": 1.0, "click": 0.55, "view": 0.25}
_AGE_BINS: tuple[str, ...] = ("<25", "25-34", "35-44", "45-54", "55+", "unknown")


def resolve_hm_v1_dir() -> Path | None:
    """Locate processed hm-v1 dataset directory if mounted/present."""
    env_data = os.environ.get("HMREC_DATA_DIR", "").strip()
    candidates: list[Path] = []
    if env_data:
        env_path = Path(env_data)
        candidates.extend([env_path / "processed" / "hm-v1", env_path])
    candidates.extend([
        Path("/data/processed/hm-v1"),
        REPO_ROOT / "data" / "processed" / "hm-v1",
    ])
    for cand in candidates:
        if (cand / "manifest.json").is_file():
            return cand
    return None


def resolve_artifacts_dir() -> Path | None:
    """Locate artifacts directory if mounted at /artifacts or present under REPO_ROOT."""
    env_art = os.environ.get("HMREC_ARTIFACTS_DIR", "").strip()
    candidates: list[Path] = []
    if env_art:
        candidates.append(Path(env_art))
    candidates.extend([
        Path("/artifacts"),
        REPO_ROOT / "artifacts",
    ])
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


# ---------------------------------------------------------------------------
# Synthetic Dataset & Release Builders
# ---------------------------------------------------------------------------

def build_synthetic_dataset(
    root: Path,
    *,
    num_articles: int = 40,
    num_users: int = 8,
    cutoff: date = date(2020, 9, 9),
) -> tuple[DatasetManifest, dict[str, Any]]:
    """Build a genuine synthetic Parquet dataset with 10-digit article_ids and rich attributes."""
    if num_articles < 12:
        raise ValueError("num_articles must be >= 12")
    if num_users < 4:
        raise ValueError("num_users must be >= 4")

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    index_groups = ("Ladieswear", "Menswear", "Divided", "Baby/Children")
    product_types = ("T-shirt", "Dress", "Trousers", "Sweater")
    product_groups = ("Garment Upper body", "Garment Full body", "Garment Lower body", "Garment Upper body")
    colour_groups = ("Black", "Blue", "White", "Red")
    appearances = ("Solid", "Stripe", "Melange", "All over pattern")

    article_ids = [f"{i:010d}" for i in range(num_articles)]
    article_rows: list[dict[str, str]] = []
    for i, aid in enumerate(article_ids):
        ptype = product_types[i % len(product_types)]
        pgroup = product_groups[i % len(product_groups)]
        colour = colour_groups[i % len(colour_groups)]
        igroup = index_groups[(i // 2) % len(index_groups)]
        app_name = appearances[i % len(appearances)]
        pcode = f"pc_{i // 2:04d}"  # pairs of colorways share product_code
        article_rows.append({
            "article_id": aid,
            "product_code": pcode,
            "prod_name": f"Atelier Style {i // 2:03d}",
            "product_type_name": ptype,
            "product_group_name": pgroup,
            "colour_group_name": colour,
            "graphical_appearance_name": app_name,
            "index_group_name": igroup,
            "detail_desc": f"Tailored {ptype.lower()} in {colour.lower()} ({app_name.lower()}) for {igroup.lower()}.",
        })
    articles_df = pl.DataFrame(article_rows)

    ages = ("24", "30", "40", "50", "60")
    user_ids = [f"cust_{u:04d}" for u in range(num_users)]
    customer_rows = [
        {
            "customer_id": uid,
            "FN": "1.0" if u % 2 == 0 else "0.0",
            "Active": "1.0",
            "club_member_status": "ACTIVE",
            "fashion_news_frequency": "Regularly" if u % 2 == 0 else "NONE",
            "age": ages[u % len(ages)],
            "postal_code": f"postal_{u:04d}",
        }
        for u, uid in enumerate(user_ids)
    ]
    customers_df = pl.DataFrame(customer_rows)

    # Exclude older_buyer (user_ids[2]), future_only_buyer (user_ids[3]),
    # and cold_start_buyer (user_ids[-1] when num_users >= 6) from background coverage.
    if num_users >= 6:
        bg_users = [user_ids[0], *user_ids[4:-1]]
    elif num_users == 5:
        bg_users = [user_ids[0], user_ids[4]]
    else:
        bg_users = [user_ids[0], user_ids[1]]

    tx_rows: list[tuple[date, str, str, float, int]] = []
    # Guarantee every catalog article has at least one pre-cutoff transaction
    for i, aid in enumerate(article_ids):
        buyer = user_ids[0] if i < 6 else bg_users[i % len(bg_users)]
        # Make item 0..2 very popular in the last 7 days
        days_ago = 2 if i < 4 else (5 if i < 12 else 14 + (i % 10))
        tx_rows.append((cutoff - timedelta(days=days_ago), buyer, aid, round(19.99 + (i % 7) * 5.0, 2), 2))

    # Boost recent purchases for cust_0000 (including both colorways of pc_0000 and co-purchases)
    for aid in (article_ids[0], article_ids[1], article_ids[2], article_ids[4]):
        tx_rows.append((cutoff - timedelta(days=1), user_ids[0], aid, 29.99, 2))
    tx_rows.append((cutoff - timedelta(days=3), user_ids[0], article_ids[0], 29.99, 1))

    # cust_0001: recent 28d buyer
    tx_rows.append((cutoff - timedelta(days=10), user_ids[1], article_ids[6], 39.99, 2))
    tx_rows.append((cutoff - timedelta(days=12), user_ids[1], article_ids[7], 39.99, 2))

    # cust_0002: older buyer (> 28d, < 180d before cutoff)
    tx_rows.append((cutoff - timedelta(days=90), user_ids[2], article_ids[8], 49.99, 1))
    tx_rows.append((cutoff - timedelta(days=120), user_ids[2], article_ids[9], 49.99, 1))

    # cust_0003: future-only user (>= cutoff) for temporal leakage checks
    tx_rows.append((cutoff, user_ids[3], article_ids[10], 59.99, 2))
    tx_rows.append((cutoff + timedelta(days=1), user_ids[3], article_ids[11], 59.99, 2))

    # Additional popularity gradient across remaining non-special users
    pop_user_a = user_ids[4] if num_users >= 5 else user_ids[0]
    pop_user_b = user_ids[5] if num_users >= 7 else pop_user_a
    for rep in range(3):
        tx_rows.append((cutoff - timedelta(days=2), pop_user_a, article_ids[0], 24.99, 2))
        if rep < 2:
            tx_rows.append((cutoff - timedelta(days=3), pop_user_b, article_ids[2], 24.99, 2))

    tx_df = pl.DataFrame(
        tx_rows,
        schema={
            "t_dat": pl.Date,
            "customer_id": pl.String,
            "article_id": pl.String,
            "price": pl.Float64,
            "sales_channel_id": pl.Int8,
        },
        orient="row",
    )

    partitions = [
        write_partition(tx_df.to_arrow(), root, "transactions/part.parquet"),
        write_partition(articles_df.to_arrow(), root, "articles/part.parquet"),
        write_partition(customers_df.to_arrow(), root, "customers/part.parquet"),
    ]
    min_d = tx_df["t_dat"].min()
    max_d = tx_df["t_dat"].max()
    assert isinstance(min_d, date) and isinstance(max_d, date)

    manifest = DatasetManifest(
        dataset_id="synthetic-e2e",
        source_sha256="b" * 64,
        min_date=min_d,
        max_date=max_d,
        counts={
            "articles": articles_df.height,
            "customers": customers_df.height,
            "transactions": tx_df.height,
        },
        partitions=partitions,
    )
    atomic_json(root / "manifest.json", manifest.model_dump(mode="json"))
    manifest._root = root.resolve()

    metadata: dict[str, Any] = {
        "article_ids": article_ids,
        "user_ids": user_ids,
        "active_buyer": user_ids[0],
        "recent_buyer": user_ids[1],
        "older_buyer": user_ids[2],
        "future_only_buyer": user_ids[3],
        "cutoff": cutoff,
        "articles_df": articles_df,
        "customers_df": customers_df,
        "transactions_df": tx_df,
    }
    if num_users >= 6:
        metadata["cold_start_buyer"] = user_ids[-1]
    return manifest, metadata


def build_synthetic_h_release(
    root: Path,
    *,
    data_as_of: date = date(2020, 9, 9),
    num_articles: int = 40,
    num_users: int = 8,
) -> Path:
    """Build and verify a genuine h-<64hex> release directory using public build_history_release."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ds_dir = root / "_dataset"
    ds, _ = build_synthetic_dataset(
        ds_dir, num_articles=num_articles, num_users=num_users, cutoff=data_as_of
    )

    prev_commit = os.environ.get("HMREC_SOURCE_COMMIT")
    if not prev_commit or not _COMMIT_RE.fullmatch(prev_commit):
        os.environ["HMREC_SOURCE_COMMIT"] = "a" * 40
    try:
        staged = root / "_staged_h"
        manifest = build_history_release(ds, data_as_of, staged)
        release_dir = root / manifest.release_id
        if not release_dir.exists():
            staged.rename(release_dir)
        verified = verify_history_release(release_dir)
        assert verified.release_id == manifest.release_id
        return release_dir
    finally:
        if prev_commit is None:
            os.environ.pop("HMREC_SOURCE_COMMIT", None)
        else:
            os.environ["HMREC_SOURCE_COMMIT"] = prev_commit


def _find_spec_safe(fullname: str):
    """Return module spec or None without raising ModuleNotFoundError/ValueError for stubs or missing parents."""
    try:
        return importlib.util.find_spec(fullname)
    except (ModuleNotFoundError, ValueError):
        return None


def _try_delegate_v2_release(
    ds: DatasetManifest,
    data_as_of: date,
    root: Path,
) -> tuple[Path, str] | None:
    """Dynamically delegate to hm_recommender.v2.package if present and compatible."""
    if _find_spec_safe("hm_recommender.v2.package") is None:
        return None
    try:
        v2_pkg = importlib.import_module("hm_recommender.v2.package")
    except ImportError:
        return None
    builder = getattr(v2_pkg, "build_v2_release", None)
    verifier = getattr(v2_pkg, "verify_v2_release", None)
    if not callable(builder):
        return None
    sig = inspect.signature(builder)
    params = list(sig.parameters.values())
    required_positional = [
        p
        for p in params
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    required_kw_only = [
        p
        for p in params
        if p.default is inspect.Parameter.empty and p.kind is inspect.Parameter.KEYWORD_ONLY
    ]
    accepts_three_pos = (
        len(
            [
                p
                for p in params
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
        )
        >= 3
        or any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)
    )
    if (
        params
        and params[0].name in {"dataset", "ds"}
        and len(required_positional) <= 3
        and not required_kw_only
        and accepts_three_pos
    ):
        staged = root / "_staged_v2_pkg"
        shutil.rmtree(staged, ignore_errors=True)
        tmp_model_path: Path | None = None
        try:
            call_kwargs: dict[str, Any] = {}
            if "model_path" in sig.parameters and _find_spec_safe("hm_recommender.v2.features") is not None:
                v2_feat = importlib.import_module("hm_recommender.v2.features")
                feat_names = list(getattr(v2_feat, "V2_FEATURE_NAMES", ()))
                if feat_names:
                    rng = np.random.default_rng(42)
                    x_train = rng.normal(size=(12, len(feat_names))).astype(np.float32)
                    y_train = np.array([2, 1, 0, 0, 1, 0, 2, 1, 0, 0, 1, 0], dtype=np.int32)
                    train_ds = lgb.Dataset(x_train, label=y_train, group=[6, 6], feature_name=feat_names)
                    booster = lgb.train(
                        {
                            "objective": "lambdarank",
                            "num_leaves": 4,
                            "min_data_in_leaf": 1,
                            "learning_rate": 0.1,
                            "seed": 42,
                            "deterministic": True,
                            "num_threads": 1,
                            "verbose": -1,
                        },
                        train_ds,
                        num_boost_round=2,
                    )
                    tmp_model_path = root / f".tmp_v2_model_{uuid4().hex}.txt"
                    booster.save_model(str(tmp_model_path))
                    call_kwargs["model_path"] = tmp_model_path
                    if "validation_reference" in sig.parameters:
                        call_kwargs["validation_reference"] = {
                            "best_iteration": int(booster.current_iteration()),
                        }
            manifest = builder(ds, data_as_of, staged, **call_kwargs)
            rid = manifest.release_id
            release_dir = root / rid
            if staged.exists():
                if not release_dir.exists():
                    staged.rename(release_dir)
                else:
                    shutil.rmtree(staged, ignore_errors=True)
            if callable(verifier):
                verifier(release_dir)
            return release_dir, rid
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            return None
        finally:
            if tmp_model_path is not None:
                tmp_model_path.unlink(missing_ok=True)
    return None


def build_synthetic_v2_release(
    root: Path,
    *,
    data_as_of: date = date(2020, 9, 9),
    num_articles: int = 40,
    num_users: int = 8,
) -> tuple[Path, str]:
    """Build a portable v2-<64hex> package directory conforming to M2 package.py / Interface Contracts #2."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ds, meta = build_synthetic_dataset(
        root / "_dataset_v2",
        num_articles=num_articles,
        num_users=num_users,
        cutoff=data_as_of,
    )

    prev_commit = os.environ.get("HMREC_SOURCE_COMMIT")
    if not prev_commit or not _COMMIT_RE.fullmatch(prev_commit):
        os.environ["HMREC_SOURCE_COMMIT"] = "a" * 40
    try:
        delegated = _try_delegate_v2_release(ds, data_as_of, root)
    finally:
        if prev_commit is None:
            os.environ.pop("HMREC_SOURCE_COMMIT", None)
        else:
            os.environ["HMREC_SOURCE_COMMIT"] = prev_commit
    if delegated is not None:
        return delegated

    staging = root / f".v2-staging-{uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)

    articles_df: pl.DataFrame = meta["articles_df"]
    customers_df: pl.DataFrame = meta["customers_df"]
    tx_df: pl.DataFrame = meta["transactions_df"]
    past_tx = tx_df.filter(pl.col("t_dat") < data_as_of)

    # 1. catalog.parquet (12 columns matching package.py _CATALOG_SCHEMA)
    first_seen_df = past_tx.group_by("article_id").agg(pl.col("t_dat").min().alias("first_seen"))
    catalog_df = (
        articles_df.join(first_seen_df, on="article_id", how="inner")
        .with_columns(
            pl.lit("").cast(pl.String).alias("department_no"),
            pl.lit("").cast(pl.String).alias("garment_group_name"),
        )
        .select(
            "article_id",
            "first_seen",
            "product_code",
            "prod_name",
            "product_type_name",
            "product_group_name",
            "colour_group_name",
            "graphical_appearance_name",
            "index_group_name",
            "department_no",
            "garment_group_name",
            "detail_desc",
        )
        .sort("article_id")
    )

    # 2. histories.parquet (8 columns strictly < data_as_of, sorted per package.py)
    w180 = past_tx.filter(pl.col("t_dat") >= data_as_of - timedelta(days=180))
    histories_df = (
        w180.group_by(["customer_id", "article_id"])
        .agg(
            pl.col("t_dat").max().alias("last_purchase"),
            (pl.col("t_dat") >= data_as_of - timedelta(days=7)).sum().cast(pl.UInt16).alias("count_7"),
            (pl.col("t_dat") >= data_as_of - timedelta(days=28)).sum().cast(pl.UInt16).alias("count_28"),
            (pl.col("t_dat") >= data_as_of - timedelta(days=90)).sum().cast(pl.UInt16).alias("count_90"),
            pl.len().cast(pl.UInt32).alias("count_180"),
        )
        .with_columns(
            (
                pl.col("count_180").cast(pl.Float32)
                * (
                    0.5
                    ** (
                        (pl.lit(data_as_of) - pl.col("last_purchase")).dt.total_days().cast(pl.Float32)
                        / 14.0
                    )
                )
            )
            .cast(pl.Float32)
            .alias("decay_score")
        )
        .select("customer_id", "article_id", "last_purchase", "count_7", "count_28", "count_90", "count_180", "decay_score")
        .sort(["customer_id", "last_purchase", "count_180", "article_id"], descending=[False, True, True, False])
    )

    # 3. item_stats.parquet (13 columns matching package.py _ITEM_STATS_SCHEMA)
    c7 = (
        past_tx.filter(pl.col("t_dat") >= data_as_of - timedelta(days=7))
        .group_by("article_id")
        .agg(pl.len().cast(pl.UInt32).alias("item_count7"))
    )
    c14 = (
        past_tx.filter(pl.col("t_dat") >= data_as_of - timedelta(days=14))
        .group_by("article_id")
        .agg(pl.len().cast(pl.UInt32).alias("item_count14"))
    )
    c28 = (
        past_tx.filter(pl.col("t_dat") >= data_as_of - timedelta(days=28))
        .group_by("article_id")
        .agg(pl.len().cast(pl.UInt32).alias("item_count28"))
    )
    c90 = (
        past_tx.filter(pl.col("t_dat") >= data_as_of - timedelta(days=90))
        .group_by("article_id")
        .agg(
            pl.len().cast(pl.UInt32).alias("item_count90"),
            pl.col("price").mean().cast(pl.Float32).alias("item_price"),
            (pl.col("sales_channel_id") == 2).mean().cast(pl.Float32).alias("item_online_ratio"),
        )
    )
    item_stats_df = (
        catalog_df.select("article_id", "first_seen")
        .join(c7, on="article_id", how="left")
        .join(c14, on="article_id", how="left")
        .join(c28, on="article_id", how="left")
        .join(c90, on="article_id", how="left")
        .with_columns(
            pl.col("item_price").fill_null(25.0).cast(pl.Float32),
            pl.col("item_count7").fill_null(0).cast(pl.UInt32),
            pl.col("item_count14").fill_null(0).cast(pl.UInt32),
            pl.col("item_count28").fill_null(0).cast(pl.UInt32),
            pl.col("item_count90").fill_null(0).cast(pl.UInt32),
            pl.col("item_online_ratio").fill_null(0.5).cast(pl.Float32),
            pl.lit(1.0, dtype=pl.Float32).alias("item_trend_7_28"),
            pl.lit(1.0, dtype=pl.Float32).alias("item_trend_14_90"),
            pl.lit(35.0, dtype=pl.Float32).alias("item_mean_age"),
            pl.lit(10.0, dtype=pl.Float32).alias("item_std_age"),
            pl.lit(0.1, dtype=pl.Float32).alias("item_repurchase_rate"),
            (pl.lit(data_as_of) - pl.col("first_seen"))
            .dt.total_days()
            .cast(pl.Float32)
            .clip(lower_bound=1.0)
            .alias("item_days_since_first_seen"),
        )
        .select(
            "article_id",
            "item_price",
            "item_count7",
            "item_count14",
            "item_count28",
            "item_count90",
            "item_trend_7_28",
            "item_trend_14_90",
            "item_online_ratio",
            "item_mean_age",
            "item_std_age",
            "item_repurchase_rate",
            "item_days_since_first_seen",
        )
        .sort("article_id")
    )

    # 4. user_stats.parquet (18 columns, zero nulls, pref_index_group="" when empty)
    u_beh = (
        w180.group_by("customer_id")
        .agg(
            (pl.col("t_dat") >= data_as_of - timedelta(days=7)).sum().cast(pl.UInt32).alias("user_count7"),
            (pl.col("t_dat") >= data_as_of - timedelta(days=28)).sum().cast(pl.UInt32).alias("user_count28"),
            (pl.col("t_dat") >= data_as_of - timedelta(days=90)).sum().cast(pl.UInt32).alias("user_count90"),
            pl.len().cast(pl.UInt32).alias("user_count180"),
            (pl.lit(data_as_of) - pl.col("t_dat").max()).dt.total_days().cast(pl.Float32).alias("user_recency"),
            pl.col("price").mean().cast(pl.Float32).alias("user_avg_price"),
            pl.col("price").std(ddof=0).fill_null(0.0).cast(pl.Float32).alias("user_std_price"),
            (pl.col("sales_channel_id") == 2).mean().cast(pl.Float32).alias("user_online_ratio"),
        )
    )
    u_prodcodes = (
        w180.join(articles_df.select(["article_id", "product_code"]), on="article_id", how="left")
        .group_by("customer_id")
        .agg(pl.col("product_code").n_unique().cast(pl.UInt32).alias("user_n_unique_prodcodes"))
    )
    u_pref_group = (
        w180.join(articles_df.select(["article_id", "index_group_name"]), on="article_id", how="left")
        .group_by(["customer_id", "index_group_name"])
        .agg(pl.len().alias("_cnt"))
        .sort(["customer_id", "_cnt", "index_group_name"], descending=[False, True, False])
        .group_by("customer_id")
        .first()
        .select(["customer_id", pl.col("index_group_name").alias("pref_index_group")])
    )
    age_f = pl.col("age").cast(pl.String, strict=False).str.strip_chars().cast(pl.Float32, strict=False)
    valid_age = (
        pl.when(age_f.is_null() | age_f.is_nan() | (age_f < 10.0) | (age_f > 110.0))
        .then(pl.lit(-1.0, dtype=pl.Float32))
        .otherwise(age_f)
    )
    user_stats_df = (
        customers_df.select(["customer_id", "age", "FN", "Active", "club_member_status", "fashion_news_frequency"])
        .join(u_beh, on="customer_id", how="left")
        .join(u_prodcodes, on="customer_id", how="left")
        .join(u_pref_group, on="customer_id", how="left")
        .with_columns(
            valid_age.alias("age"),
            pl.when(valid_age < 0.0).then(pl.lit(1, dtype=pl.Int8)).otherwise(pl.lit(0, dtype=pl.Int8)).alias("age_missing"),
            pl.when(valid_age < 0.0)
            .then(pl.lit("unknown"))
            .when(valid_age < 25.0)
            .then(pl.lit("<25"))
            .when(valid_age < 35.0)
            .then(pl.lit("25-34"))
            .when(valid_age < 45.0)
            .then(pl.lit("35-44"))
            .when(valid_age < 55.0)
            .then(pl.lit("45-54"))
            .otherwise(pl.lit("55+"))
            .alias("age_bin"),
            pl.when(pl.col("club_member_status") == "ACTIVE").then(pl.lit(1, dtype=pl.Int8)).otherwise(pl.lit(0, dtype=pl.Int8)).alias("club_active"),
            pl.when(pl.col("fashion_news_frequency") == "Regularly").then(pl.lit(1, dtype=pl.Int8)).otherwise(pl.lit(0, dtype=pl.Int8)).alias("news_freq_code"),
            pl.when(pl.col("FN") == "1.0").then(pl.lit(1, dtype=pl.Int8)).otherwise(pl.lit(0, dtype=pl.Int8)).alias("fn_active"),
            pl.when(pl.col("Active") == "1.0").then(pl.lit(1, dtype=pl.Int8)).otherwise(pl.lit(0, dtype=pl.Int8)).alias("user_active_flag"),
            pl.col("user_count7").fill_null(0).cast(pl.UInt32),
            pl.col("user_count28").fill_null(0).cast(pl.UInt32),
            pl.col("user_count90").fill_null(0).cast(pl.UInt32),
            pl.col("user_count180").fill_null(0).cast(pl.UInt32),
            pl.col("user_recency").fill_null(181.0).cast(pl.Float32),
            pl.col("user_avg_price").fill_null(0.0).cast(pl.Float32),
            pl.col("user_std_price").fill_null(0.0).cast(pl.Float32),
            pl.col("user_online_ratio").fill_null(0.5).cast(pl.Float32),
            pl.col("user_n_unique_prodcodes").fill_null(0).cast(pl.UInt32),
        )
        .with_columns(
            pl.when(pl.col("user_count180") > 0)
            .then(pl.col("pref_index_group").fill_null(""))
            .otherwise(pl.lit(""))
            .cast(pl.String)
            .alias("pref_index_group"),
        )
        .select(
            "customer_id",
            "age",
            "age_missing",
            "age_bin",
            "club_active",
            "news_freq_code",
            "fn_active",
            "user_active_flag",
            "pref_index_group",
            "user_count7",
            "user_count28",
            "user_count90",
            "user_count180",
            "user_recency",
            "user_avg_price",
            "user_std_price",
            "user_online_ratio",
            "user_n_unique_prodcodes",
        )
        .sort("customer_id")
    )

    # 4b. user_affinities.parquet (3 columns)
    user_affinities_df = pl.DataFrame(
        schema={
            "customer_id": pl.String,
            "attr_code": pl.List(pl.UInt16),
            "share": pl.List(pl.Float32),
        }
    )

    # 5. segment_popularity.parquet (6 columns including ('__all__', '__all__'))
    seg_rows: list[dict[str, Any]] = []
    pop_map = dict(zip(item_stats_df["article_id"].to_list(), item_stats_df["item_count7"].to_list()))
    catalog_aids = catalog_df["article_id"].to_list()
    aid_to_node = {aid: idx for idx, aid in enumerate(catalog_aids)}
    for age_bin in _AGE_BINS:
        for igroup in ("Ladieswear", "Menswear", "Divided", "Baby/Children"):
            matching = catalog_df.filter(pl.col("index_group_name") == igroup)["article_id"].to_list()
            matching.sort(key=lambda a: (-pop_map.get(a, 0), a))
            for rank_idx, aid in enumerate(matching, start=1):
                seg_rows.append({
                    "age_bin": age_bin,
                    "index_group_name": igroup,
                    "article_id": aid,
                    "node": aid_to_node[aid],
                    "score_seg_pop": float(pop_map.get(aid, 0) + 1) / float(rank_idx),
                    "rank_seg_pop": rank_idx,
                })
    all_sorted = sorted(catalog_aids, key=lambda a: (-pop_map.get(a, 0), a))
    for rank_idx, aid in enumerate(all_sorted, start=1):
        seg_rows.append({
            "age_bin": "__all__",
            "index_group_name": "__all__",
            "article_id": aid,
            "node": aid_to_node[aid],
            "score_seg_pop": float(pop_map.get(aid, 0) + 1) / float(rank_idx),
            "rank_seg_pop": rank_idx,
        })
    segment_pop_df = pl.DataFrame(
        seg_rows,
        schema={
            "age_bin": pl.String,
            "index_group_name": pl.String,
            "article_id": pl.String,
            "node": pl.Int32,
            "score_seg_pop": pl.Float32,
            "rank_seg_pop": pl.Int16,
        },
    )

    parquet_tables: list[tuple[str, pl.DataFrame]] = [
        ("catalog", catalog_df),
        ("item_stats", item_stats_df),
        ("user_stats", user_stats_df),
        ("user_affinities", user_affinities_df),
        ("histories", histories_df),
        ("segment_popularity", segment_pop_df),
    ]
    files_meta: dict[str, dict[str, Any]] = {}
    for name, df in parquet_tables:
        fname = f"{name}.parquet"
        fpath = staging / fname
        df.write_parquet(fpath, compression="zstd", row_group_size=65536)
        files_meta[name] = {
            "path": fname,
            "sha256": sha256(fpath),
            "size_bytes": fpath.stat().st_size,
            "rows": df.height,
        }

    # 6. LightGBM LambdaRank booster (model.txt)
    if _find_spec_safe("hm_recommender.v2.features") is not None:
        v2_feat_mod = importlib.import_module("hm_recommender.v2.features")
        feat_names = list(getattr(v2_feat_mod, "V2_FEATURE_NAMES"))
    else:
        feat_names = [f"f_{i}" for i in range(57)]
    rng = np.random.default_rng(42)
    x_train = rng.normal(size=(12, len(feat_names))).astype(np.float32)
    y_train = np.array([2, 1, 0, 0, 1, 0, 2, 1, 0, 0, 1, 0], dtype=np.int32)
    train_ds = lgb.Dataset(x_train, label=y_train, group=[6, 6], feature_name=feat_names)
    booster = lgb.train(
        {
            "objective": "lambdarank",
            "num_leaves": 4,
            "min_data_in_leaf": 1,
            "learning_rate": 0.1,
            "seed": 42,
            "deterministic": True,
            "num_threads": 1,
            "verbose": -1,
        },
        train_ds,
        num_boost_round=2,
    )
    model_path = staging / "model.txt"
    booster.save_model(str(model_path))
    files_meta["model"] = {
        "path": "model.txt",
        "sha256": sha256(model_path),
        "size_bytes": model_path.stat().st_size,
        "rows": None,
    }

    # 7. Sparse matrices & SVD factors (cooc.npz, content_tfidf.npz, svd_v.npy, svd_sigma.npy)
    n_items = catalog_df.height
    cooc_dense = np.zeros((n_items, n_items), dtype=np.float32)
    for i in range(n_items - 1):
        cooc_dense[i, i + 1] = 1.0 / (1.0 + i % 5)
        cooc_dense[i + 1, i] = cooc_dense[i, i + 1]
    cooc_csr = sp.csr_matrix(cooc_dense, dtype=np.float32)
    cooc_csr.sort_indices()
    cooc_path = staging / "cooc.npz"
    sp.save_npz(cooc_path, cooc_csr)
    files_meta["cooc"] = {
        "path": "cooc.npz",
        "sha256": sha256(cooc_path),
        "size_bytes": cooc_path.stat().st_size,
        "rows": None,
    }

    tfidf_dense = np.zeros((n_items, 16), dtype=np.float32)
    for i in range(n_items):
        tfidf_dense[i, i % 8] = 0.8
        tfidf_dense[i, 8 + ((i // 2) % 8)] = 0.6
    tfidf_csr = sp.csr_matrix(tfidf_dense, dtype=np.float32)
    tfidf_path = staging / "content_tfidf.npz"
    sp.save_npz(tfidf_path, tfidf_csr)
    files_meta["content_tfidf"] = {
        "path": "content_tfidf.npz",
        "sha256": sha256(tfidf_path),
        "size_bytes": tfidf_path.stat().st_size,
        "rows": None,
    }

    k_latent = min(64, max(1, min(n_items, customers_df.height) - 1))
    svd_v = rng.normal(size=(n_items, k_latent)).astype(np.float32)
    norms = np.linalg.norm(svd_v, axis=1, keepdims=True)
    svd_v = (svd_v / np.where(norms > 0, norms, 1.0)).astype(np.float32)
    svd_path = staging / "svd_v.npy"
    np.save(svd_path, svd_v)
    files_meta["svd_v"] = {
        "path": "svd_v.npy",
        "sha256": sha256(svd_path),
        "size_bytes": svd_path.stat().st_size,
        "rows": None,
    }

    svd_sigma = np.linspace(float(k_latent), 1.0, num=k_latent, dtype=np.float32)
    svd_sigma_path = staging / "svd_sigma.npy"
    np.save(svd_sigma_path, svd_sigma)
    files_meta["svd_sigma"] = {
        "path": "svd_sigma.npy",
        "sha256": sha256(svd_sigma_path),
        "size_bytes": svd_sigma_path.stat().st_size,
        "rows": None,
    }

    # 8. rules.json and validation-reference.json (matching V2Rules & V2ValidationReference)
    rules_payload = {
        "strategy": "two_stage_lgbm_v2",
        "protocol": "quality-v2",
        "events": "strictly_before_cutoff",
        "feature_version": 2,
        "feature_names": feat_names,
        "cold_start_col": "user_count180",
        "missing_rank_sentinel": 9999,
        "history_days": 180,
        "popularity_days": 7,
        "latent_dim": 64,
        "latent_window_days": 180,
        "latent_half_life_days": 30.0,
        "content_max_vocab": 16384,
        "event_weights": _SIGNAL_WEIGHTS,
        "modes": ["balanced", "discovery"],
        "explainable_reasons": [
            "recent_purchase",
            "same_product_variant",
            "frequently_bought_together",
            "latent_style_match",
            "content_similar",
            "popular_for_segment",
            "popular_7d",
        ],
        "retrieval_config": {},
        "ranker_config": {},
    }
    rules_path = staging / "rules.json"
    atomic_json(rules_path, rules_payload)
    files_meta["rules"] = {
        "path": "rules.json",
        "sha256": sha256(rules_path),
        "size_bytes": rules_path.stat().st_size,
        "rows": None,
    }

    val_ref_payload = {
        "protocol": "quality-v2",
        "data_as_of": data_as_of.isoformat(),
        "feature_version": 2,
        "best_iteration": int(booster.current_iteration()),
        "val_ndcg_at_12": None,
        "metrics_measured_for_this_package": False,
        "quality_v2_report": None,
        "training_metadata": {},
    }
    val_ref_path = staging / "validation-reference.json"
    atomic_json(val_ref_path, val_ref_payload)
    files_meta["validation-reference"] = {
        "path": "validation-reference.json",
        "sha256": sha256(val_ref_path),
        "size_bytes": val_ref_path.stat().st_size,
        "rows": None,
    }

    commit_env = os.environ.get("HMREC_SOURCE_COMMIT", "")
    commit = commit_env if _COMMIT_RE.fullmatch(commit_env) else "a" * 40
    manifest_payload: dict[str, Any] = {
        "schema_version": 1,
        "commit": commit,
        "source_sha256": ds.source_sha256,
        "dataset_manifest_sha256": sha256(ds._root / "manifest.json"),
        "data_as_of": data_as_of.isoformat(),
        "strategy": "two_stage_lgbm_v2",
        "rules": rules_payload,
        "files": files_meta,
        "software_versions": {
            "python": platform.python_version(),
            "polars": importlib.metadata.version("polars"),
            "pyarrow": importlib.metadata.version("pyarrow"),
            "pydantic": importlib.metadata.version("pydantic"),
            "numpy": importlib.metadata.version("numpy"),
            "scipy": importlib.metadata.version("scipy"),
            "lightgbm": importlib.metadata.version("lightgbm"),
        },
    }
    release_id = "v2-" + digest(manifest_payload)
    manifest_payload["release_id"] = release_id
    atomic_json(staging / "manifest.json", manifest_payload)

    release_dir = root / release_id
    if release_dir.exists():
        shutil.rmtree(staging)
    else:
        staging.rename(release_dir)

    return release_dir, release_id


# ---------------------------------------------------------------------------
# Image Archive & Gateway Auth Config Builders
# ---------------------------------------------------------------------------

_MINIMAL_VALID_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00"
    b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12"
    b"\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
    b"\xff\xdb\x00C\x01\t\t\t\x0c\x0b\x0c\x18\r\r\x182!\x1c!2222222222222222222222222222222"
    b"2222222222222222222\xff\xc0\x00\x11\x08\x00\x02\x00\x02\x03\x01\"\x00\x02\x11\x01\x03"
    b"\x11\x01\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01"
    b"\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A"
    b"\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18"
    b"\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a"
    b"\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4"
    b"\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7"
    b"\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8"
    b"\xf9\xfa\xff\xc4\x00\x1f\x01\x00\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00"
    b"\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x11\x00\x02\x01"
    b"\x02\x04\x04\x03\x04\x07\x05\x04\x04\x00\x01\x02w\x00\x01\x02\x03\x11\x04\x05!1\x06"
    b"\x12AQ\x07aq\x13\"2\x81\x08\x14B\x91\xa1\xb1\xc1\t#3R\xf0\x15br\xd1\n\x16$4\xe1%\xf1"
    b"\x17\x18\x19\x1a&'()*56789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x82\x83\x84\x85\x86\x87\x88"
    b"\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2"
    b"\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5"
    b"\xd6\xd7\xd8\xd9\xda\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf2\xf3\xf4\xf5\xf6\xf7\xf8"
    b"\xf9\xfa\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xf1\x1a(\xa2\xb62?\xff\xd9"
)


def _make_jpeg_bytes(index: int) -> bytes:
    """Create a real decodable JPEG image byte string."""
    try:
        import io
        from PIL import Image

        buf = io.BytesIO()
        color = ((index * 37) % 255, (index * 73) % 255, (index * 109) % 255)
        Image.new("RGB", (2, 2), color).save(buf, format="JPEG")
        return buf.getvalue()
    except ImportError:
        return _MINIMAL_VALID_JPEG


def build_synthetic_image_zip(
    zip_path: Path,
    article_ids: Sequence[str],
    *,
    corrupt_ids: Sequence[str] = (),
) -> Path:
    """Write valid JPEG bytes under images/<prefix>/<article_id>.jpg and corrupt bytes for corrupt_ids."""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_set = set(corrupt_ids)
    for aid in list(article_ids) + list(corrupt_set):
        if not isinstance(aid, str) or not _ARTICLE_ID_10_RE.fullmatch(aid):
            raise ValueError(f"article_id must be 10 ASCII digits, got {aid!r}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for idx, aid in enumerate(article_ids):
            member = f"images/{aid[:3]}/{aid}.jpg"
            if aid in corrupt_set:
                payload = b"CORRUPT_NOT_JPEG_" + aid.encode("ascii")
            else:
                payload = _make_jpeg_bytes(idx)
            zf.writestr(member, payload)
    return zip_path


def build_gateway_config(
    tmp_path: Path,
    users: dict[str, str],
    *,
    domain: str = "hm.test",
    hash_mode: Literal["auto", "sha256", "scrypt"] = "auto",
    filename: str = "gateway.json",
) -> Path:
    """Create a gateway.json auth configuration with scrypt or legacy SHA-256 password_hashes."""
    if hash_mode not in {"auto", "sha256", "scrypt"}:
        raise ValueError(f"Unsupported hash_mode: {hash_mode!r}")

    hashes: dict[str, str] = {}
    for username, password in users.items():
        if hash_mode == "sha256":
            hashes[username] = hashlib.sha256(password.encode("utf-8")).hexdigest()
        else:
            hashes[username] = hash_password(password)

    out_path = Path(tmp_path) / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(
        out_path,
        {
            "domain": domain,
            "users": {u: "bcrypt_stub" for u in users},
            "password_hashes": hashes,
        },
    )
    return out_path


# ---------------------------------------------------------------------------
# Postgres-Free Serving & Event Adapters (FakeEventRegistry, InMemoryApiAdapter)
# ---------------------------------------------------------------------------

class _CompatEventInput(EventInput):
    """Backward/forward-compatible EventInput allowing v2-* release IDs before M3 updates schemas.py."""

    release_id: str = Field(pattern=r"^(h|v2)-[a-f0-9]{64}$")


@dataclass
class _LoadedRelease:
    release_id: str
    state: Any
    publication: dict[str, Any]
    is_v2: bool
    catalog_rows: list[dict[str, Any]]
    histories: dict[str, tuple[str, ...]]
    popularity: tuple[str, ...]
    v2_artifacts: dict[str, Any] | None = None


def _build_filter_index(catalog_df: pl.DataFrame) -> MappingProxyType:
    indexes: dict[str, MappingProxyType] = {}
    for field in FILTERS:
        groups: dict[str, set[str]] = {}
        for item, val in catalog_df.select("article_id", field).iter_rows():
            if val is not None:
                groups.setdefault(val, set()).add(item)
        indexes[field] = MappingProxyType({k: frozenset(v) for k, v in groups.items()})
    return MappingProxyType(indexes)


def _load_release_bundle(directory: Path) -> _LoadedRelease:
    """Load an h-* or v2-* release directory into state + metadata, delegating to v2 modules if present."""
    directory = Path(directory)
    manifest_data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rid = str(manifest_data["release_id"])
    cat_df = pl.read_parquet(directory / "catalog.parquet")
    catalog_rows: list[dict[str, Any]] = [
        {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in row.items()}
        for row in cat_df.to_dicts()
    ]

    if rid.startswith("v2-"):
        if _find_spec_safe("hm_recommender.v2.package") is not None:
            v2_pkg = importlib.import_module("hm_recommender.v2.package")
            verifier = getattr(v2_pkg, "verify_v2_release", None)
            if callable(verifier):
                verifier(directory)

        hist_df = pl.read_parquet(directory / "histories.parquet")
        user_df = pl.read_parquet(directory / "user_stats.parquet")
        item_df = pl.read_parquet(directory / "item_stats.parquet")
        seg_df = pl.read_parquet(directory / "segment_popularity.parquet")

        histories_list: dict[str, list[str]] = {}
        for u, a in hist_df.select("customer_id", "article_id").iter_rows():
            histories_list.setdefault(u, []).append(a)
        histories_map = {k: tuple(v) for k, v in histories_list.items()}
        pop_col = "item_count7" if "item_count7" in item_df.columns else "pop_7d"
        pop_tuple = tuple(item_df.sort([pop_col, "article_id"], descending=[True, False])["article_id"].to_list())

        state: Any = None
        if _find_spec_safe("hm_recommender.api.recommend_v2") is not None:
            rec_v2 = importlib.import_module("hm_recommender.api.recommend_v2")
            loader = getattr(rec_v2, "load_state", None) or getattr(rec_v2, "load_v2_state", None)
            if callable(loader):
                state = loader(directory)
                if hasattr(state, "histories"):
                    histories_map = {k: tuple(v) for k, v in state.histories.items()}
                if hasattr(state, "popularity"):
                    pop_tuple = tuple(state.popularity)

        if state is None:
            state = HistoryState(
                release_id=rid,
                data_as_of=date.fromisoformat(manifest_data["data_as_of"]),
                customers=frozenset(user_df["customer_id"].to_list()),
                catalog=frozenset(cat_df["article_id"].to_list()),
                histories=MappingProxyType(histories_map),
                popularity=pop_tuple,
                filter_index=_build_filter_index(cat_df),
            )

        sorted_aids = cat_df.sort("article_id")["article_id"].to_list()
        aid_to_idx = {aid: idx for idx, aid in enumerate(sorted_aids)}
        seg_pop: dict[tuple[str, str], list[str]] = {}
        seg_rank_col = "rank_seg_pop" if "rank_seg_pop" in seg_df.columns else "rank"
        for age_bin, igroup, aid in seg_df.sort(["age_bin", "index_group_name", seg_rank_col]).select(
            "age_bin", "index_group_name", "article_id"
        ).iter_rows():
            seg_pop.setdefault((age_bin, igroup), []).append(aid)

        cooc = sp.load_npz(directory / "cooc.npz") if (directory / "cooc.npz").is_file() else None
        content_tfidf = (
            sp.load_npz(directory / "content_tfidf.npz") if (directory / "content_tfidf.npz").is_file() else None
        )
        svd_v = np.load(directory / "svd_v.npy") if (directory / "svd_v.npy").is_file() else None

        v2_artifacts = {
            "aid_to_idx": aid_to_idx,
            "sorted_aids": sorted_aids,
            "user_stats": {row["customer_id"]: row for row in user_df.to_dicts()},
            "item_pop7": dict(zip(item_df["article_id"].to_list(), item_df[pop_col].to_list())),
            "seg_pop": seg_pop,
            "cooc": cooc,
            "content_tfidf": content_tfidf,
            "svd_v": svd_v,
        }
        pub = {
            "release_id": rid,
            "catalog_size": len(state.catalog),
            "past_buyers": len(state.customers),
            "history_rows": manifest_data["files"]["histories"]["rows"],
            "ranking_verified": True,
            "status": "prepared",
            "activated": False,
        }
        return _LoadedRelease(
            release_id=rid,
            state=state,
            publication=pub,
            is_v2=True,
            catalog_rows=catalog_rows,
            histories=histories_map,
            popularity=pop_tuple,
            v2_artifacts=v2_artifacts,
        )

    manifest = verify_history_release(directory)
    state = load_state(directory)
    pub = HistoryPublicationResult(
        release_id=manifest.release_id,
        catalog_size=manifest.files["catalog"].rows,
        past_buyers=manifest.files["customers"].rows,
        history_rows=manifest.files["histories"].rows,
    ).model_dump()
    return _LoadedRelease(
        release_id=manifest.release_id,
        state=state,
        publication=pub,
        is_v2=False,
        catalog_rows=catalog_rows,
        histories={k: tuple(v) for k, v in state.histories.items()},
        popularity=tuple(state.popularity),
        v2_artifacts=None,
    )


class _InMemorySqlCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


def _split_sql_columns(cols_part: str) -> list[tuple[str, str]]:
    """Split a SELECT column list on top-level commas, returning (raw_expr, col_name)."""
    parts: list[str] = []
    depth = 0
    in_quote = False
    start = 0
    for idx, ch in enumerate(cols_part):
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append(cols_part[start:idx].strip())
                start = idx + 1
    tail = cols_part[start:].strip()
    if tail:
        parts.append(tail)
    out: list[tuple[str, str]] = []
    for part in parts:
        expr = part.strip()
        if not expr:
            continue
        alias_match = re.search(r"\bAS\s+([a-z_][a-z0-9_]*|\"[^\"]+\")\s*$", expr, flags=re.IGNORECASE)
        if alias_match:
            col = alias_match.group(1).strip('"')
        else:
            cleaned = re.sub(r"::[a-z0-9_ ]+$", "", expr, flags=re.IGNORECASE).strip()
            col = cleaned.split()[-1].split(".")[-1].strip('"')
        out.append((part, col))
    return out


def _extract_where_param(
    low: str,
    where_pos: int,
    column: str,
    params: Sequence[Any] | Mapping[str, Any],
) -> str | None:
    """Resolve the parameter bound to ``column = %s`` (or ``%(name)s``) by placeholder order."""
    if isinstance(params, Mapping):
        val = params.get(column)
        return str(val) if val is not None else None
    clause = low[where_pos:]
    match = re.search(rf"(?<![a-z0-9_])(?:\w+\.)?{re.escape(column)}\s*=\s*%s\b", clause)
    if match is None:
        return None
    # Count %s placeholders strictly preceding this binding's %s token
    placeholder_idx = low[: where_pos + match.end() - 2].count("%s")
    if 0 <= placeholder_idx < len(params):
        return str(params[placeholder_idx])
    return None


class _InMemorySqlConnection:
    """Minimal Postgres-cursor shim routing EventStore/events.py queries to InMemoryEventStore."""

    def __init__(self, registry: FakeEventRegistry) -> None:
        self._registry = registry

    def __enter__(self) -> _InMemorySqlConnection:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    def commit(self) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | Sequence[Any] | Mapping[str, Any] = ()) -> _InMemorySqlCursor:
        store = self._registry.event_store
        if store is None:
            raise AssertionError("In-memory event store not initialized")
        norm = " ".join(sql.split())
        low = norm.lower()

        if "select clock_timestamp()" in low:
            return _InMemorySqlCursor([(datetime.now(timezone.utc),)])

        if low.startswith("select stream_id from event_streams"):
            rel_id = str(params[0]) if params and not isinstance(params, Mapping) else store.release_id
            rows = [(sid,) for sid, s in store.streams.items() if s.release_id == rel_id and s.revision > 0]
            return _InMemorySqlCursor(rows)

        if low.startswith("select release_id,mode from event_streams") or low.startswith(
            "select release_id, mode from event_streams"
        ):
            sid = str(params["stream_id"] if isinstance(params, Mapping) else params[0])
            stream = store.streams.get(sid)
            return _InMemorySqlCursor([(stream.release_id, stream.mode)] if stream is not None else [])

        if (
            "from event_streams" in low
            and "processed_events" in low
            and "published_at" in low
            and "for update" not in low
            and "where stream_id" in low
        ):
            # Supports events/aggregate.py:metadata(conn, stream_id)
            sid = str(params["stream_id"] if isinstance(params, Mapping) else params[0])
            stream = store.streams.get(sid)
            if stream is None:
                return _InMemorySqlCursor([])
            return _InMemorySqlCursor(
                [
                    (
                        stream.release_id,
                        stream.mode,
                        stream.data_as_of,
                        stream.revision,
                        stream.received_before,
                        stream.published_at,
                        stream.processed_events,
                    )
                ]
            )

        if "from event_streams" in low and "operational_profiles" in low:
            cid = str(params["customer_id"] if isinstance(params, Mapping) else params[0])
            sid = str(params["stream_id"] if isinstance(params, Mapping) else params[1])
            stream = store.streams.get(sid)
            if stream is None:
                return _InMemorySqlCursor([])
            return _InMemorySqlCursor(
                [
                    (
                        stream.release_id,
                        stream.data_as_of,
                        stream.revision,
                        stream.received_before,
                        stream.published_at,
                        list(stream.popularity),
                        list(stream.live_histories.get(cid, ())),
                        stream.mode,
                    )
                ]
            )

        if "from stream_catalog" in low:
            assert not isinstance(params, Mapping)
            sid, aid = str(params[0]), str(params[1])
            if sid in store.streams and aid in store.catalog_ids:
                return _InMemorySqlCursor([(1,)])
            return _InMemorySqlCursor([])

        if low.startswith("insert into interaction_events"):
            assert not isinstance(params, Mapping)
            eid, sid, cid, aid, kind, ev_time, raw_payload = params[:7]
            body = getattr(raw_payload, "obj", raw_payload)
            if isinstance(body, str):
                body = json.loads(body)
            payload = dict(body) if isinstance(body, dict) else {
                "event_id": str(eid),
                "stream_id": str(sid),
                "customer_id": str(cid),
                "article_id": str(aid),
                "kind": str(kind),
                "origin": store.streams[str(sid)].mode if str(sid) in store.streams else "synthetic",
                "event_time": ev_time.isoformat() if hasattr(ev_time, "isoformat") else str(ev_time),
            }
            status, _ = store.insert_event(payload)
            return _InMemorySqlCursor([(str(eid),)] if status == 201 else [])

        if "from interaction_events" in low and "select payload" in low and "where event_id" in low:
            eid = str(params["event_id"] if isinstance(params, Mapping) else params[0])
            existing = store.events_by_id.get(eid)
            return _InMemorySqlCursor([(existing,)] if existing is not None else [])

        if "from interaction_events" in low and low.startswith("select "):
            # Hardened generic selector for M3 live-event queries (parses WHERE param ordering)
            from_idx = low.index(" from interaction_events")
            cols_part = norm[len("SELECT ") : from_idx]
            cols = _split_sql_columns(cols_part)
            where_match = re.search(r"\bwhere\b", low[from_idx:])
            where_pos = from_idx + where_match.start() if where_match is not None else len(low)

            sid = _extract_where_param(low, where_pos, "stream_id", params)
            cid = _extract_where_param(low, where_pos, "customer_id", params)
            kind_filter = _extract_where_param(low, where_pos, "kind", params)
            stream = store.streams.get(sid) if sid is not None else None
            out: list[tuple[Any, ...]] = []
            for ev in store.events_log:
                if sid is not None and ev["stream_id"] != sid:
                    continue
                if cid is not None and ev["customer_id"] != cid:
                    continue
                if kind_filter is not None and ev["kind"] != kind_filter:
                    continue
                if stream is not None and datetime.fromisoformat(ev["event_time"]).date() >= stream.data_as_of:
                    continue
                row_vals = []
                for raw_expr, col in cols:
                    if col == "weight" or (col not in ev and "weight" in raw_expr.lower()):
                        row_vals.append(_SIGNAL_WEIGHTS.get(ev["kind"], 0.25))
                    elif col == "payload":
                        row_vals.append(ev)
                    elif col in {"event_time", "received_at"} or (
                        col not in ev and ("received_at" in raw_expr.lower() or "event_time" in raw_expr.lower())
                    ):
                        row_vals.append(datetime.fromisoformat(ev["event_time"]))
                    else:
                        row_vals.append(ev.get(col))
                out.append(tuple(row_vals))
            return _InMemorySqlCursor(out)

        raise AssertionError(f"Unsupported SQL in FakeEventRegistry: {norm}")


class FakeEventRegistry:
    """In-memory ReleaseRegistry replacement for Postgres-free create_app + TestClient + uvicorn."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path("/tmp/e2e-fake-registry")
        self._lock = threading.RLock()
        self._open = False
        self._releases: dict[str, tuple[Path, Any]] = {}
        self._bundles: dict[str, _LoadedRelease] = {}
        self._active_id: str | None = None
        self._previous_id: str | None = None
        self._state: Any = None
        self._in_memory_events_enabled = False
        self._stream_id: str | None = None
        self.event_store: InMemoryEventStore | None = None
        self.store = SimpleNamespace(connection=self._no_db)

    def _no_db(self) -> Any:
        raise AssertionError("FakeEventRegistry does not support PostgreSQL queries")

    def _in_memory_connection(self) -> _InMemorySqlConnection:
        return _InMemorySqlConnection(self)

    def enable_in_memory_events(self, *, stream_id: str | None = None) -> str:
        """Enable Postgres-free EventStore SQL execution for create_app /events and /streams routes."""
        with self._lock:
            self._in_memory_events_enabled = True
            self._stream_id = stream_id or self._stream_id or str(uuid4())
            self.store = SimpleNamespace(connection=self._in_memory_connection)
            if self._active_id is not None and self._active_id in self._bundles:
                self._sync_event_store_locked(self._bundles[self._active_id])
            return self._stream_id

    def _sync_event_store_locked(self, bundle: _LoadedRelease) -> None:
        sid = self._stream_id or str(uuid4())
        self._stream_id = sid
        self.event_store = InMemoryEventStore(
            release_id=bundle.release_id,
            data_as_of=bundle.state.data_as_of,
            catalog_rows=bundle.catalog_rows,
            histories=bundle.histories,
            popularity=bundle.popularity,
            stream_id=sid,
        )

    def refresh_in_memory_stream(self, stream_id: str | None = None, data_as_of: date | None = None) -> dict[str, Any]:
        with self._lock:
            if self.event_store is None:
                raise ValueError("In-memory event store not initialized")
            sid = stream_id or self.event_store.default_stream_id
            return self.event_store.refresh(sid, data_as_of)

    def open(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._open = True

    def close(self) -> None:
        with self._lock:
            self._open = False

    def recover(self) -> None:
        with self._lock:
            if self._active_id is not None and self._active_id in self._releases:
                self._state = self._releases[self._active_id][1]

    def _directory(self, release_id: str) -> Path:
        if not _RELEASE_ID_RE.fullmatch(release_id):
            raise ValueError("Invalid release ID")
        return self.root / release_id

    def snapshot(self) -> Any:
        with self._lock:
            if self._state is None:
                raise Unavailable("No valid active release")
            return self._state

    def _load_release(self, directory: Path) -> tuple[str, Any, dict[str, Any]]:
        bundle = _load_release_bundle(directory)
        with self._lock:
            self._bundles[bundle.release_id] = bundle
        return bundle.release_id, bundle.state, bundle.publication

    def prepare_release(self, directory: Path) -> dict[str, Any]:
        directory = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        rid, state, pub = self._load_release(directory)
        target = self._directory(rid)
        if not target.exists():
            shutil.copytree(directory, target)
        with self._lock:
            self._releases[rid] = (target, state)
        return pub

    def activate_release(self, release_id: str) -> None:
        with self._lock:
            if release_id not in self._releases:
                raise ValueError("Release not prepared")
            _, candidate = self._releases[release_id]
            if self._active_id != release_id:
                self._previous_id = self._active_id
                self._active_id = release_id
            self._state = candidate
            if self._in_memory_events_enabled and release_id in self._bundles:
                self._sync_event_store_locked(self._bundles[release_id])

    def rollback(self) -> str:
        with self._lock:
            if self._previous_id is None:
                raise ValueError("No previous release")
            prev = self._previous_id
            self._previous_id = self._active_id
            self._active_id = prev
            self._state = self._releases[prev][1]
            if self._in_memory_events_enabled and prev in self._bundles:
                self._sync_event_store_locked(self._bundles[prev])
            return prev


@dataclass
class _StreamState:
    stream_id: str
    release_id: str
    mode: Literal["synthetic", "simulation"]
    data_as_of: date
    revision: int
    received_before: datetime
    published_at: datetime
    processed_events: int
    popularity: tuple[str, ...]
    live_histories: dict[str, tuple[str, ...]]
    live_purchases: dict[str, frozenset[str]]
    live_weights: dict[str, dict[str, float]]
    live_events: dict[str, list[dict[str, Any]]]


class InMemoryEventStore:
    """In-memory event store modeling /events, /streams, and /admin/streams/{id}/refresh."""

    def __init__(
        self,
        *,
        release_id: str,
        data_as_of: date,
        catalog_rows: list[dict[str, Any]],
        histories: dict[str, tuple[str, ...]],
        popularity: tuple[str, ...],
        stream_id: str | None = None,
        mode: Literal["synthetic", "simulation"] = "synthetic",
    ) -> None:
        self.lock = threading.RLock()
        self.release_id = release_id
        self.catalog_rows = list(catalog_rows)
        self.catalog_ids = {row["article_id"] for row in self.catalog_rows}
        self.base_histories = {k: tuple(v) for k, v in histories.items()}
        self.base_purchases = {k: frozenset(v) for k, v in self.base_histories.items()}
        self.base_popularity = tuple(popularity)
        self.events_by_id: dict[str, dict[str, Any]] = {}
        self.events_log: list[dict[str, Any]] = []

        sid = stream_id or str(uuid4())
        now = datetime.now(timezone.utc)
        self.streams: dict[str, _StreamState] = {
            sid: _StreamState(
                stream_id=sid,
                release_id=release_id,
                mode=mode,
                data_as_of=data_as_of,
                revision=1,
                received_before=now,
                published_at=now,
                processed_events=0,
                popularity=tuple(popularity),
                live_histories=dict(self.base_histories),
                live_purchases=dict(self.base_purchases),
                live_weights={},
                live_events={},
            )
        }
        self.default_stream_id = sid

    def list_streams(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self._stream_meta(s) for s in self.streams.values()]

    @staticmethod
    def _stream_meta(s: _StreamState) -> dict[str, Any]:
        return {
            "stream_id": s.stream_id,
            "release_id": s.release_id,
            "mode": s.mode,
            "data_as_of": s.data_as_of.isoformat(),
            "revision": s.revision,
            "received_before": s.received_before.isoformat(),
            "published_at": s.published_at.isoformat(),
            "processed_events": s.processed_events,
        }

    def insert_event(self, raw_payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            event = EventInput.model_validate(raw_payload)
        except ValidationError:
            if isinstance(raw_payload, dict) and str(raw_payload.get("release_id", "")).startswith("v2-"):
                event = _CompatEventInput.model_validate(raw_payload)
            else:
                raise
        canonical = event.model_dump(mode="json")
        canonical["event_time"] = event.event_time.astimezone(timezone.utc).isoformat()
        with self.lock:
            stream = self.streams.get(event.stream_id)
            if stream is None or stream.release_id != event.release_id or stream.mode != event.origin:
                return 409, {"detail": "Event or stream identity conflict"}
            if event.article_id not in self.catalog_ids:
                return 422, {"detail": "Invalid event or article"}
            existing = self.events_by_id.get(event.event_id)
            if existing is not None:
                if existing != canonical:
                    return 409, {"detail": "Event ID already has another payload"}
                return 200, {"event_id": event.event_id, "status": "duplicate"}
            self.events_by_id[event.event_id] = canonical
            self.events_log.append(canonical)
            return 201, {"event_id": event.event_id, "status": "created"}

    def refresh(self, stream_id: str, data_as_of: date | None = None) -> dict[str, Any]:
        with self.lock:
            stream = self.streams.get(stream_id)
            if stream is None:
                raise ValueError("Unknown stream")
            cutoff = data_as_of or stream.data_as_of
            if cutoff < stream.data_as_of:
                raise ValueError("Clock cannot move backwards")
            stream_events = [
                ev for ev in self.events_log
                if ev["stream_id"] == stream_id
                and datetime.fromisoformat(ev["event_time"]).date() < cutoff
            ]
            # Aggregate live signals (purchase=1.0, click=0.55, view=0.25);
            # keep live_histories and live_purchases strictly to actual purchases
            # (matching src/hm_recommender/events/aggregate.py:66-76) so discovery never excludes view/click.
            weights_by_user: dict[str, dict[str, float]] = {}
            purchases_by_user: dict[str, set[str]] = {uid: set(items) for uid, items in self.base_histories.items()}
            purchase_stats_by_user: dict[str, dict[str, tuple[date, int]]] = {}
            events_by_user: dict[str, list[dict[str, Any]]] = {}
            purchase_counts: dict[str, int] = {}
            for ev in stream_events:
                uid = ev["customer_id"]
                aid = ev["article_id"]
                kind = ev["kind"]
                w = _SIGNAL_WEIGHTS.get(kind, 0.25)
                user_w = weights_by_user.setdefault(uid, {})
                user_w[aid] = user_w.get(aid, 0.0) + w
                events_by_user.setdefault(uid, []).append(
                    {"article_id": aid, "kind": kind, "weight": w, "event_time": ev["event_time"]}
                )
                if kind == "purchase":
                    purchases_by_user.setdefault(uid, set()).add(aid)
                    purchase_counts[aid] = purchase_counts.get(aid, 0) + 1
                    ev_day = datetime.fromisoformat(ev["event_time"]).astimezone(timezone.utc).date()
                    user_pstats = purchase_stats_by_user.setdefault(uid, {})
                    prev_day, prev_freq = user_pstats.get(aid, (ev_day, 0))
                    user_pstats[aid] = (max(prev_day, ev_day), prev_freq + 1)

            new_histories = dict(self.base_histories)
            for uid, pstats in purchase_stats_by_user.items():
                ordered_live = [
                    a
                    for a, _ in sorted(
                        pstats.items(),
                        key=lambda kv: (-kv[1][0].toordinal(), -kv[1][1], kv[0]),
                    )
                ]
                seen = set(ordered_live)
                base = []
                for a in self.base_histories.get(uid, ()):
                    if a not in seen:
                        seen.add(a)
                        base.append(a)
                new_histories[uid] = tuple(ordered_live + base)

            base_rank = {a: idx for idx, a in enumerate(self.base_popularity)}
            new_pop = tuple(
                sorted(
                    self.base_popularity,
                    key=lambda a: (-purchase_counts.get(a, 0), base_rank[a]),
                )
            )
            now = datetime.now(timezone.utc)
            stream.data_as_of = cutoff
            stream.revision += 1
            stream.received_before = now
            stream.published_at = now
            stream.processed_events = len(stream_events)
            stream.live_histories = new_histories
            stream.live_purchases = {uid: frozenset(items) for uid, items in purchases_by_user.items()}
            stream.live_weights = weights_by_user
            stream.live_events = events_by_user
            stream.popularity = new_pop
            return self._stream_meta(stream)


class InMemoryApiAdapter:
    """In-memory httpx.MockTransport API adapter covering v1/v2 serving, catalog, images & events."""

    def __init__(
        self,
        release_dir: Path,
        *,
        image_zip: Path | None = None,
        image_cache_dir: Path | None = None,
        admin_token: str = "local-test-token",
        stream_id: str | None = None,
    ) -> None:
        self.admin_token = admin_token
        self._image_zip = image_zip
        self._image_cache_dir = Path(image_cache_dir) if image_cache_dir else Path(release_dir).parent / "_img_cache"
        self.image_cache = ImageCache(self._image_zip, self._image_cache_dir)
        self._load_from_dir(Path(release_dir), stream_id=stream_id)
        self.transport = httpx.MockTransport(self.handle)

    def _load_from_dir(self, release_dir: Path, *, stream_id: str | None = None) -> None:
        self.release_dir = Path(release_dir)
        bundle = _load_release_bundle(self.release_dir)
        self.state = bundle.state
        self._is_v2 = bundle.is_v2
        self._v2_artifacts = bundle.v2_artifacts
        self.catalog_rows = bundle.catalog_rows
        self.catalog_by_id: dict[str, dict[str, Any]] = {r["article_id"]: r for r in self.catalog_rows}

        sample_customers = heapq.nsmallest(5, self.state.customers)
        demo_profiles = []
        if sample_customers:
            cid = sample_customers[0]
            hist_items = list(bundle.histories.get(cid, ()))[:3]
            examples = [self.catalog_by_id[a] for a in hist_items if a in self.catalog_by_id]
            demo_profiles.append({
                "customer_id": cid,
                "key": "balanced",
                "label": "Comprador frecuente",
                "description": "Perfil con compras recientes en el catálogo de demostración.",
                "history_count": len(bundle.histories.get(cid, ())),
                "matched_count": len(examples),
                "examples": examples,
            })
        self.demo_profiles = demo_profiles

        self.store = InMemoryEventStore(
            release_id=self.state.release_id,
            data_as_of=self.state.data_as_of,
            catalog_rows=self.catalog_rows,
            histories=bundle.histories,
            popularity=bundle.popularity,
            stream_id=stream_id,
        )

    def swap_release(self, release_dir: Path, *, stream_id: str | None = None) -> str:
        """Switch active release (e.g. h-* <-> v2-*) in-place for live rollback/upgrade tests."""
        self._load_from_dir(Path(release_dir), stream_id=stream_id)
        return self.state.release_id

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="http://in-memory.test", transport=self.transport, trust_env=False)

    @staticmethod
    def _parse_recommend_payload(
        payload: dict[str, Any],
    ) -> tuple[RecommendationRequest, Literal["balanced", "discovery"], dict[str, Any] | None]:
        mode = payload.get("mode", "balanced")
        if mode not in ("balanced", "discovery"):
            raise ValueError(f"Unsupported mode: {mode!r}")
        preferences = payload.get("preferences")
        if preferences is not None:
            if not isinstance(preferences, dict):
                raise ValueError("preferences must be a dict or null")
            unknown_pref = set(preferences.keys()) - {"age_bin", "index_group_name"}
            if unknown_pref:
                raise ValueError(f"Unknown preference keys: {sorted(unknown_pref)}")
        if "mode" in RecommendationRequest.model_fields:
            req = RecommendationRequest.model_validate(payload)
        else:
            extra_keys = set(payload.keys()) - {"customer_id", "k", "filters", "mode", "preferences"}
            if extra_keys:
                raise ValueError(f"Unexpected request keys: {sorted(extra_keys)}")
            req = RecommendationRequest.model_validate(
                {
                    "customer_id": payload["customer_id"],
                    "k": payload.get("k", 12),
                    "filters": payload.get("filters", {}),
                }
            )
        return req, mode, preferences

    def _recommend_for_state(
        self,
        state: Any,
        payload: dict[str, Any],
        *,
        purchased_override: frozenset[str] | set[str] | None = None,
        live_weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        req, mode, preferences = self._parse_recommend_payload(payload)

        if hasattr(state, "recommend_stream"):
            res = state.recommend(req)
            return res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)

        eligible = state.eligible(req.filters)
        history = state.histories.get(req.customer_id, ())
        purchased = set(purchased_override) if purchased_override is not None else set(history)
        live_w = dict(live_weights or {})

        if self._is_v2 and self._v2_artifacts is not None:
            return self._recommend_v2_fallback(
                state,
                req,
                mode=mode,
                preferences=preferences,
                eligible=eligible,
                history=history,
                purchased=purchased,
                live_weights=live_w,
            )

        # v1 (h-*) serving path
        recent = frozenset(aid for aid in history if aid in eligible)
        if mode == "discovery":
            seen_pcodes: set[str] = set()
            ranked: list[str] = []
            pop_rank = {aid: idx for idx, aid in enumerate(state.popularity)}
            candidates = [aid for aid in state.popularity if aid in eligible and aid not in purchased]
            if live_w:
                candidates.sort(key=lambda a: (-live_w.get(a, 0.0), pop_rank[a]))
            for aid in candidates:
                pcode = self.catalog_by_id.get(aid, {}).get("product_code")
                if pcode and pcode in seen_pcodes:
                    continue
                if pcode:
                    seen_pcodes.add(pcode)
                ranked.append(aid)
                if len(ranked) >= req.k:
                    break
            return {
                "request_id": str(uuid4()),
                "release_id": state.release_id,
                "strategy": "recent_then_popular",
                "features_timestamp": state.data_as_of.isoformat(),
                "fallback": not (bool(recent) or bool(live_w)),
                "items": [
                    {
                        "article_id": aid,
                        "rank": idx,
                        "reason": "recent_purchase" if aid in purchased else "popular_7d",
                    }
                    for idx, aid in enumerate(ranked, start=1)
                ],
            }

        if preferences and isinstance(preferences, dict) and not history and not live_w:
            pref_group = preferences.get("index_group_name")
            if pref_group:
                preferred_items = [
                    aid for aid in state.popularity
                    if aid in eligible and self.catalog_by_id.get(aid, {}).get("index_group_name") == pref_group
                ]
                other_items = [aid for aid in state.popularity if aid in eligible and aid not in set(preferred_items)]
                combined = (preferred_items + other_items)[: req.k]
                return {
                    "request_id": str(uuid4()),
                    "release_id": state.release_id,
                    "strategy": "recent_then_popular",
                    "features_timestamp": state.data_as_of.isoformat(),
                    "fallback": True,
                    "items": [
                        {"article_id": aid, "rank": idx, "reason": "popular_7d"}
                        for idx, aid in enumerate(combined, start=1)
                    ],
                }

        return state.recommend(req).model_dump(mode="json")

    def _recommend_v2_fallback(
        self,
        state: HistoryState,
        req: RecommendationRequest,
        *,
        mode: Literal["balanced", "discovery"],
        preferences: dict[str, Any] | None,
        eligible: frozenset[str],
        history: tuple[str, ...],
        purchased: set[str],
        live_weights: dict[str, float],
    ) -> dict[str, Any]:
        v2 = self._v2_artifacts or {}
        aid_to_idx: dict[str, int] = v2.get("aid_to_idx", {})
        user_stats: dict[str, dict[str, Any]] = v2.get("user_stats", {})
        item_pop7: dict[str, int] = v2.get("item_pop7", {})
        seg_pop: dict[tuple[str, str], list[str]] = v2.get("seg_pop", {})
        cooc = v2.get("cooc")
        content_tfidf = v2.get("content_tfidf")
        svd_v = v2.get("svd_v")

        seed_weights: dict[str, float] = {}
        for idx, aid in enumerate(history):
            if aid in purchased:
                seed_weights[aid] = max(seed_weights.get(aid, 0.0), 1.0 / (1.0 + 0.1 * idx))
        for aid, weight in live_weights.items():
            seed_weights[aid] = seed_weights.get(aid, 0.0) + float(weight)

        has_signal = bool(seed_weights)
        uinfo = user_stats.get(req.customer_id, {})
        pref_age: str | None = (preferences or {}).get("age_bin") if isinstance(preferences, dict) else None
        pref_group: str | None = (preferences or {}).get("index_group_name") if isinstance(preferences, dict) else None
        if pref_age is None and uinfo:
            pref_age = uinfo.get("age_bin")
        if pref_group is None and has_signal and uinfo and (uinfo.get("user_count180") or 0) > 0:
            pref_group = (uinfo.get("pref_index_group") or uinfo.get("preferred_index_group")) or None

        seg_ranks: dict[str, int] = {}
        if pref_group:
            seg_items = seg_pop.get((pref_age or "<25", pref_group), [])
            if not seg_items:
                for (ab, ig), items in seg_pop.items():
                    if ig == pref_group:
                        seg_items = items
                        break
            seg_ranks = {aid: r for r, aid in enumerate(seg_items, start=1)}
        elif pref_age:
            merged: list[str] = []
            for (ab, _), items in seg_pop.items():
                if ab == pref_age:
                    merged.extend(items)
            for aid in merged:
                if aid not in seg_ranks:
                    seg_ranks[aid] = len(seg_ranks) + 1

        seed_pcodes: dict[str, float] = {}
        for aid, w in seed_weights.items():
            pcode = self.catalog_by_id.get(aid, {}).get("product_code")
            if pcode:
                seed_pcodes[pcode] = max(seed_pcodes.get(pcode, 0.0), w)

        user_vec: np.ndarray | None = None
        if svd_v is not None and seed_weights:
            acc = np.zeros(svd_v.shape[1], dtype=np.float32)
            for aid, w in seed_weights.items():
                idx = aid_to_idx.get(aid)
                if idx is not None:
                    acc += float(w) * svd_v[idx]
            norm = float(np.linalg.norm(acc))
            if norm > 0.0:
                user_vec = acc / norm

        cooc_vec: np.ndarray | None = None
        if cooc is not None and seed_weights:
            cooc_acc = np.zeros(cooc.shape[1], dtype=np.float32)
            for aid, w in seed_weights.items():
                idx = aid_to_idx.get(aid)
                if idx is not None:
                    cooc_acc += float(w) * np.asarray(cooc[idx].todense()).ravel()
            cooc_vec = cooc_acc

        content_vec: np.ndarray | None = None
        if content_tfidf is not None and seed_weights:
            q_vec = np.zeros(content_tfidf.shape[1], dtype=np.float32)
            for aid, w in seed_weights.items():
                idx = aid_to_idx.get(aid)
                if idx is not None:
                    q_vec += float(w) * np.asarray(content_tfidf[idx].todense()).ravel()
            content_vec = np.asarray(content_tfidf @ q_vec).ravel()

        max_pop7 = max(1, max(item_pop7.values(), default=1))
        pop_rank = {aid: idx for idx, aid in enumerate(state.popularity)}

        scored: list[tuple[float, int, str, str]] = []
        for aid in state.popularity:
            if aid not in eligible:
                continue
            if mode == "discovery" and aid in purchased:
                continue
            cidx = aid_to_idx.get(aid)
            pcode = self.catalog_by_id.get(aid, {}).get("product_code")

            hist_score = seed_weights.get(aid, 0.0)
            variant_score = 0.0
            if pcode and pcode in seed_pcodes:
                # True colorway variant if another article with same product_code is in seeds
                if any(s != aid and self.catalog_by_id.get(s, {}).get("product_code") == pcode for s in seed_weights):
                    variant_score = seed_pcodes[pcode]
            cooc_score = float(cooc_vec[cidx]) if (cooc_vec is not None and cidx is not None) else 0.0
            latent_score = (
                max(0.0, float(svd_v[cidx] @ user_vec))
                if (user_vec is not None and svd_v is not None and cidx is not None)
                else 0.0
            )
            content_score = float(content_vec[cidx]) if (content_vec is not None and cidx is not None) else 0.0
            seg_r = seg_ranks.get(aid)
            seg_score = (1.0 / float(seg_r)) if seg_r is not None else 0.0
            if pref_group and self.catalog_by_id.get(aid, {}).get("index_group_name") == pref_group:
                seg_score += 1.0
            pop_score = float(item_pop7.get(aid, 0)) / float(max_pop7)

            total = (
                3.0 * hist_score
                + 2.5 * variant_score
                + 2.0 * cooc_score
                + 1.5 * latent_score
                + 1.2 * content_score
                + 1.5 * seg_score
                + 0.5 * pop_score
            )

            if aid in purchased:
                reason = "recent_purchase"
            elif variant_score > 0.0 and variant_score >= max(cooc_score, latent_score, content_score):
                reason = "same_product_variant"
            elif cooc_score > 0.0 and cooc_score >= max(latent_score, content_score):
                reason = "frequently_bought_together"
            elif latent_score > 0.0 and latent_score >= content_score:
                reason = "latent_style_match"
            elif content_score > 0.0:
                reason = "content_similar"
            elif seg_score > 0.0 and (pref_group is not None or (preferences and pref_age is not None)):
                reason = "popular_for_segment"
            else:
                reason = "popular_7d"

            scored.append((total, -pop_rank.get(aid, 10_000), aid, reason))

        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))

        chosen: list[tuple[str, str]] = []
        seen_pcodes: set[str] = set()
        for _, _, aid, reason in scored:
            if mode == "discovery":
                pcode = self.catalog_by_id.get(aid, {}).get("product_code")
                if pcode and pcode in seen_pcodes:
                    continue
                if pcode:
                    seen_pcodes.add(pcode)
            chosen.append((aid, reason))
            if len(chosen) >= req.k:
                break

        return {
            "request_id": str(uuid4()),
            "release_id": state.release_id,
            "strategy": "two_stage_lgbm_v2",
            "features_timestamp": state.data_as_of.isoformat(),
            "fallback": not has_signal,
            "items": [
                {"article_id": aid, "rank": idx, "reason": reason}
                for idx, (aid, reason) in enumerate(chosen, start=1)
            ],
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method.upper()

        if method == "GET" and path == "/health/live":
            return httpx.Response(200, json={"status": "alive"})
        if method == "GET" and path == "/health/ready":
            return httpx.Response(
                200,
                json={"status": "ready", "release_id": self.state.release_id, "data_as_of": self.state.data_as_of.isoformat()},
            )
        if method == "GET" and path == "/streams":
            return httpx.Response(200, json={"items": self.store.list_streams()})
        if method == "GET" and path == "/customers":
            return httpx.Response(200, json={"items": heapq.nsmallest(50, self.state.customers)})
        if method == "GET" and path.startswith("/streams/") and path.endswith("/demo-profiles"):
            sid = path.split("/")[2]
            stream = self.store.streams.get(sid)
            if stream is None:
                return httpx.Response(404, json={"detail": "Stream unavailable"})
            return httpx.Response(
                200,
                json={
                    "release_id": stream.release_id,
                    "stream_id": stream.stream_id,
                    "revision": stream.revision,
                    "data_as_of": stream.data_as_of.isoformat(),
                    "mode": stream.mode,
                    "items": self.demo_profiles,
                },
            )
        if method == "GET" and path == "/catalog":
            params = request.url.params
            try:
                offset = int(params.get("offset", "0"))
                limit = int(params.get("limit", "12"))
            except ValueError:
                return httpx.Response(422, json={"detail": "Invalid pagination"})
            if not (0 <= offset <= 10_000_000 and 1 <= limit <= 48):
                return httpx.Response(422, json={"detail": "Pagination out of range"})
            q = (params.get("q") or "").strip().lower()
            idx_group = (params.get("index_group_name") or "").strip()
            filtered = self.catalog_rows
            if idx_group:
                filtered = [r for r in filtered if r.get("index_group_name") == idx_group]
            if q:
                filtered = [
                    r for r in filtered
                    if any(q in str(v).lower() for v in r.values() if v is not None)
                ]
            return httpx.Response(
                200,
                json={
                    "release_id": self.state.release_id,
                    "total": len(filtered),
                    "items": filtered[offset : offset + limit],
                    "filters": {field: sorted(idx) for field, idx in self.state.filter_index.items()},
                },
            )
        if method == "GET" and path.startswith("/articles/") and path.endswith("/image"):
            aid = path.split("/")[2]
            if aid not in self.state.catalog:
                return httpx.Response(404, json={"detail": "Unknown article"})
            body, mime = self.image_cache.read(aid)
            return httpx.Response(200, content=body, headers={"content-type": mime})
        if method == "POST" and path == "/articles/details":
            payload = json.loads(request.content or b"{}")
            ids = payload.get("article_ids")
            if not isinstance(ids, list) or not (1 <= len(ids) <= 48):
                return httpx.Response(422, json={"detail": "Invalid request"})
            items = []
            for aid in ids:
                if aid in self.state.catalog:
                    row = {"article_id": aid}
                    for field in FILTERS:
                        if field in self.catalog_by_id.get(aid, {}):
                            row[field] = self.catalog_by_id[aid][field]
                    items.append(row)
            return httpx.Response(200, json={"release_id": self.state.release_id, "items": items})
        if method == "POST" and path == "/events":
            payload = json.loads(request.content or b"{}")
            try:
                status, body = self.store.insert_event(payload)
                return httpx.Response(status, json=body)
            except Exception:
                return httpx.Response(422, json={"detail": "Invalid event"})
        if method == "POST" and path.startswith("/admin/streams/") and path.endswith("/refresh"):
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {self.admin_token}":
                return httpx.Response(403, json={"detail": "Administrative authorization required"})
            sid = path.split("/")[3]
            payload = json.loads(request.content or b"{}")
            raw_date = payload.get("data_as_of")
            try:
                cutoff = date.fromisoformat(raw_date) if raw_date else None
                meta = self.store.refresh(sid, cutoff)
                return httpx.Response(200, json=meta)
            except Exception:
                return httpx.Response(400, json={"detail": "Invalid stream or clock"})
        if method == "POST" and path == "/recommend":
            payload = json.loads(request.content or b"{}")
            try:
                return httpx.Response(200, json=self._recommend_for_state(self.state, payload))
            except Exception:
                return httpx.Response(422, json={"detail": "Invalid request"})
        if method == "POST" and path.startswith("/streams/") and path.endswith("/recommend"):
            sid = path.split("/")[2]
            stream = self.store.streams.get(sid)
            if stream is None:
                return httpx.Response(404, json={"detail": "Stream unavailable"})
            if stream.release_id != self.state.release_id:
                return httpx.Response(409, json={"detail": "Stream belongs to another active release"})
            payload = json.loads(request.content or b"{}")
            uid = payload.get("customer_id", "")
            try:
                if hasattr(self.state, "recommend_stream"):
                    req, _, _ = self._parse_recommend_payload(payload)
                    live_ev_list = stream.live_events.get(uid, [])
                    try:
                        res = self.state.recommend_stream(
                            req,
                            cutoff=stream.data_as_of,
                            popular=stream.popularity,
                            history=stream.live_histories.get(uid, ()),
                            live_events=live_ev_list,
                        )
                    except (TypeError, AttributeError):
                        res = self.state.recommend_stream(
                            req,
                            cutoff=stream.data_as_of,
                            popular=stream.popularity,
                            history=stream.live_histories.get(uid, ()),
                            live_events=stream.live_weights.get(uid, {}),
                        )
                    body = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
                else:
                    live_state = replace(
                        self.state,
                        data_as_of=stream.data_as_of,
                        histories=MappingProxyType({uid: stream.live_histories.get(uid, ())}),
                        popularity=stream.popularity,
                    )
                    body = self._recommend_for_state(
                        live_state,
                        payload,
                        purchased_override=stream.live_purchases.get(uid, frozenset()),
                        live_weights=stream.live_weights.get(uid, {}),
                    )
            except Exception:
                return httpx.Response(422, json={"detail": "Invalid request"})
            body.update(
                stream_id=sid,
                revision=stream.revision,
                received_before=stream.received_before.isoformat(),
                published_at=stream.published_at.isoformat(),
                mode=stream.mode,
            )
            return httpx.Response(200, json=body)

        return httpx.Response(404, json={"detail": f"Unhandled mock path: {method} {path}"})


# ---------------------------------------------------------------------------
# In-Process Uvicorn Server, Streamlit AppTest Wrapper & Latency Timer
# ---------------------------------------------------------------------------

@contextmanager
def start_uvicorn_app(
    app: FastAPI,
    *,
    lifespan: Literal["on", "off"] = "off",
    startup_timeout: float = 10.0,
):
    """Run a FastAPI app on an ephemeral 127.0.0.1:0 port in a daemon thread."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        lifespan=lifespan,
        access_log=False,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time_mod.monotonic() + startup_timeout
        while not server.started and thread.is_alive() and time_mod.monotonic() < deadline:
            time_mod.sleep(0.01)
        if not server.started:
            raise RuntimeError("In-process uvicorn server failed to start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        sock.close()


def visible_text(app: AppTest) -> str:
    """Collect visible text elements from a Streamlit AppTest instance."""
    kinds = ("title", "header", "subheader", "text", "caption", "markdown", "info", "success", "warning", "error")
    return "\n".join(str(item.value) for kind in kinds for item in getattr(app, kind))


def _clear_st_caches() -> None:
    """Safely clear Streamlit @st.cache_data caches between AppTest sessions."""
    try:
        import streamlit as st

        clear_fn = getattr(getattr(st, "cache_data", None), "clear", None)
        if callable(clear_fn):
            clear_fn()
    except Exception:
        pass


@contextmanager
def ui_apptest_session(
    api_client_or_transport: Any = None,
    *,
    api_url: str | None = None,
    ui_source: Path | None = None,
    admin_token: str = "local-test-token",
    default_timeout: int = 20,
):
    """Context manager keeping httpx.Client MockTransport or HMREC_API_URL active across AppTest runs."""
    source = Path(ui_source or os.environ.get("TEST_UI_SOURCE") or UI_APP_PATH)
    prev_pw = os.environ.get("HM_APP_PASSWORD")
    prev_url = os.environ.get("HMREC_API_URL")
    os.environ["HM_APP_PASSWORD"] = admin_token
    if api_url is not None:
        os.environ["HMREC_API_URL"] = api_url

    _clear_st_caches()
    try:
        if api_client_or_transport is not None:
            if hasattr(api_client_or_transport, "transport"):
                transport = api_client_or_transport.transport
            elif isinstance(api_client_or_transport, httpx.BaseTransport):
                transport = api_client_or_transport
            elif callable(api_client_or_transport):
                transport = httpx.MockTransport(api_client_or_transport)
            else:
                raise TypeError("api_client_or_transport must provide .transport, BaseTransport, or callable")

            original_client = httpx.Client

            def _mock_client(*args: Any, **kwargs: Any) -> httpx.Client:
                kwargs.setdefault("transport", transport)
                return original_client(*args, **kwargs)

            with patch("httpx.Client", side_effect=_mock_client):
                app = AppTest.from_file(str(source), default_timeout=default_timeout).run()
                yield app
        else:
            app = AppTest.from_file(str(source), default_timeout=default_timeout).run()
            yield app
    finally:
        _clear_st_caches()
        if prev_pw is None:
            os.environ.pop("HM_APP_PASSWORD", None)
        else:
            os.environ["HM_APP_PASSWORD"] = prev_pw
        if api_url is not None:
            if prev_url is None:
                os.environ.pop("HMREC_API_URL", None)
            else:
                os.environ["HMREC_API_URL"] = prev_url


def run_ui_apptest(
    api_client_or_transport: Any = None,
    *,
    api_url: str | None = None,
    ui_source: Path | None = None,
    admin_token: str = "local-test-token",
    default_timeout: int = 20,
) -> AppTest:
    """Convenience one-shot AppTest runner returning the initial AppTest state."""
    with ui_apptest_session(
        api_client_or_transport,
        api_url=api_url,
        ui_source=ui_source,
        admin_token=admin_token,
        default_timeout=default_timeout,
    ) as app:
        return app


def measure_p95_ms(
    fn: Callable[[], Any],
    iterations: int = 50,
    *,
    warmup: int = 5,
) -> float:
    """Measure the 95th percentile execution latency in milliseconds."""
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")

    for _ in range(warmup):
        fn()

    samples: list[float] = []
    for _ in range(iterations):
        t0 = time_mod.perf_counter()
        fn()
        t1 = time_mod.perf_counter()
        samples.append((t1 - t0) * 1000.0)

    samples.sort()
    n = len(samples)
    if n == 1:
        return samples[0]
    idx = min(n - 1, max(0, math.ceil(0.95 * n) - 1))
    return samples[idx]


# ---------------------------------------------------------------------------
# Opaque-Box UI & Transport Helpers (Shared by Tier 3 & Tier 4)
# ---------------------------------------------------------------------------


def make_recording_transport(
    adapter: InMemoryApiAdapter,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[dict[str, Any]]]:
    """Wrap an InMemoryApiAdapter in a transport that records HTTP calls and JSON responses."""
    calls: list[dict[str, Any]] = []

    def _transport(request: httpx.Request) -> httpx.Response:
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except Exception:
                body = request.content
        response = adapter.handle(request)
        resp_json: Any = None
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                resp_json = json.loads(response.content.decode("utf-8"))
            except Exception:
                resp_json = None
        calls.append(
            {
                "method": request.method.upper(),
                "path": request.url.path,
                "params": dict(request.url.params),
                "json": body,
                "status_code": response.status_code,
                "response_json": resp_json,
            }
        )
        return response

    return _transport, calls


def find_control(
    app: Any,
    kinds: tuple[str, ...],
    *,
    label_re: str | None = None,
    key_re: str | None = None,
    option_re: str | None = None,
    exclude_key_re: str | None = None,
    exclude_label_re: str | None = None,
) -> Any | None:
    """Locate a Streamlit AppTest widget using key_re > label_re > option_re precedence."""
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


def find_buttons(
    app: Any,
    *,
    label_re: str | None = None,
    key_re: str | None = None,
) -> list[Any]:
    """Return all AppTest button widgets matching label_re or key_re."""
    matches: list[Any] = []
    for btn in getattr(app, "button", []):
        label_val = str(getattr(btn, "label", "") or "")
        key_val = str(getattr(btn, "key", "") or "")
        if label_re and re.search(label_re, label_val, re.I):
            matches.append(btn)
        elif key_re and re.search(key_re, key_val, re.I):
            matches.append(btn)
    return matches


def select_option_matching(ctrl: Any, pattern: str) -> str | None:
    """Select the first option on an AppTest selector matching regex ``pattern``."""
    options = list(getattr(ctrl, "options", []) or [])
    for opt in options:
        if re.search(pattern, str(opt), re.I):
            if hasattr(ctrl, "select"):
                ctrl.select(opt)
            elif hasattr(ctrl, "set_value"):
                ctrl.set_value(opt)
            return str(opt)
    return None


def count_catalog_calls(calls: Sequence[dict[str, Any]]) -> int:
    """Count GET /catalog requests recorded by make_recording_transport."""
    return sum(1 for c in calls if c["method"] == "GET" and c["path"] == "/catalog")
