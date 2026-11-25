"""Portable v2-* release package builder, atomic publisher, and strict verifier."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import date, timedelta
import fcntl
import gc
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
from typing import Any, Literal

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, PrivateAttr
from scipy import sparse

from hm_recommender.contracts import Contract, DatasetManifest, SHA256
from hm_recommender.data.ingest import load_dataset
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.quality.contracts import digest
from hm_recommender.releases.contracts import ReleaseFile
from hm_recommender.v2.features import (
    CATALOG_COLUMNS,
    V2_FEATURE_NAMES,
    V2_FEATURE_VERSION,
    V2FeatureContext,
    _build_attr_vocab,
)
from hm_recommender.v2.ranker import (
    RankerConfig,
    TrainedRankerResult,
    load_booster,
    train_sliding_ranker,
)
from hm_recommender.v2.retrieval import (
    AGE_BINS,
    EVENT_KIND_WEIGHTS,
    EXPLAINABLE_REASONS,
    MISSING_RANK_SENTINEL,
    MultiCandidateRetriever,
    RetrievalConfig,
)

PARQUET_TABLES: frozenset[str] = frozenset({
    'catalog',
    'item_stats',
    'user_stats',
    'user_affinities',
    'histories',
    'segment_popularity',
})

MATRIX_FILES: dict[str, str] = {
    'cooc': 'cooc.npz',
    'svd_v': 'svd_v.npy',
    'svd_sigma': 'svd_sigma.npy',
    'content_tfidf': 'content_tfidf.npz',
}

OTHER_FILES: dict[str, str] = {
    'model': 'model.txt',
    'rules': 'rules.json',
    'validation-reference': 'validation-reference.json',
}

EXPECTED_FILENAMES: dict[str, str] = {
    **{name: f'{name}.parquet' for name in sorted(PARQUET_TABLES)},
    **MATRIX_FILES,
    **OTHER_FILES,
}

_CATALOG_SCHEMA: dict[str, pl.DataType] = {
    'article_id': pl.String,
    'first_seen': pl.Date,
    'product_code': pl.String,
    'prod_name': pl.String,
    'product_type_name': pl.String,
    'product_group_name': pl.String,
    'colour_group_name': pl.String,
    'graphical_appearance_name': pl.String,
    'index_group_name': pl.String,
    'department_no': pl.String,
    'garment_group_name': pl.String,
    'detail_desc': pl.String,
}

_ITEM_STATS_SCHEMA: dict[str, pl.DataType] = {
    'article_id': pl.String,
    'item_price': pl.Float32,
    'item_count7': pl.UInt32,
    'item_count14': pl.UInt32,
    'item_count28': pl.UInt32,
    'item_count90': pl.UInt32,
    'item_trend_7_28': pl.Float32,
    'item_trend_14_90': pl.Float32,
    'item_online_ratio': pl.Float32,
    'item_mean_age': pl.Float32,
    'item_std_age': pl.Float32,
    'item_repurchase_rate': pl.Float32,
    'item_days_since_first_seen': pl.Float32,
}

_USER_STATS_SCHEMA: dict[str, pl.DataType] = {
    'customer_id': pl.String,
    'age': pl.Float32,
    'age_missing': pl.Int8,
    'age_bin': pl.String,
    'club_active': pl.Int8,
    'news_freq_code': pl.Int8,
    'fn_active': pl.Int8,
    'user_active_flag': pl.Int8,
    'pref_index_group': pl.String,
    'user_count7': pl.UInt32,
    'user_count28': pl.UInt32,
    'user_count90': pl.UInt32,
    'user_count180': pl.UInt32,
    'user_recency': pl.Float32,
    'user_avg_price': pl.Float32,
    'user_std_price': pl.Float32,
    'user_online_ratio': pl.Float32,
    'user_n_unique_prodcodes': pl.UInt32,
}

_USER_AFFINITIES_SCHEMA: dict[str, pl.DataType] = {
    'customer_id': pl.String,
    'attr_code': pl.List(pl.UInt16),
    'share': pl.List(pl.Float32),
}

_HISTORIES_SCHEMA: dict[str, pl.DataType] = {
    'customer_id': pl.String,
    'article_id': pl.String,
    'last_purchase': pl.Date,
    'count_7': pl.UInt16,
    'count_28': pl.UInt16,
    'count_90': pl.UInt16,
    'count_180': pl.UInt32,
    'decay_score': pl.Float32,
}

_SEGMENT_POPULARITY_SCHEMA: dict[str, pl.DataType] = {
    'age_bin': pl.String,
    'index_group_name': pl.String,
    'article_id': pl.String,
    'node': pl.Int32,
    'score_seg_pop': pl.Float32,
    'rank_seg_pop': pl.Int16,
}


def _json_safe_dict(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(mapping)))


class V2Rules(Contract):
    strategy: Literal['two_stage_lgbm_v2'] = 'two_stage_lgbm_v2'
    protocol: Literal['quality-v2'] = 'quality-v2'
    events: Literal['strictly_before_cutoff'] = 'strictly_before_cutoff'
    feature_version: Literal[2] = V2_FEATURE_VERSION  # type: ignore[assignment]
    feature_names: tuple[str, ...] = V2_FEATURE_NAMES
    cold_start_col: str = 'user_count180'
    missing_rank_sentinel: int = MISSING_RANK_SENTINEL
    history_days: int = 180
    popularity_days: int = 7
    latent_dim: int = 64
    latent_window_days: int = 180
    latent_half_life_days: float = 30.0
    content_max_vocab: int = 16384
    event_weights: dict[str, float] = Field(default_factory=lambda: dict(EVENT_KIND_WEIGHTS))
    modes: tuple[str, ...] = ('balanced', 'discovery')
    explainable_reasons: tuple[str, ...] = EXPLAINABLE_REASONS
    retrieval_config: dict[str, Any] = Field(default_factory=lambda: _json_safe_dict(asdict(RetrievalConfig())))
    ranker_config: dict[str, Any] = Field(default_factory=lambda: _json_safe_dict(asdict(RankerConfig())))


class V2ValidationReference(Contract):
    protocol: Literal['quality-v2'] = 'quality-v2'
    data_as_of: date
    feature_version: Literal[2] = V2_FEATURE_VERSION  # type: ignore[assignment]
    best_iteration: int = Field(ge=1, strict=True)
    val_ndcg_at_12: float | None = Field(default=None, ge=0.0, le=1.0)
    metrics_measured_for_this_package: bool = False
    quality_v2_report: dict[str, Any] | None = None
    training_metadata: dict[str, Any] = Field(default_factory=dict)


class V2ReleaseManifest(Contract):
    _root: Path | None = PrivateAttr(default=None)
    schema_version: Literal[1] = 1
    release_id: str = Field(pattern=r'^v2-[a-f0-9]{64}$')
    commit: str = Field(pattern=r'^[a-f0-9]{40}$')
    source_sha256: SHA256
    dataset_manifest_sha256: SHA256
    data_as_of: date
    strategy: Literal['two_stage_lgbm_v2'] = 'two_stage_lgbm_v2'
    rules: V2Rules = Field(default_factory=V2Rules)
    files: dict[str, ReleaseFile]
    software_versions: dict[str, str]


def v2_release_identity(payload: Mapping[str, Any]) -> str:
    return 'v2-' + digest({k: v for k, v in payload.items() if k != 'release_id'})


def _software_versions() -> dict[str, str]:
    pkgs = ('polars', 'pyarrow', 'pydantic', 'numpy', 'scipy', 'lightgbm')
    return {
        'python': platform.python_version(),
        **{p: version(p) for p in pkgs},
    }


def _fsync_directory(directory: Path) -> None:
    for path in directory.iterdir():
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_parent(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _assert_finite_float_cols(table: pl.DataFrame, cols: Sequence[str], label: str) -> None:
    for col in cols:
        s = table[col]
        if s.null_count() > 0:
            raise ValueError(f'Null values in {label}.{col}')
        arr = s.to_numpy()
        if not np.isfinite(arr).all():
            raise ValueError(f'Nonfinite values in {label}.{col}')


def _verify_tables_sequential(root: Path, manifest: V2ReleaseManifest) -> tuple[int, int]:
    cutoff = manifest.data_as_of

    # 1. catalog.parquet + item_stats.parquet
    catalog = pl.read_parquet(root / 'catalog.parquet')
    if dict(catalog.schema) != _CATALOG_SCHEMA:
        raise ValueError('Unexpected catalog.parquet schema')
    if catalog.is_empty():
        raise ValueError('catalog.parquet must not be empty')
    if catalog['article_id'].null_count() > 0 or (catalog['article_id'] == '').any():
        raise ValueError('catalog.parquet contains null or empty article_id')
    if catalog['article_id'].n_unique() != catalog.height:
        raise ValueError('Duplicate article_id in catalog.parquet')
    if not catalog['article_id'].equals(catalog['article_id'].sort()):
        raise ValueError('catalog.parquet must be sorted ascending by article_id')
    if catalog['first_seen'].null_count() > 0 or catalog['first_seen'].max() >= cutoff:  # type: ignore[operator]
        raise ValueError('catalog.parquet contains invalid or >= data_as_of first_seen')

    item_stats = pl.read_parquet(root / 'item_stats.parquet')
    if dict(item_stats.schema) != _ITEM_STATS_SCHEMA:
        raise ValueError('Unexpected item_stats.parquet schema')
    if item_stats.height != catalog.height or not item_stats['article_id'].equals(catalog['article_id']):
        raise ValueError('item_stats.parquet must be 1:1 row-aligned with catalog.parquet')
    for col in item_stats.columns:
        if item_stats[col].null_count() > 0:
            raise ValueError(f'Null values in item_stats.{col}')
    _assert_finite_float_cols(
        item_stats,
        (
            'item_price',
            'item_trend_7_28',
            'item_trend_14_90',
            'item_online_ratio',
            'item_mean_age',
            'item_std_age',
            'item_repurchase_rate',
            'item_days_since_first_seen',
        ),
        'item_stats',
    )
    if (
        (item_stats['item_days_since_first_seen'] < 1.0).any()
        or (item_stats['item_count7'] > item_stats['item_count14']).any()
        or (item_stats['item_count14'] > item_stats['item_count28']).any()
        or (item_stats['item_count28'] > item_stats['item_count90']).any()
        or (item_stats['item_online_ratio'] < 0.0).any()
        or (item_stats['item_online_ratio'] > 1.0).any()
        or (item_stats['item_repurchase_rate'] < 0.0).any()
        or (item_stats['item_repurchase_rate'] > 1.0).any()
    ):
        raise ValueError('Invalid item_stats monotonicity or bounds')

    n_items = catalog.height
    article_ids_df = catalog.select('article_id')
    article_ids_list = catalog['article_id'].to_list()
    _, n_attrs, _ = _build_attr_vocab(catalog)
    del catalog, item_stats
    gc.collect()

    # 2. user_stats.parquet
    user_stats = pl.read_parquet(root / 'user_stats.parquet')
    if dict(user_stats.schema) != _USER_STATS_SCHEMA:
        raise ValueError('Unexpected user_stats.parquet schema')
    if user_stats.is_empty():
        raise ValueError('user_stats.parquet must not be empty')
    if user_stats['customer_id'].null_count() > 0 or (user_stats['customer_id'] == '').any():
        raise ValueError('user_stats.parquet contains null or empty customer_id')
    if user_stats['customer_id'].n_unique() != user_stats.height:
        raise ValueError('Duplicate customer_id in user_stats.parquet')
    if not user_stats['customer_id'].equals(user_stats['customer_id'].sort()):
        raise ValueError('user_stats.parquet must be sorted ascending by customer_id')
    for col in user_stats.columns:
        if user_stats[col].null_count() > 0:
            raise ValueError(f'Null values in user_stats.{col}')
    _assert_finite_float_cols(
        user_stats,
        ('age', 'user_recency', 'user_avg_price', 'user_std_price', 'user_online_ratio'),
        'user_stats',
    )
    if not set(user_stats['age_bin'].unique().to_list()) <= set(AGE_BINS):
        raise ValueError('Invalid age_bin in user_stats.parquet')
    if (
        (user_stats['user_count7'] > user_stats['user_count28']).any()
        or (user_stats['user_count28'] > user_stats['user_count90']).any()
        or (user_stats['user_count90'] > user_stats['user_count180']).any()
        or (user_stats['user_online_ratio'] < 0.0).any()
        or (user_stats['user_online_ratio'] > 1.0).any()
    ):
        raise ValueError('Invalid user_stats monotonicity or bounds')

    user_ids_df = user_stats.select('customer_id')
    del user_stats
    gc.collect()

    # 3. user_affinities.parquet
    user_aff = pl.read_parquet(root / 'user_affinities.parquet')
    if dict(user_aff.schema) != _USER_AFFINITIES_SCHEMA:
        raise ValueError('Unexpected user_affinities.parquet schema')
    if not user_aff.is_empty():
        if user_aff['customer_id'].null_count() > 0 or (user_aff['customer_id'] == '').any():
            raise ValueError('user_affinities.parquet contains null or empty customer_id')
        if user_aff['customer_id'].n_unique() != user_aff.height:
            raise ValueError('Duplicate customer_id in user_affinities.parquet')
        if not user_aff['customer_id'].equals(user_aff['customer_id'].sort()):
            raise ValueError('user_affinities.parquet must be sorted ascending by customer_id')
        if user_aff.select('customer_id').join(user_ids_df, on='customer_id', how='anti').height > 0:
            raise ValueError('Unbound customer_id in user_affinities.parquet')

        pa_codes = user_aff['attr_code'].rechunk().to_arrow()
        if isinstance(pa_codes, pa.ChunkedArray):
            pa_codes = pa_codes.combine_chunks()
        pa_shares = user_aff['share'].rechunk().to_arrow()
        if isinstance(pa_shares, pa.ChunkedArray):
            pa_shares = pa_shares.combine_chunks()
        if pa_codes.null_count > 0 or pa_shares.null_count > 0:
            raise ValueError('Null list entries in user_affinities.parquet')
        c_offsets = pa_codes.offsets.to_numpy(zero_copy_only=False)
        s_offsets = pa_shares.offsets.to_numpy(zero_copy_only=False)
        if not np.array_equal(np.diff(c_offsets), np.diff(s_offsets)):
            raise ValueError('Mismatched attr_code/share list lengths in user_affinities.parquet')
        c_values = pa_codes.values.slice(int(c_offsets[0]), int(c_offsets[-1] - c_offsets[0]))
        s_values = pa_shares.values.slice(int(s_offsets[0]), int(s_offsets[-1] - s_offsets[0]))
        if c_values.null_count > 0 or s_values.null_count > 0:
            raise ValueError('Null list elements in user_affinities.parquet')
        if len(c_values) > 0:
            codes_np = c_values.to_numpy(zero_copy_only=False)
            shares_np = s_values.to_numpy(zero_copy_only=False)
            if (codes_np < 0).any() or (codes_np >= n_attrs).any():
                raise ValueError('Out-of-range attr_code in user_affinities.parquet')
            if not np.isfinite(shares_np).all() or (shares_np < 0.0).any():
                raise ValueError('Invalid share weights in user_affinities.parquet')
    del user_aff
    gc.collect()

    # 4. histories.parquet
    histories = pl.read_parquet(root / 'histories.parquet')
    if dict(histories.schema) != _HISTORIES_SCHEMA:
        raise ValueError('Unexpected histories.parquet schema')
    if not histories.is_empty():
        for key in ('customer_id', 'article_id'):
            if histories[key].null_count() > 0 or (histories[key] == '').any():
                raise ValueError(f'Missing or empty {key} in histories.parquet')
        for col in histories.columns:
            if histories[col].null_count() > 0:
                raise ValueError(f'Null values in histories.{col}')
        if histories.select('customer_id', 'article_id').n_unique() != histories.height:
            raise ValueError('Duplicate (customer_id, article_id) in histories.parquet')
        expected_hist = histories.sort(
            ['customer_id', 'last_purchase', 'count_180', 'article_id'],
            descending=[False, True, True, False],
        )
        if not histories.equals(expected_hist):
            raise ValueError('Unordered histories.parquet')
        del expected_hist
        min_window = cutoff - timedelta(days=manifest.rules.history_days)
        if (
            histories['last_purchase'].min() < min_window  # type: ignore[operator]
            or histories['last_purchase'].max() >= cutoff  # type: ignore[operator]
            or histories['count_180'].min() < 1  # type: ignore[operator]
            or (histories['count_7'] > histories['count_28']).any()
            or (histories['count_28'] > histories['count_90']).any()
            or (histories['count_90'] > histories['count_180']).any()
        ):
            raise ValueError('Invalid history timestamps or counts in histories.parquet')
        decay_arr = histories['decay_score'].to_numpy()
        if not np.isfinite(decay_arr).all() or (decay_arr <= 0.0).any():
            raise ValueError('Invalid decay_score in histories.parquet')
        if (
            histories.select('customer_id').join(user_ids_df, on='customer_id', how='anti').height > 0
            or histories.select('article_id').join(article_ids_df, on='article_id', how='anti').height > 0
        ):
            raise ValueError('Unbound keys in histories.parquet')
    del histories, user_ids_df, article_ids_df
    gc.collect()

    # 5. segment_popularity.parquet
    seg_pop = pl.read_parquet(root / 'segment_popularity.parquet')
    if dict(seg_pop.schema) != _SEGMENT_POPULARITY_SCHEMA:
        raise ValueError('Unexpected segment_popularity.parquet schema')
    if seg_pop.is_empty():
        raise ValueError('segment_popularity.parquet must not be empty')
    for col in seg_pop.columns:
        if seg_pop[col].null_count() > 0:
            raise ValueError(f'Null values in segment_popularity.{col}')
    if (seg_pop['index_group_name'] == '').any() or (seg_pop['article_id'] == '').any():
        raise ValueError('Empty index_group_name or article_id in segment_popularity.parquet')
    if not set(seg_pop['age_bin'].unique().to_list()) <= (set(AGE_BINS) | {'__all__'}):
        raise ValueError('Invalid age_bin in segment_popularity.parquet')
    if seg_pop.filter((pl.col('age_bin') == '__all__') & (pl.col('index_group_name') == '__all__')).is_empty():
        raise ValueError('segment_popularity.parquet missing (__all__, __all__) fallback')
    if seg_pop.select('age_bin', 'index_group_name', 'article_id').n_unique() != seg_pop.height:
        raise ValueError('Duplicate (age_bin, index_group_name, article_id) in segment_popularity.parquet')
    _assert_finite_float_cols(seg_pop, ('score_seg_pop',), 'segment_popularity')
    if (seg_pop['score_seg_pop'] < 0.0).any():
        raise ValueError('Negative score_seg_pop in segment_popularity.parquet')

    nodes_np = seg_pop['node'].to_numpy()
    if (nodes_np < 0).any() or (nodes_np >= n_items).any():
        raise ValueError('Out-of-range node in segment_popularity.parquet')
    seg_arts = seg_pop['article_id'].to_list()
    for node_val, art_id in zip(nodes_np.tolist(), seg_arts):
        if article_ids_list[node_val] != art_id:
            raise ValueError('segment_popularity.node does not match catalog article_id order')

    # Verify rank_seg_pop is strictly 1..group_len per (age_bin, index_group_name)
    rank_check = (
        seg_pop
        .with_columns(pl.int_range(1, pl.len() + 1).over(['age_bin', 'index_group_name']).cast(pl.Int16).alias('_expected_rank'))
        .filter(pl.col('rank_seg_pop') != pl.col('_expected_rank'))
    )
    if not rank_check.is_empty():
        raise ValueError('Invalid or non-sequential rank_seg_pop in segment_popularity.parquet')

    del seg_pop, rank_check, article_ids_list
    gc.collect()

    return n_items, n_attrs


def _verify_matrices_sequential(root: Path, n_items: int, rules: V2Rules) -> None:
    # 1. cooc.npz
    cooc = sparse.load_npz(root / 'cooc.npz')
    if not isinstance(cooc, sparse.csr_matrix):
        raise ValueError('cooc.npz must be a CSR sparse matrix')
    if cooc.shape != (n_items, n_items) or cooc.dtype != np.float32:
        raise ValueError(f'Invalid cooc.npz shape/dtype: {cooc.shape} / {cooc.dtype}')
    cooc.check_format(full_check=False)
    if cooc.indptr.shape != (n_items + 1,) or cooc.indptr[0] != 0 or cooc.indptr[-1] != cooc.indices.size:
        raise ValueError('Invalid cooc.npz CSR indptr')
    if cooc.indices.size > 1:
        non_boundary = np.ones(cooc.indices.size - 1, dtype=bool)
        nz_ends = cooc.indptr[1:-1] - 1
        nz_ends = nz_ends[(nz_ends >= 0) & (nz_ends < non_boundary.size)]
        non_boundary[nz_ends] = False
        if np.any(cooc.indices[1:][non_boundary] <= cooc.indices[:-1][non_boundary]):
            raise ValueError('cooc.npz must have strictly sorted unique indices per row')
    if not np.isfinite(cooc.data).all() or (cooc.data < 0.0).any():
        raise ValueError('cooc.npz contains negative or nonfinite values')
    if float(np.abs(cooc.diagonal()).sum()) != 0.0:
        raise ValueError('cooc.npz must have zero diagonal')
    del cooc
    gc.collect()

    # 2. svd_v.npy
    svd_v = np.load(root / 'svd_v.npy', allow_pickle=False)
    if svd_v.ndim != 2 or svd_v.shape[0] != n_items or not (0 <= svd_v.shape[1] <= rules.latent_dim):
        raise ValueError(f'Invalid svd_v.npy shape: {svd_v.shape}')
    if svd_v.dtype != np.float32 or not np.isfinite(svd_v).all():
        raise ValueError('svd_v.npy must be finite float32')
    k = int(svd_v.shape[1])
    del svd_v
    gc.collect()

    # 3. svd_sigma.npy
    svd_sigma = np.load(root / 'svd_sigma.npy', allow_pickle=False)
    if svd_sigma.ndim != 1 or svd_sigma.shape != (k,):
        raise ValueError(f'Invalid svd_sigma.npy shape: {svd_sigma.shape} != ({k},)')
    if svd_sigma.dtype != np.float32 or not np.isfinite(svd_sigma).all() or (svd_sigma < 0.0).any():
        raise ValueError('svd_sigma.npy must be finite non-negative float32')
    if k >= 2 and not np.all(np.diff(svd_sigma) <= 1e-6):
        raise ValueError('svd_sigma.npy must be sorted descending')
    del svd_sigma
    gc.collect()

    # 4. content_tfidf.npz
    tfidf = sparse.load_npz(root / 'content_tfidf.npz')
    if not isinstance(tfidf, sparse.csr_matrix):
        raise ValueError('content_tfidf.npz must be a CSR sparse matrix')
    if tfidf.shape[0] != n_items or not (0 <= tfidf.shape[1] <= rules.content_max_vocab) or tfidf.dtype != np.float32:
        raise ValueError(f'Invalid content_tfidf.npz shape/dtype: {tfidf.shape} / {tfidf.dtype}')
    tfidf.check_format(full_check=False)
    if not np.isfinite(tfidf.data).all() or (tfidf.data < 0.0).any():
        raise ValueError('content_tfidf.npz contains negative or nonfinite values')
    del tfidf
    gc.collect()


def verify_v2_release(directory: Path | str) -> V2ReleaseManifest:
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Release must be a regular directory')

    expected_names = set(EXPECTED_FILENAMES.values()) | {'manifest.json'}
    if {p.name for p in root.iterdir()} != expected_names:
        raise ValueError('Incomplete or unexpected release inventory')
    if any(p.is_symlink() or not p.is_file() for p in root.iterdir()):
        raise ValueError('Release symlinks or subdirectories are forbidden')

    manifest = V2ReleaseManifest.model_validate_json((root / 'manifest.json').read_text())
    if set(manifest.files) != set(EXPECTED_FILENAMES):
        raise ValueError('Invalid release manifest file keys')
    if manifest.release_id != v2_release_identity(manifest.model_dump(mode='json')):
        raise ValueError('Release identity mismatch')

    for name, entry in manifest.files.items():
        expected_fname = EXPECTED_FILENAMES[name]
        if entry.path != expected_fname:
            raise ValueError(f'Release paths must be canonical relative filenames ({name}: {entry.path})')
        fpath = root / expected_fname
        if fpath.stat().st_size != entry.size_bytes or sha256(fpath) != entry.sha256:
            raise ValueError(f'Release file integrity mismatch for {expected_fname}')
        if name in PARQUET_TABLES:
            if entry.rows is None or pq.ParquetFile(fpath).metadata.num_rows != entry.rows:
                raise ValueError(f'Parquet row count mismatch for {expected_fname}')
        elif entry.rows is not None:
            raise ValueError(f'Non-Parquet entry {name} must have rows=None')

    if json.loads((root / 'rules.json').read_text()) != manifest.rules.model_dump(mode='json'):
        raise ValueError('rules.json mismatch against manifest.rules')
    if manifest.rules.feature_version != V2_FEATURE_VERSION or manifest.rules.feature_names != V2_FEATURE_NAMES:
        raise ValueError('Unsupported V2Rules feature schema')

    val_ref = V2ValidationReference.model_validate_json((root / 'validation-reference.json').read_text())
    if (
        val_ref.data_as_of != manifest.data_as_of
        or val_ref.feature_version != V2_FEATURE_VERSION
        or val_ref.protocol != manifest.rules.protocol
    ):
        raise ValueError('validation-reference.json provenance mismatch')

    booster = load_booster(root / 'model.txt', expected_feature_names=manifest.rules.feature_names)
    try:
        if booster.num_trees() < 1 or int(booster.current_iteration()) != val_ref.best_iteration:
            raise ValueError('Booster iteration mismatch against validation-reference.json')
    finally:
        del booster
        gc.collect()

    n_items, _ = _verify_tables_sequential(root, manifest)
    _verify_matrices_sequential(root, n_items, manifest.rules)

    manifest._root = root
    return manifest


def _resolve_commit(commit: str | None) -> str:
    resolved = commit if commit is not None else os.environ.get('HMREC_SOURCE_COMMIT', '')
    if not re.fullmatch(r'[a-f0-9]{40}', resolved):
        raise ValueError('HMREC_SOURCE_COMMIT or commit must be a 40-char lowercase hex SHA')
    return resolved


def build_v2_release(
    dataset: DatasetManifest,
    data_as_of: date,
    output_dir: Path | str,
    *,
    commit: str | None = None,
    retrieval_config: RetrievalConfig | None = None,
    ranker_config: RankerConfig | None = None,
    trained_ranker: TrainedRankerResult | None = None,
    model_path: Path | str | None = None,
    validation_reference: Mapping[str, Any] | None = None,
    work_dir: Path | str | None = None,
) -> V2ReleaseManifest:
    if type(data_as_of) is not date:
        raise ValueError('Explicit date cutoff required')
    resolved_commit = _resolve_commit(commit)
    if dataset._root is None:
        raise ValueError('Dataset root required')
    verified_ds = load_dataset(dataset._root)
    if verified_ds != dataset:
        raise ValueError('Dataset manifest changed')
    if not (dataset.min_date < data_as_of <= dataset.max_date + timedelta(days=1)):
        raise ValueError('Cutoff outside dataset coverage')

    r_cfg = retrieval_config if retrieval_config is not None else RetrievalConfig()
    rk_cfg = ranker_config if ranker_config is not None else RankerConfig()
    rules = V2Rules(
        cold_start_col=rk_cfg.cold_start_col,
        missing_rank_sentinel=r_cfg.missing_rank_sentinel,
        history_days=r_cfg.history_days,
        popularity_days=r_cfg.pop_days,
        latent_dim=r_cfg.latent_dim,
        latent_window_days=r_cfg.latent_window_days,
        latent_half_life_days=r_cfg.latent_half_life_days,
        content_max_vocab=r_cfg.content_max_vocab,
        retrieval_config=_json_safe_dict(asdict(r_cfg)),
        ranker_config=_json_safe_dict(asdict(rk_cfg)),
    )
    identity = {
        'commit': resolved_commit,
        'source_sha256': dataset.source_sha256,
        'dataset_manifest_sha256': sha256(dataset._root / 'manifest.json'),
        'data_as_of': data_as_of,
    }

    root = Path(output_dir)
    root.parent.mkdir(parents=True, exist_ok=True)
    with (root.parent / ('.' + root.name + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another process is writing to this release destination') from None

        if root.exists() or root.is_symlink():
            saved = verify_v2_release(root)
            if any(getattr(saved, k) != v for k, v in identity.items()) or saved.rules != rules:
                raise ValueError('Destination belongs to another release')
            return saved

        temporary = Path(tempfile.mkdtemp(prefix='.' + root.name + '.partial-', dir=root.parent))
        files: dict[str, ReleaseFile] = {}
        try:
            # Stage A: Ranker model.txt (ranker work_dir strictly OUTSIDE temporary)
            if trained_ranker is not None:
                if tuple(trained_ranker.feature_names) != V2_FEATURE_NAMES:
                    raise ValueError('TrainedRankerResult feature_names mismatch')
                src_model = Path(trained_ranker.model_path)
                dst_model = temporary / 'model.txt'
                shutil.copyfile(src_model, dst_model)
                b = load_booster(dst_model, expected_feature_names=V2_FEATURE_NAMES)
                best_iter = int(b.current_iteration())
                del b
                val_ndcg = float(trained_ranker.val_ndcg_at_12)
                train_meta = dict(trained_ranker.training_metadata)
            elif model_path is not None:
                src_model = Path(model_path)
                b = load_booster(src_model, expected_feature_names=V2_FEATURE_NAMES)
                best_iter = int(b.current_iteration())
                del b
                dst_model = temporary / 'model.txt'
                shutil.copyfile(src_model, dst_model)
                val_ndcg = None
                train_meta = {}
            else:
                if work_dir is not None:
                    w_dir = Path(work_dir)
                    w_dir.mkdir(parents=True, exist_ok=True)
                    res = train_sliding_ranker(
                        dataset,
                        cutoff=data_as_of,
                        config=rk_cfg,
                        retrieval_config=r_cfg,
                        work_dir=w_dir,
                    )
                    dst_model = temporary / 'model.txt'
                    shutil.copyfile(res.model_path, dst_model)
                    best_iter = int(res.best_iteration)
                    val_ndcg = float(res.val_ndcg_at_12)
                    train_meta = dict(res.training_metadata)
                    del res
                else:
                    with tempfile.TemporaryDirectory(prefix='.v2_ranker_work_', dir=root.parent) as rk_tmp:
                        res = train_sliding_ranker(
                            dataset,
                            cutoff=data_as_of,
                            config=rk_cfg,
                            retrieval_config=r_cfg,
                            work_dir=Path(rk_tmp),
                        )
                        dst_model = temporary / 'model.txt'
                        shutil.copyfile(res.model_path, dst_model)
                        best_iter = int(res.best_iteration)
                        val_ndcg = float(res.val_ndcg_at_12)
                        train_meta = dict(res.training_metadata)
                        del res
            files['model'] = ReleaseFile(
                path='model.txt',
                sha256=sha256(temporary / 'model.txt'),
                size_bytes=(temporary / 'model.txt').stat().st_size,
            )
            gc.collect()

            # Stage B: Stage-1 retrieval matrices & segment_popularity.parquet
            retriever = MultiCandidateRetriever.fit(dataset, cutoff=data_as_of, config=r_cfg)
            retriever_article_ids = retriever.catalog['article_id'].to_list()

            cooc_path = temporary / 'cooc.npz'
            sparse.save_npz(cooc_path, retriever.cooc_matrix.astype(np.float32, copy=False))
            files['cooc'] = ReleaseFile(path=cooc_path.name, sha256=sha256(cooc_path), size_bytes=cooc_path.stat().st_size)

            svd_v_path = temporary / 'svd_v.npy'
            np.save(svd_v_path, retriever.latent_index.svd_v.astype(np.float32, copy=False))
            files['svd_v'] = ReleaseFile(path=svd_v_path.name, sha256=sha256(svd_v_path), size_bytes=svd_v_path.stat().st_size)

            svd_sigma_path = temporary / 'svd_sigma.npy'
            np.save(svd_sigma_path, retriever.latent_index.svd_sigma.astype(np.float32, copy=False))
            files['svd_sigma'] = ReleaseFile(
                path=svd_sigma_path.name,
                sha256=sha256(svd_sigma_path),
                size_bytes=svd_sigma_path.stat().st_size,
            )

            tfidf_path = temporary / 'content_tfidf.npz'
            sparse.save_npz(tfidf_path, retriever.latent_index.content_tfidf.astype(np.float32, copy=False))
            files['content_tfidf'] = ReleaseFile(
                path=tfidf_path.name,
                sha256=sha256(tfidf_path),
                size_bytes=tfidf_path.stat().st_size,
            )

            seg_pop_path = temporary / 'segment_popularity.parquet'
            retriever.seg_pop_df.write_parquet(seg_pop_path, compression='zstd', row_group_size=65536)
            files['segment_popularity'] = ReleaseFile(
                path=seg_pop_path.name,
                rows=retriever.seg_pop_df.height,
                sha256=sha256(seg_pop_path),
                size_bytes=seg_pop_path.stat().st_size,
            )
            del retriever
            gc.collect()

            # Stage C: Stage-2 summary Parquet tables
            feat_ctx = V2FeatureContext.from_dataset(dataset, cutoff=data_as_of, retain_dataframes=True)
            if feat_ctx.catalog['article_id'].to_list() != retriever_article_ids:
                raise ValueError('Feature context catalog order diverged from retriever catalog')
            del retriever_article_ids

            for tbl_name, tbl in (
                ('catalog', feat_ctx.catalog),
                ('item_stats', feat_ctx.item_stats),
                ('user_stats', feat_ctx.user_stats),
                ('user_affinities', feat_ctx.user_affinities),
                ('histories', feat_ctx.histories),
            ):
                p_path = temporary / f'{tbl_name}.parquet'
                tbl.write_parquet(p_path, compression='zstd', row_group_size=65536)
                files[tbl_name] = ReleaseFile(
                    path=p_path.name,
                    rows=tbl.height,
                    sha256=sha256(p_path),
                    size_bytes=p_path.stat().st_size,
                )
            del feat_ctx
            gc.collect()

            # Stage D: rules.json, validation-reference.json, manifest.json + pre-publish verify
            rules_path = temporary / 'rules.json'
            atomic_json(rules_path, rules.model_dump(mode='json'))
            files['rules'] = ReleaseFile(path=rules_path.name, sha256=sha256(rules_path), size_bytes=rules_path.stat().st_size)

            val_ref_payload: dict[str, Any] = {
                'protocol': rules.protocol,
                'data_as_of': data_as_of,
                'feature_version': V2_FEATURE_VERSION,
                'best_iteration': best_iter,
                'val_ndcg_at_12': val_ndcg,
                'metrics_measured_for_this_package': False,
                'quality_v2_report': None,
                'training_metadata': train_meta,
            }
            if validation_reference is not None:
                for k, v in validation_reference.items():
                    if k in ('protocol', 'data_as_of', 'feature_version'):
                        continue
                    val_ref_payload[k] = v
            val_ref = V2ValidationReference.model_validate(val_ref_payload)
            val_ref_path = temporary / 'validation-reference.json'
            atomic_json(val_ref_path, val_ref.model_dump(mode='json'))
            files['validation-reference'] = ReleaseFile(
                path=val_ref_path.name,
                sha256=sha256(val_ref_path),
                size_bytes=val_ref_path.stat().st_size,
            )

            manifest = V2ReleaseManifest(
                release_id='v2-' + ('0' * 64),
                **identity,
                rules=rules,
                files=files,
                software_versions=_software_versions(),
            )
            manifest.release_id = v2_release_identity(manifest.model_dump(mode='json'))
            atomic_json(temporary / 'manifest.json', manifest.model_dump(mode='json'))

            verify_v2_release(temporary)
            _fsync_directory(temporary)
            temporary.rename(root)
            _fsync_parent(root.parent)
            manifest._root = root
            return manifest
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def publish_v2_release(
    dataset: DatasetManifest,
    data_as_of: date,
    releases_dir: Path | str,
    *,
    commit: str | None = None,
    retrieval_config: RetrievalConfig | None = None,
    ranker_config: RankerConfig | None = None,
    trained_ranker: TrainedRankerResult | None = None,
    model_path: Path | str | None = None,
    validation_reference: Mapping[str, Any] | None = None,
    work_dir: Path | str | None = None,
) -> V2ReleaseManifest:
    base_dir = Path(releases_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix='.v2-stage-', dir=base_dir))
    try:
        staged_pkg = stage_parent / 'package'
        manifest = build_v2_release(
            dataset,
            data_as_of,
            staged_pkg,
            commit=commit,
            retrieval_config=retrieval_config,
            ranker_config=ranker_config,
            trained_ranker=trained_ranker,
            model_path=model_path,
            validation_reference=validation_reference,
            work_dir=work_dir,
        )
        target = base_dir / manifest.release_id
        with (base_dir / ('.' + target.name + '.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another process is publishing this release') from None
            if target.exists() or target.is_symlink():
                saved = verify_v2_release(target)
                if saved.release_id != manifest.release_id:
                    raise ValueError('Destination release_id mismatch')
                return saved
            staged_pkg.rename(target)
            _fsync_parent(base_dir)
        return verify_v2_release(target)
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description='Build or verify a portable v2-* recommender release.')
    parser.add_argument('--dataset', default='data/processed/hm-v1')
    parser.add_argument('--data-as-of', default='2020-09-23')
    parser.add_argument('--releases-dir', default='artifacts/releases-v2')
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--model-path', default=None)
    parser.add_argument('--quality-report', default=None)
    parser.add_argument('--verify', default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.verify is not None:
        manifest = verify_v2_release(args.verify)
        print(json.dumps({
            'verified': True,
            'release_id': manifest.release_id,
            'data_as_of': manifest.data_as_of.isoformat(),
            'catalog_rows': manifest.files['catalog'].rows,
            'user_stats_rows': manifest.files['user_stats'].rows,
        }, indent=2))
        return 0

    ds = load_dataset(Path(args.dataset))
    cutoff = date.fromisoformat(args.data_as_of)

    r_cfg: RetrievalConfig | None = None
    rk_cfg: RankerConfig | None = None
    if args.config is not None:
        raw_cfg = json.loads(Path(args.config).read_text())
        if 'retrieval' in raw_cfg:
            r_cfg = RetrievalConfig(**raw_cfg['retrieval'])
        if 'ranker' in raw_cfg:
            rk_params = dict(raw_cfg['ranker'])
            if 'label_gain' in rk_params and isinstance(rk_params['label_gain'], list):
                rk_params['label_gain'] = tuple(int(v) for v in rk_params['label_gain'])
            rk_cfg = RankerConfig(**rk_params)

    val_ref: dict[str, Any] | None = None
    if args.quality_report is not None:
        q_rep = json.loads(Path(args.quality_report).read_text())
        val_ref = {
            'quality_v2_report': {
                'protocol': q_rep.get('protocol'),
                'config_sha256': q_rep.get('config_sha256'),
                'summary_v1_v3': q_rep.get('summary_v1_v3'),
                'descriptive_test_2020_09_16': (
                    {
                        'cutoff': q_rep['descriptive_test_2020_09_16'].get('cutoff'),
                        'v2_metrics': q_rep['descriptive_test_2020_09_16'].get('v2', {}).get('metrics'),
                        'v2_targets': q_rep['descriptive_test_2020_09_16'].get('v2', {}).get('targets'),
                    }
                    if q_rep.get('descriptive_test_2020_09_16')
                    else None
                ),
                'gates': q_rep.get('gates'),
            },
        }

    if args.output_dir is not None:
        manifest = build_v2_release(
            ds,
            cutoff,
            Path(args.output_dir),
            retrieval_config=r_cfg,
            ranker_config=rk_cfg,
            model_path=args.model_path,
            validation_reference=val_ref,
        )
    else:
        manifest = publish_v2_release(
            ds,
            cutoff,
            Path(args.releases_dir),
            retrieval_config=r_cfg,
            ranker_config=rk_cfg,
            model_path=args.model_path,
            validation_reference=val_ref,
        )

    print(json.dumps({
        'release_id': manifest.release_id,
        'path': str(manifest._root),
        'data_as_of': manifest.data_as_of.isoformat(),
        'files': {k: v.rows for k, v in manifest.files.items()},
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


__all__ = [
    'EXPECTED_FILENAMES',
    'MATRIX_FILES',
    'OTHER_FILES',
    'PARQUET_TABLES',
    'V2ReleaseManifest',
    'V2Rules',
    'V2ValidationReference',
    'build_v2_release',
    'main',
    'publish_v2_release',
    'v2_release_identity',
    'verify_v2_release',
]
