"""Stage-1 Multi-Candidate Retrieval (5 generators, Polars + float32 SciPy sparse, zero unique_prefix)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence, Set as AbstractSet
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
import polars as pl
from scipy import sparse

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import scan
from hm_recommender.v2.latent import CONTENT_FIELDS, LatentContentIndex

AGE_BINS: tuple[str, ...] = ('<25', '25-34', '35-44', '45-54', '55+', 'unknown')
_AGE_BIN_TO_CODE: dict[str, int] = {name: i for i, name in enumerate(AGE_BINS)}
_UNKNOWN_AGE_CODE: int = _AGE_BIN_TO_CODE['unknown']

EVENT_KIND_WEIGHTS: dict[str, float] = {'purchase': 1.0, 'click': 0.55, 'view': 0.25}
MISSING_RANK_SENTINEL: int = 9999

GENERATOR_NAMES: tuple[str, ...] = (
    'history_decay',
    'product_code_variants',
    'cooccurrence',
    'latent',
    'content',
    'segment_popularity',
    'pop7d',
)

CANDIDATE_KEY_COLUMNS: tuple[str, str] = ('customer_id', 'article_id')

SCORE_COLUMNS: tuple[str, ...] = (
    'score_history_decay',
    'score_variant',
    'score_cooc',
    'score_latent',
    'score_content',
    'score_seg_pop',
    'score_pop7d',
)

RANK_COLUMNS: tuple[str, ...] = (
    'rank_history_decay',
    'rank_variant',
    'rank_cooc',
    'rank_latent',
    'rank_content',
    'rank_seg_pop',
    'rank_pop7d',
)

CANDIDATE_SCHEMA: dict[str, pl.DataType] = {
    'customer_id': pl.String,
    'article_id': pl.String,
    **{col: pl.Float32 for col in SCORE_COLUMNS},
    **{col: pl.Int16 for col in RANK_COLUMNS},
}

EXPLAINABLE_REASONS: tuple[str, ...] = (
    'recent_purchase',
    'same_product_variant',
    'frequently_bought_together',
    'latent_style_match',
    'content_similar',
    'popular_for_segment',
    'popular_7d',
)

REASON_PRIORITY: tuple[tuple[str, str], ...] = (
    ('rank_history_decay', 'recent_purchase'),
    ('rank_variant', 'same_product_variant'),
    ('rank_cooc', 'frequently_bought_together'),
    ('rank_latent', 'latent_style_match'),
    ('rank_content', 'content_similar'),
    ('rank_seg_pop', 'popular_for_segment'),
    ('rank_pop7d', 'popular_7d'),
)

_ALL_SEG = '__all__'


def cast_age_expr(col: str = 'age') -> pl.Expr:
    return (
        pl.col(col)
        .cast(pl.String, strict=False)
        .str.strip_chars()
        .cast(pl.Float32, strict=False)
    )


def age_bin_expr(col: str = 'age') -> pl.Expr:
    age = cast_age_expr(col)
    return (
        pl.when(age.is_null() | age.is_nan() | (age < 10.0) | (age > 110.0))
        .then(pl.lit('unknown'))
        .when(age < 25.0)
        .then(pl.lit('<25'))
        .when(age < 35.0)
        .then(pl.lit('25-34'))
        .when(age < 45.0)
        .then(pl.lit('35-44'))
        .when(age < 55.0)
        .then(pl.lit('45-54'))
        .otherwise(pl.lit('55+'))
        .alias('age_bin')
    )


def _bin_age_scalar(raw_age: object) -> str:
    if raw_age is None:
        return 'unknown'
    try:
        val = float(raw_age)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 'unknown'
    if not math.isfinite(val) or val < 10.0 or val > 110.0:
        return 'unknown'
    if val < 25.0:
        return '<25'
    if val < 35.0:
        return '25-34'
    if val < 45.0:
        return '35-44'
    if val < 55.0:
        return '45-54'
    return '55+'


def _validate_cutoff(cutoff: date) -> None:
    if type(cutoff) is not date:
        raise ValueError('Explicit date cutoff required')


@dataclass(frozen=True)
class RetrievalConfig:
    # Generator 1: history_decay
    history_days: int = 180
    history_tau_short: float = 14.0
    history_tau_long: float = 60.0
    history_short_weight: float = 0.6
    history_long_weight: float = 0.4
    history_limit: int = 80
    # Generator 2: product_code_variants
    variant_limit: int = 60
    variant_pop_days: int = 14
    variant_per_code_cap: int = 50
    # Generator 3: cooccurrence (90d weekly-basket BM25 + temporal decay)
    cooc_days: int = 90
    cooc_basket_cap: int = 50
    cooc_half_life_days: float = 14.0
    cooc_bm25_k1: float = 1.2
    cooc_bm25_b: float = 0.75
    cooc_out_degree: int = 80
    cooc_block_nodes: int = 512
    cooc_limit: int = 80
    # Generator 4: latent (SVD k=64) + content (TF-IDF)
    latent_dim: int = 64
    latent_window_days: int = 180
    latent_half_life_days: float = 30.0
    latent_limit: int = 60
    content_limit: int = 60
    content_max_vocab: int = 16384
    # Generator 5: segment_popularity + global pop7d
    pop_days: int = 7
    segment_pop_limit: int = 80
    global_pop_limit: int = 80
    # Output / batching
    missing_rank_sentinel: int = MISSING_RANK_SENTINEL
    user_batch_size: int = 4096

    def __post_init__(self) -> None:
        if (
            isinstance(self.missing_rank_sentinel, bool)
            or not isinstance(self.missing_rank_sentinel, int)
            or not (1 <= self.missing_rank_sentinel <= 32767)
        ):
            raise ValueError('missing_rank_sentinel must be in 1..32767 (pl.Int16)')
        for name in (
            'history_limit',
            'variant_limit',
            'cooc_limit',
            'latent_limit',
            'content_limit',
            'segment_pop_limit',
            'global_pop_limit',
        ):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, int) or val < 1 or val >= self.missing_rank_sentinel:
                raise ValueError(f'{name}={val} must be in 1..{self.missing_rank_sentinel - 1}')
        for name in (
            'history_days',
            'variant_pop_days',
            'variant_per_code_cap',
            'cooc_days',
            'cooc_basket_cap',
            'cooc_out_degree',
            'cooc_block_nodes',
            'latent_dim',
            'latent_window_days',
            'content_max_vocab',
            'pop_days',
            'user_batch_size',
        ):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, int) or val < 1:
                raise ValueError(f'{name} must be >= 1')
        for name in (
            'history_tau_short',
            'history_tau_long',
            'cooc_half_life_days',
            'latent_half_life_days',
        ):
            val = getattr(self, name)
            if (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not math.isfinite(val)
                or val <= 0
            ):
                raise ValueError(f'{name} must be finite and > 0')
        for name in (
            'history_short_weight',
            'history_long_weight',
            'cooc_bm25_k1',
        ):
            val = getattr(self, name)
            if (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not math.isfinite(val)
                or val < 0
            ):
                raise ValueError(f'{name} must be finite and >= 0')
        b_val = self.cooc_bm25_b
        if (
            isinstance(b_val, bool)
            or not isinstance(b_val, (int, float))
            or not math.isfinite(b_val)
            or not (0.0 <= b_val <= 1.0)
        ):
            raise ValueError('cooc_bm25_b must be finite and in [0, 1]')


def _pack_user(hist_row: int, ig_code: int, age_code: int) -> int:
    return ((hist_row + 1) << 20) | ((ig_code & 0xFFFF) << 4) | (age_code & 0xF)


def _unpack_user(packed: int) -> tuple[int, int, int]:
    return (packed >> 20) - 1, (packed >> 4) & 0xFFFF, packed & 0xF


def _extract_csr_topk(
    mat: sparse.csr_matrix,
    u_indices: np.ndarray,
    limit: int,
    *,
    exclude_csr: sparse.csr_matrix | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Extract top-limit positive entries per row ordered by (-score, +node)."""
    n_rows = mat.shape[0]
    if n_rows == 0 or limit <= 0 or mat.nnz == 0:
        return (
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.float32),
            np.empty(0, dtype=np.int16),
        )

    indptr = mat.indptr
    indices = mat.indices
    data = mat.data
    ex_indptr = exclude_csr.indptr if exclude_csr is not None else None
    ex_indices = exclude_csr.indices if exclude_csr is not None else None

    out_u: list[np.ndarray] = []
    out_n: list[np.ndarray] = []
    out_s: list[np.ndarray] = []
    out_r: list[np.ndarray] = []

    for r in range(n_rows):
        s_pos = int(indptr[r])
        e_pos = int(indptr[r + 1])
        if s_pos == e_pos:
            continue
        cols = indices[s_pos:e_pos]
        vals = data[s_pos:e_pos]
        mask = vals > 0.0
        if ex_indptr is not None and ex_indices is not None:
            ex_cols = ex_indices[int(ex_indptr[r]):int(ex_indptr[r + 1])]
            if ex_cols.size:
                mask = mask & (~np.isin(cols, ex_cols, assume_unique=False))
        if not np.all(mask):
            cols = cols[mask]
            vals = vals[mask]
        if vals.size == 0:
            continue
        order = np.lexsort((cols, -vals))[:limit]
        cnt = order.size
        out_u.append(np.full(cnt, u_indices[r], dtype=np.uint32))
        out_n.append(cols[order].astype(np.int32, copy=False))
        out_s.append(vals[order].astype(np.float32, copy=False))
        out_r.append(np.arange(1, cnt + 1, dtype=np.int16))

    if not out_u:
        return (
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.float32),
            np.empty(0, dtype=np.int16),
        )
    return (
        np.concatenate(out_u),
        np.concatenate(out_n),
        np.concatenate(out_s),
        np.concatenate(out_r),
    )


def _dense_topk_to_arrays(
    u_indices: np.ndarray,
    top_nodes: np.ndarray,
    top_scores: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if u_indices.size == 0 or top_nodes.shape[1] == 0:
        return (
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.float32),
            np.empty(0, dtype=np.int16),
        )
    valid = top_nodes >= 0
    if not np.any(valid):
        return (
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.float32),
            np.empty(0, dtype=np.int16),
        )
    n_rows, width = top_nodes.shape
    u_grid = np.broadcast_to(u_indices[:, None], (n_rows, width))
    rank_grid = np.broadcast_to(np.arange(1, width + 1, dtype=np.int16)[None, :], (n_rows, width))
    return (
        u_grid[valid].astype(np.uint32, copy=False),
        top_nodes[valid].astype(np.int32, copy=False),
        top_scores[valid].astype(np.float32, copy=False),
        rank_grid[valid].astype(np.int16, copy=False),
    )


def _row_l1_normalize(mat: sparse.csr_matrix) -> sparse.csr_matrix:
    if mat.shape[0] == 0 or mat.nnz == 0:
        return mat.astype(np.float32, copy=False)
    row_sums = np.asarray(mat.sum(axis=1)).ravel().astype(np.float32)
    inv = np.zeros(mat.shape[0], dtype=np.float32)
    nz = row_sums > 1e-12
    inv[nz] = np.float32(1.0) / row_sums[nz]
    out = mat.astype(np.float32, copy=True)
    row_rep = np.repeat(np.arange(out.shape[0], dtype=np.int32), np.diff(out.indptr))
    out.data *= inv[row_rep]
    return out


def _make_gen_frame(
    u_arr: np.ndarray,
    n_arr: np.ndarray,
    s_arr: np.ndarray,
    r_arr: np.ndarray,
    score_col: str,
    rank_col: str,
) -> pl.DataFrame:
    return pl.DataFrame({
        '_u_idx': pl.Series('_u_idx', u_arr, dtype=pl.UInt32),
        'node': pl.Series('node', n_arr, dtype=pl.Int32),
        score_col: pl.Series(score_col, s_arr, dtype=pl.Float32),
        rank_col: pl.Series(rank_col, r_arr, dtype=pl.Int16),
    })


def _build_variant_matrix(
    catalog: pl.DataFrame,
    past_tx: pl.LazyFrame,
    cutoff: date,
    cfg: RetrievalConfig,
) -> sparse.csr_matrix:
    n_articles = catalog.height
    if n_articles == 0:
        return sparse.csr_matrix((0, 0), dtype=np.float32)

    pop_lower = cutoff - timedelta(days=cfg.variant_pop_days)
    pop14_df = (
        past_tx
        .filter(pl.col('t_dat') >= pl.lit(pop_lower))
        .join(catalog.select('article_id', 'node').lazy(), on='article_id', how='inner')
        .group_by('node')
        .agg(pl.len().cast(pl.Float32).alias('pop14d'))
        .collect(engine='streaming')
    )
    pop14d = np.zeros(n_articles, dtype=np.float32)
    if not pop14_df.is_empty():
        pop14d[pop14_df['node'].to_numpy()] = pop14_df['pop14d'].to_numpy().astype(np.float32, copy=False)

    groups_df = (
        catalog
        .select('node', 'product_code')
        .filter(pl.col('product_code').is_not_null() & (pl.col('product_code') != ''))
        .group_by('product_code')
        .agg(pl.col('node'))
        .filter(pl.col('node').list.len() >= 2)
    )
    if groups_df.is_empty():
        return sparse.csr_matrix((n_articles, n_articles), dtype=np.float32)

    rows_list: list[np.ndarray] = []
    cols_list: list[np.ndarray] = []
    vals_list: list[np.ndarray] = []
    for node_group in groups_df['node'].to_list():
        nodes_arr = np.asarray(node_group, dtype=np.int32)
        order = np.lexsort((nodes_arr, -pop14d[nodes_arr]))
        ordered_nodes = nodes_arr[order]
        targets = ordered_nodes[:cfg.variant_per_code_cap]
        target_weights = (1.0 + np.log1p(pop14d[targets])).astype(np.float32)
        for src in ordered_nodes:
            keep = targets != src
            if np.any(keep):
                t_kept = targets[keep]
                w_kept = target_weights[keep]
                rows_list.append(np.full(t_kept.size, src, dtype=np.int32))
                cols_list.append(t_kept)
                vals_list.append(w_kept)

    if not rows_list:
        return sparse.csr_matrix((n_articles, n_articles), dtype=np.float32)

    mat = sparse.csr_matrix(
        (np.concatenate(vals_list), (np.concatenate(rows_list), np.concatenate(cols_list))),
        shape=(n_articles, n_articles),
        dtype=np.float32,
    )
    mat.sum_duplicates()
    mat.sort_indices()
    return mat


def _build_cooccurrence_matrix(
    catalog: pl.DataFrame,
    past_tx: pl.LazyFrame,
    cutoff: date,
    cfg: RetrievalConfig,
) -> sparse.csr_matrix:
    n_articles = catalog.height
    if n_articles == 0:
        return sparse.csr_matrix((0, 0), dtype=np.float32)

    cooc_lower = cutoff - timedelta(days=cfg.cooc_days)
    unique_baskets = (
        past_tx
        .filter((pl.col('t_dat') >= pl.lit(cooc_lower)) & pl.col('customer_id').is_not_null())
        .join(catalog.select('article_id', 'node').lazy(), on='article_id', how='inner')
        .with_columns(((pl.col('t_dat') - pl.lit(cooc_lower)).dt.total_days() // 7).alias('week'))
        .group_by('customer_id', 'week', 'node')
        .agg(
            pl.col('t_dat').max().alias('last_purchase'),
            pl.len().alias('frequency'),
        )
        .sort(
            ['customer_id', 'week', 'last_purchase', 'frequency', 'node'],
            descending=[False, False, True, True, False],
        )
    )
    baskets = (
        unique_baskets
        .group_by('customer_id', 'week', maintain_order=True)
        .agg(
            pl.col('node').head(cfg.cooc_basket_cap),
            pl.col('last_purchase').head(cfg.cooc_basket_cap),
        )
        .sort('customer_id', 'week')
        .collect(engine='streaming')
    )
    n_baskets = baskets.height
    if n_baskets == 0:
        return sparse.csr_matrix((n_articles, n_articles), dtype=np.float32)

    members = (
        baskets
        .with_row_index('basket')
        .select('basket', 'node', 'last_purchase')
        .explode(['node', 'last_purchase'], empty_as_null=False)
        .with_columns(
            (
                pl.lit(2.0, dtype=pl.Float64)
                ** (
                    -(pl.lit(cutoff) - pl.col('last_purchase')).dt.total_days().cast(pl.Float64)
                    / pl.lit(float(cfg.cooc_half_life_days), dtype=pl.Float64)
                )
            )
            .sqrt()
            .cast(pl.Float32)
            .alias('sqrt_w')
        )
    )
    del baskets

    b_idx = members['basket'].to_numpy().astype(np.int32, copy=False)
    n_idx = members['node'].to_numpy().astype(np.int32, copy=False)
    s_w = members['sqrt_w'].to_numpy().astype(np.float32, copy=False)
    del members

    incidence = sparse.csr_matrix((s_w, (b_idx, n_idx)), shape=(n_baskets, n_articles), dtype=np.float32)
    incidence.sum_duplicates()
    incidence.sort_indices()
    del b_idx, n_idx, s_w

    df = np.bincount(incidence.indices, minlength=n_articles).astype(np.float32)
    decayed_support = np.asarray(incidence.power(2).sum(axis=0)).ravel().astype(np.float32)
    nz_sup = decayed_support > 0.0
    avg_support = max(float(decayed_support[nz_sup].mean()) if np.any(nz_sup) else 1.0, 1e-6)

    k1 = np.float32(cfg.cooc_bm25_k1)
    b_param = np.float32(cfg.cooc_bm25_b)
    idf_all = np.log1p(
        (np.float32(n_baskets) - df + np.float32(0.5)) / (df + np.float32(0.5))
    ).astype(np.float32)
    norm_all = (np.float32(1.0) - b_param + (b_param / np.float32(avg_support)) * decayed_support).astype(np.float32)

    columns = incidence.tocsc()
    indptr = np.zeros(n_articles + 1, dtype=np.int32)
    out_cols: list[np.ndarray] = []
    out_vals: list[np.ndarray] = []

    for start in range(0, n_articles, cfg.cooc_block_nodes):
        end = min(start + cfg.cooc_block_nodes, n_articles)
        block_csr = (columns[:, start:end].T @ incidence).tocsr()
        b_indptr = block_csr.indptr
        b_indices = block_csr.indices
        b_data = block_csr.data
        for local_r in range(end - start):
            origin = start + local_r
            s_pos = int(b_indptr[local_r])
            e_pos = int(b_indptr[local_r + 1])
            if s_pos == e_pos:
                indptr[origin + 1] = indptr[origin]
                continue
            cols = b_indices[s_pos:e_pos]
            vals = b_data[s_pos:e_pos]
            keep = (cols != origin) & (vals > 0.0)
            if not np.all(keep):
                cols = cols[keep]
                vals = vals[keep]
            if cols.size == 0:
                indptr[origin + 1] = indptr[origin]
                continue
            denom = np.maximum(vals + k1 * norm_all[origin], np.float32(1e-6))
            weights = ((vals * np.float32(k1 + 1.0)) / denom) * idf_all[cols]
            weights = weights.astype(np.float32, copy=False)
            if cols.size > cfg.cooc_out_degree:
                order = np.lexsort((cols, -weights))[:cfg.cooc_out_degree]
                cols = cols[order]
                weights = weights[order]
            sort_cols = np.argsort(cols)
            cols_sorted = cols[sort_cols].astype(np.int32, copy=False)
            w_sorted = weights[sort_cols].astype(np.float32, copy=False)
            out_cols.append(cols_sorted)
            out_vals.append(w_sorted)
            indptr[origin + 1] = indptr[origin] + cols_sorted.size

    del incidence, columns
    if not out_cols:
        return sparse.csr_matrix((n_articles, n_articles), dtype=np.float32)

    all_cols = np.concatenate(out_cols)
    all_vals = np.concatenate(out_vals)
    mat = sparse.csr_matrix((all_vals, all_cols, indptr), shape=(n_articles, n_articles), dtype=np.float32)
    mat.has_sorted_indices = True
    return mat


def _build_popularity_tables(
    catalog: pl.DataFrame,
    past_tx: pl.LazyFrame,
    cust_age_df: pl.DataFrame | None,
    cutoff: date,
    cfg: RetrievalConfig,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    pop_lower = cutoff - timedelta(days=cfg.pop_days)
    recent_tx = past_tx.filter(pl.col('t_dat') >= pl.lit(pop_lower))

    counts_7d = (
        recent_tx
        .join(catalog.select('article_id', 'node').lazy(), on='article_id', how='inner')
        .group_by('node')
        .agg(pl.len().cast(pl.Int64).alias('count_7d'))
        .collect(engine='streaming')
    )

    pop_all = (
        catalog
        .select('node', 'article_id', 'index_group_name')
        .join(counts_7d, on='node', how='left')
        .with_columns(pl.col('count_7d').fill_null(0).cast(pl.Int64))
    )

    pop7d_df = (
        pop_all
        .sort(['count_7d', 'node'], descending=[True, False])
        .head(cfg.global_pop_limit)
        .with_row_index('rank_pop7d', offset=1)
        .with_columns(
            pl.col('count_7d').cast(pl.Float64).log1p().cast(pl.Float32).alias('score_pop7d'),
            pl.col('rank_pop7d').cast(pl.Int16),
        )
        .select('node', 'article_id', 'score_pop7d', 'rank_pop7d')
    )

    # Segmented 7d popularity across hierarchy levels:
    # (age_bin, index_group_name), (age_bin, '__all__'), ('__all__', index_group_name), ('__all__', '__all__')
    tx_items = (
        recent_tx
        .join(catalog.select('article_id', 'node', 'index_group_name').lazy(), on='article_id', how='inner')
    )
    if cust_age_df is not None and not cust_age_df.is_empty():
        tx_seg = tx_items.join(cust_age_df.lazy(), on='customer_id', how='left').with_columns(
            pl.col('age_bin').fill_null('unknown')
        )
    else:
        tx_seg = tx_items.with_columns(pl.lit('unknown').alias('age_bin'))

    base_counts = (
        tx_seg
        .group_by('age_bin', 'index_group_name', 'node')
        .agg(pl.len().cast(pl.Int64).alias('seg_count'))
        .collect(engine='streaming')
    )

    seg_frames: list[pl.DataFrame] = []
    if not base_counts.is_empty():
        # Level 1: (age_bin, index_group_name) excluding empty index_group_name
        lvl1 = base_counts.filter(pl.col('index_group_name') != '')
        if not lvl1.is_empty():
            seg_frames.append(lvl1)
        # Level 2: (age_bin, '__all__')
        lvl2 = (
            base_counts
            .group_by('age_bin', 'node')
            .agg(pl.col('seg_count').sum())
            .with_columns(pl.lit(_ALL_SEG).alias('index_group_name'))
            .select('age_bin', 'index_group_name', 'node', 'seg_count')
        )
        seg_frames.append(lvl2)
        # Level 3: ('__all__', index_group_name)
        lvl3 = (
            base_counts
            .filter(pl.col('index_group_name') != '')
            .group_by('index_group_name', 'node')
            .agg(pl.col('seg_count').sum())
            .with_columns(pl.lit(_ALL_SEG).alias('age_bin'))
            .select('age_bin', 'index_group_name', 'node', 'seg_count')
        )
        if not lvl3.is_empty():
            seg_frames.append(lvl3)

    # Level 4: ('__all__', '__all__') always populated from pop_all top segment_pop_limit
    lvl4 = (
        pop_all
        .sort(['count_7d', 'node'], descending=[True, False])
        .head(cfg.segment_pop_limit)
        .select(
            pl.lit(_ALL_SEG).alias('age_bin'),
            pl.lit(_ALL_SEG).alias('index_group_name'),
            pl.col('node'),
            pl.col('count_7d').alias('seg_count'),
        )
    )
    seg_frames.append(lvl4)

    seg_pop_df = (
        pl.concat(seg_frames)
        .join(pop_all.select('node', 'article_id', pl.col('count_7d').alias('global_count7d')), on='node', how='left')
        .sort(['age_bin', 'index_group_name', 'seg_count', 'global_count7d', 'node'], descending=[False, False, True, True, False])
        .group_by(['age_bin', 'index_group_name'], maintain_order=True)
        .head(cfg.segment_pop_limit)
        .with_columns(
            pl.int_range(1, pl.len() + 1).over(['age_bin', 'index_group_name']).cast(pl.Int16).alias('rank_seg_pop'),
            pl.col('seg_count').cast(pl.Float64).log1p().cast(pl.Float32).alias('score_seg_pop'),
        )
        .select('age_bin', 'index_group_name', 'article_id', 'node', 'score_seg_pop', 'rank_seg_pop')
    )
    return seg_pop_df, pop7d_df


class MultiCandidateRetriever:
    """Stage-1 5-generator hybrid candidate retriever with batch offline and <40ms online APIs."""

    def __init__(
        self,
        *,
        cutoff: date,
        config: RetrievalConfig,
        catalog: pl.DataFrame,
        latent_index: LatentContentIndex,
        cooc_matrix: sparse.csr_matrix,
        variant_matrix: sparse.csr_matrix,
        user_history_csr: sparse.csr_matrix,
        user_packed: dict[str, int],
        ig_list: tuple[str, ...],
        seg_pop_df: pl.DataFrame,
        pop7d_df: pl.DataFrame,
    ) -> None:
        _validate_cutoff(cutoff)
        self.cutoff: date = cutoff
        self.config: RetrievalConfig = config
        self.catalog: pl.DataFrame = catalog
        self.latent_index: LatentContentIndex = latent_index
        self.cooc_matrix: sparse.csr_matrix = cooc_matrix.tocsr().astype(np.float32, copy=False)
        self.variant_matrix: sparse.csr_matrix = variant_matrix.tocsr().astype(np.float32, copy=False)
        self.user_history_csr: sparse.csr_matrix = user_history_csr.tocsr().astype(np.float32, copy=False)
        self._user_packed: dict[str, int] = user_packed
        self._ig_list: tuple[str, ...] = ig_list
        self._ig_to_code: dict[str, int] = {name: i for i, name in enumerate(ig_list)}
        self.seg_pop_df: pl.DataFrame = seg_pop_df
        self.pop7d_df: pl.DataFrame = pop7d_df

        self.article_ids: tuple[str, ...] = tuple(catalog['article_id'].to_list())
        self.article_to_idx: dict[str, int] = {aid: i for i, aid in enumerate(self.article_ids)}
        self.product_codes: tuple[str | None, ...] = (
            tuple(catalog['product_code'].to_list())
            if 'product_code' in catalog.columns
            else (None,) * len(self.article_ids)
        )
        self.index_groups: tuple[str, ...] = (
            tuple(catalog['index_group_name'].fill_null('').to_list())
            if 'index_group_name' in catalog.columns
            else ('',) * len(self.article_ids)
        )
        self._catalog_nodes_df: pl.DataFrame = catalog.select(
            pl.col('node').cast(pl.Int32),
            pl.col('article_id').cast(pl.String),
        )

        # Build fast integer seg_id lookup for batch retrieve() & numpy arrays for retrieve_online().
        self._seg_key_to_id: dict[tuple[str, str], int] = {}
        self._seg_pop_arrays: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        seg_id_rows: list[pl.DataFrame] = []
        for seg_id, ((age_b, ig_n), part) in enumerate(
            seg_pop_df.partition_by(['age_bin', 'index_group_name'], as_dict=True, maintain_order=True).items()
        ):
            key = (str(age_b), str(ig_n))
            self._seg_key_to_id[key] = seg_id
            nodes_arr = part['node'].to_numpy().astype(np.int32, copy=False)
            scores_arr = part['score_seg_pop'].to_numpy().astype(np.float32, copy=False)
            ranks_arr = part['rank_seg_pop'].to_numpy().astype(np.int16, copy=False)
            self._seg_pop_arrays[key] = (nodes_arr, scores_arr, ranks_arr)
            seg_id_rows.append(
                part.select(
                    pl.lit(seg_id, dtype=pl.Int32).alias('seg_id'),
                    pl.col('node').cast(pl.Int32),
                    pl.col('score_seg_pop').cast(pl.Float32),
                    pl.col('rank_seg_pop').cast(pl.Int16),
                )
            )
        self._seg_pop_indexed: pl.DataFrame = (
            pl.concat(seg_id_rows)
            if seg_id_rows
            else pl.DataFrame(schema={'seg_id': pl.Int32, 'node': pl.Int32, 'score_seg_pop': pl.Float32, 'rank_seg_pop': pl.Int16})
        )

        self._pop7d_nodes: np.ndarray = pop7d_df['node'].to_numpy().astype(np.int32, copy=False)
        self._pop7d_scores: np.ndarray = pop7d_df['score_pop7d'].to_numpy().astype(np.float32, copy=False)
        self._pop7d_ranks: np.ndarray = pop7d_df['rank_pop7d'].to_numpy().astype(np.int16, copy=False)
        self._pop7d_indexed: pl.DataFrame = pop7d_df.select(
            pl.col('node').cast(pl.Int32),
            pl.col('score_pop7d').cast(pl.Float32),
            pl.col('rank_pop7d').cast(pl.Int16),
        )

    @classmethod
    def fit(
        cls,
        dataset: DatasetManifest,
        cutoff: date,
        config: RetrievalConfig | None = None,
    ) -> 'MultiCandidateRetriever':
        _validate_cutoff(cutoff)
        has_customers = any(p.path.startswith('customers/') for p in dataset.partitions)
        return cls.fit_frames(
            scan(dataset, 'transactions'),
            scan(dataset, 'articles'),
            scan(dataset, 'customers') if has_customers else None,
            cutoff=cutoff,
            config=config,
        )

    @classmethod
    def fit_frames(
        cls,
        transactions: pl.LazyFrame | pl.DataFrame,
        articles: pl.LazyFrame | pl.DataFrame,
        customers: pl.LazyFrame | pl.DataFrame | None = None,
        *,
        cutoff: date,
        config: RetrievalConfig | None = None,
    ) -> 'MultiCandidateRetriever':
        _validate_cutoff(cutoff)
        cfg = config if config is not None else RetrievalConfig()
        tx_lf = transactions.lazy() if isinstance(transactions, pl.DataFrame) else transactions
        art_lf = articles.lazy() if isinstance(articles, pl.DataFrame) else articles
        cust_lf = customers.lazy() if isinstance(customers, pl.DataFrame) else customers

        past_tx = tx_lf.filter(pl.col('t_dat') < pl.lit(cutoff))

        art_cols = set(art_lf.collect_schema().names())
        art_exprs: list[pl.Expr] = [pl.col('article_id').cast(pl.String, strict=False)]
        if 'product_code' in art_cols:
            art_exprs.append(pl.col('product_code').cast(pl.String, strict=False).alias('product_code'))
        else:
            art_exprs.append(pl.lit(None, dtype=pl.String).alias('product_code'))
        for field_name in CONTENT_FIELDS:
            if field_name in art_cols:
                art_exprs.append(pl.col(field_name).cast(pl.String, strict=False).fill_null('').alias(field_name))
            else:
                art_exprs.append(pl.lit('', dtype=pl.String).alias(field_name))
        norm_articles = art_lf.select(art_exprs)

        catalog = (
            past_tx
            .group_by('article_id')
            .agg(pl.col('t_dat').min().alias('first_seen'))
            .join(norm_articles, on='article_id', how='left')
            .with_columns(*[pl.col(c).fill_null('').alias(c) for c in CONTENT_FIELDS])
            .sort('article_id')
            .with_row_index('node')
            .with_columns(pl.col('node').cast(pl.Int32))
            .collect(engine='streaming')
        )
        if catalog.is_empty():
            raise ValueError('No eligible catalog before cutoff')

        cust_age_df: pl.DataFrame | None = None
        if cust_lf is not None:
            cust_cols = set(cust_lf.collect_schema().names())
            if 'customer_id' in cust_cols and 'age' in cust_cols:
                cust_age_df = (
                    cust_lf
                    .select(pl.col('customer_id').cast(pl.String, strict=False), age_bin_expr('age'))
                    .filter(pl.col('customer_id').is_not_null())
                    .unique('customer_id', keep='first')
                    .filter(pl.col('age_bin') != 'unknown')
                    .sort('customer_id')
                    .collect(engine='streaming')
                )

        # Generator 1: history_decay & 180d user_history_csr
        hist_lower = cutoff - timedelta(days=cfg.history_days)
        days_expr = (pl.lit(cutoff) - pl.col('t_dat')).dt.total_days().cast(pl.Float64)
        decay_expr = (
            pl.lit(float(cfg.history_short_weight), dtype=pl.Float64)
            * (-days_expr / pl.lit(float(cfg.history_tau_short), dtype=pl.Float64)).exp()
            + pl.lit(float(cfg.history_long_weight), dtype=pl.Float64)
            * (-days_expr / pl.lit(float(cfg.history_tau_long), dtype=pl.Float64)).exp()
        )

        hist_df = (
            past_tx
            .filter((pl.col('t_dat') >= pl.lit(hist_lower)) & pl.col('customer_id').is_not_null())
            .join(catalog.select('article_id', 'node').lazy(), on='article_id', how='inner')
            .group_by('customer_id', 'node', 't_dat')
            .agg(pl.len().cast(pl.Float64).alias('cnt'))
            .sort('customer_id', 'node', 't_dat')
            .with_columns((decay_expr * pl.col('cnt')).alias('decay'))
            .group_by('customer_id', 'node', maintain_order=True)
            .agg(pl.col('decay').sum().cast(pl.Float32).alias('score'))
            .sort('customer_id', 'node')
            .collect(engine='streaming')
        )

        n_articles = catalog.height
        user_packed: dict[str, int] = {}
        distinct_igs = sorted({ig for ig in catalog['index_group_name'].to_list() if ig})
        ig_list = ('',) + tuple(distinct_igs)
        ig_to_code = {name: i for i, name in enumerate(ig_list)}

        if hist_df.is_empty():
            user_history_csr = sparse.csr_matrix((0, n_articles), dtype=np.float32)
        else:
            users_hist = (
                hist_df
                .select('customer_id')
                .unique()
                .sort('customer_id')
                .with_row_index('row')
                .with_columns(pl.col('row').cast(pl.Int32))
            )
            mapped_hist = hist_df.join(users_hist, on='customer_id', how='left').select('row', 'node', 'score')
            r_arr = mapped_hist['row'].to_numpy().astype(np.int32, copy=False)
            n_arr = mapped_hist['node'].to_numpy().astype(np.int32, copy=False)
            s_arr = mapped_hist['score'].to_numpy().astype(np.float32, copy=False)
            user_history_csr = sparse.csr_matrix(
                (s_arr, (r_arr, n_arr)),
                shape=(users_hist.height, n_articles),
                dtype=np.float32,
            )
            user_history_csr.sum_duplicates()
            user_history_csr.sort_indices()
            del mapped_hist, r_arr, n_arr, s_arr

            for cid, row_id in zip(users_hist['customer_id'].to_list(), range(users_hist.height)):
                user_packed[cid] = _pack_user(row_id, 0, _UNKNOWN_AGE_CODE)
            del users_hist

            pref_ig_df = (
                hist_df
                .join(catalog.select('node', 'index_group_name'), on='node', how='left')
                .filter(pl.col('index_group_name') != '')
                .sort(['customer_id', 'index_group_name', 'node'])
                .group_by('customer_id', 'index_group_name', maintain_order=True)
                .agg(pl.col('score').cast(pl.Float64).sum().round(6).alias('ig_score'))
                .sort(['customer_id', 'ig_score', 'index_group_name'], descending=[False, True, False])
                .group_by('customer_id', maintain_order=True)
                .first()
            )
            for cid, ig_name in zip(pref_ig_df['customer_id'].to_list(), pref_ig_df['index_group_name'].to_list()):
                packed = user_packed.get(cid, _pack_user(-1, 0, _UNKNOWN_AGE_CODE))
                h_row, _, age_c = _unpack_user(packed)
                user_packed[cid] = _pack_user(h_row, ig_to_code.get(ig_name, 0), age_c)
            del pref_ig_df

        del hist_df

        if cust_age_df is not None and not cust_age_df.is_empty():
            for cid, age_b in zip(cust_age_df['customer_id'].to_list(), cust_age_df['age_bin'].to_list()):
                age_c = _AGE_BIN_TO_CODE.get(age_b, _UNKNOWN_AGE_CODE)
                packed = user_packed.get(cid)
                if packed is None:
                    user_packed[cid] = _pack_user(-1, 0, age_c)
                else:
                    h_row, ig_c, _ = _unpack_user(packed)
                    user_packed[cid] = _pack_user(h_row, ig_c, age_c)

        # Generator 2: product_code_variants
        variant_matrix = _build_variant_matrix(catalog, past_tx, cutoff, cfg)

        # Generator 3: cooccurrence
        cooc_matrix = _build_cooccurrence_matrix(catalog, past_tx, cutoff, cfg)

        # Generator 4: latent & content
        latent_index = LatentContentIndex.fit(
            past_tx,
            catalog,
            cutoff,
            window_days=cfg.latent_window_days,
            half_life_days=cfg.latent_half_life_days,
            k=cfg.latent_dim,
            max_vocab=cfg.content_max_vocab,
        )

        # Generator 5: segment_popularity & pop7d
        seg_pop_df, pop7d_df = _build_popularity_tables(catalog, past_tx, cust_age_df, cutoff, cfg)

        return cls(
            cutoff=cutoff,
            config=cfg,
            catalog=catalog,
            latent_index=latent_index,
            cooc_matrix=cooc_matrix,
            variant_matrix=variant_matrix,
            user_history_csr=user_history_csr,
            user_packed=user_packed,
            ig_list=ig_list,
            seg_pop_df=seg_pop_df,
            pop7d_df=pop7d_df,
        )

    from_frames = fit_frames

    def _resolve_seg_key(self, age_bin: str, pref_ig: str) -> tuple[str, str]:
        if pref_ig:
            k1 = (age_bin, pref_ig)
            if k1 in self._seg_key_to_id:
                return k1
            k2 = (_ALL_SEG, pref_ig)
            if k2 in self._seg_key_to_id:
                return k2
        k3 = (age_bin, _ALL_SEG)
        if k3 in self._seg_key_to_id:
            return k3
        return (_ALL_SEG, _ALL_SEG)

    def retrieve(
        self,
        customers: Sequence[str] | pl.Series | pl.DataFrame,
        *,
        top_k_per_generator: Mapping[str, int] | None = None,
    ) -> pl.DataFrame:
        cfg = self.config
        limits: dict[str, int] = {
            'history_decay': cfg.history_limit,
            'product_code_variants': cfg.variant_limit,
            'cooccurrence': cfg.cooc_limit,
            'latent': cfg.latent_limit,
            'content': cfg.content_limit,
            'segment_popularity': cfg.segment_pop_limit,
            'pop7d': cfg.global_pop_limit,
        }
        if top_k_per_generator is not None:
            for gen_name, val in top_k_per_generator.items():
                if gen_name not in GENERATOR_NAMES:
                    raise ValueError(f'Unknown generator: {gen_name}')
                if isinstance(val, bool) or not isinstance(val, int) or val < 1 or val >= cfg.missing_rank_sentinel:
                    raise ValueError(f'Invalid top_k for {gen_name}: {val}')
                limits[gen_name] = val

        if isinstance(customers, pl.DataFrame):
            if 'customer_id' not in customers.columns:
                raise ValueError('customers DataFrame must contain customer_id column')
            cust_list = customers['customer_id'].cast(pl.String, strict=False).drop_nulls().unique().sort().to_list()
        elif isinstance(customers, pl.Series):
            cust_list = customers.cast(pl.String, strict=False).drop_nulls().unique().sort().to_list()
        else:
            cust_list = sorted({str(c) for c in customers if c is not None})

        if not cust_list:
            return pl.DataFrame(schema=CANDIDATE_SCHEMA)

        sentinel = cfg.missing_rank_sentinel
        chunk_outputs: list[pl.DataFrame] = []

        for start in range(0, len(cust_list), cfg.user_batch_size):
            batch_custs = cust_list[start:start + cfg.user_batch_size]
            batch_len = len(batch_custs)

            hist_u_list: list[int] = []
            hist_row_list: list[int] = []
            seg_id_arr = np.empty(batch_len, dtype=np.int32)

            for u_idx, cid in enumerate(batch_custs):
                packed = self._user_packed.get(cid)
                if packed is None:
                    age_b = 'unknown'
                    pref_ig = ''
                else:
                    h_row, ig_c, age_c = _unpack_user(packed)
                    if h_row >= 0:
                        hist_u_list.append(u_idx)
                        hist_row_list.append(h_row)
                    age_b = AGE_BINS[age_c] if age_c < len(AGE_BINS) else 'unknown'
                    pref_ig = self._ig_list[ig_c] if ig_c < len(self._ig_list) else ''
                seg_key = self._resolve_seg_key(age_b, pref_ig)
                seg_id_arr[u_idx] = self._seg_key_to_id[seg_key]

            frames: list[pl.DataFrame] = []
            if hist_u_list:
                hist_u = np.asarray(hist_u_list, dtype=np.uint32)
                sub_csr = self.user_history_csr[np.asarray(hist_row_list, dtype=np.int32)]

                # 1. history_decay
                u1, n1, s1, r1 = _extract_csr_topk(sub_csr, hist_u, limits['history_decay'])
                if u1.size:
                    frames.append(_make_gen_frame(u1, n1, s1, r1, 'score_history_decay', 'rank_history_decay'))

                # 2. product_code_variants (excludes already purchased)
                if self.variant_matrix.nnz > 0:
                    var_csr = sub_csr @ self.variant_matrix
                    u2, n2, s2, r2 = _extract_csr_topk(var_csr, hist_u, limits['product_code_variants'], exclude_csr=sub_csr)
                    if u2.size:
                        frames.append(_make_gen_frame(u2, n2, s2, r2, 'score_variant', 'rank_variant'))

                # 3. cooccurrence (L1-normalized seed @ cooc_matrix)
                if self.cooc_matrix.nnz > 0:
                    seed_norm = _row_l1_normalize(sub_csr)
                    cooc_csr = seed_norm @ self.cooc_matrix
                    u3, n3, s3, r3 = _extract_csr_topk(cooc_csr, hist_u, limits['cooccurrence'])
                    if u3.size:
                        frames.append(_make_gen_frame(u3, n3, s3, r3, 'score_cooc', 'rank_cooc'))

                # 4a. latent (batch_size=512 inside LatentContentIndex)
                if self.latent_index.k > 0:
                    lat_idx, lat_sc = self.latent_index.batch_query_latent(
                        sub_csr,
                        top_k=limits['latent'],
                        exclude_purchased=True,
                        batch_size=512,
                    )
                    u4, n4, s4, r4 = _dense_topk_to_arrays(hist_u, lat_idx, lat_sc)
                    if u4.size:
                        frames.append(_make_gen_frame(u4, n4, s4, r4, 'score_latent', 'rank_latent'))

                # 4b. content (batch_size=512 inside LatentContentIndex)
                if self.latent_index.vocab_size > 0:
                    con_idx, con_sc = self.latent_index.batch_query_content(
                        sub_csr,
                        top_k=limits['content'],
                        exclude_purchased=True,
                        batch_size=512,
                    )
                    u5, n5, s5, r5 = _dense_topk_to_arrays(hist_u, con_idx, con_sc)
                    if u5.size:
                        frames.append(_make_gen_frame(u5, n5, s5, r5, 'score_content', 'rank_content'))

            # 5a. segment_popularity
            batch_seg_df = pl.DataFrame({
                '_u_idx': pl.Series('_u_idx', np.arange(batch_len, dtype=np.uint32), dtype=pl.UInt32),
                'seg_id': pl.Series('seg_id', seg_id_arr, dtype=pl.Int32),
            })
            seg_cand = (
                batch_seg_df
                .join(
                    self._seg_pop_indexed.filter(pl.col('rank_seg_pop') <= limits['segment_popularity']),
                    on='seg_id',
                    how='inner',
                )
                .drop('seg_id')
            )
            if not seg_cand.is_empty():
                frames.append(seg_cand)

            # 5b. global pop7d
            pop_cand = (
                pl.DataFrame({'_u_idx': pl.Series('_u_idx', np.arange(batch_len, dtype=np.uint32), dtype=pl.UInt32)})
                .join(
                    self._pop7d_indexed.filter(pl.col('rank_pop7d') <= limits['pop7d']),
                    how='cross',
                )
            )
            if not pop_cand.is_empty():
                frames.append(pop_cand)

            if not frames:
                continue

            concatted = pl.concat(frames, how='diagonal')
            present_cols = set(concatted.columns)
            agg_exprs: list[pl.Expr] = [
                pl.col(c).max().fill_null(0.0).cast(pl.Float32).alias(c)
                for c in SCORE_COLUMNS
                if c in present_cols
            ] + [
                pl.col(c).min().fill_null(sentinel).cast(pl.Int16).alias(c)
                for c in RANK_COLUMNS
                if c in present_cols
            ]
            missing_exprs: list[pl.Expr] = [
                pl.lit(0.0, dtype=pl.Float32).alias(c)
                for c in SCORE_COLUMNS
                if c not in present_cols
            ] + [
                pl.lit(sentinel, dtype=pl.Int16).alias(c)
                for c in RANK_COLUMNS
                if c not in present_cols
            ]
            combined = concatted.group_by('_u_idx', 'node').agg(agg_exprs)
            if missing_exprs:
                combined = combined.with_columns(missing_exprs)
            combined = combined.sort('_u_idx', 'node')

            batch_cust_map = pl.DataFrame({
                '_u_idx': pl.Series('_u_idx', np.arange(batch_len, dtype=np.uint32), dtype=pl.UInt32),
                'customer_id': pl.Series('customer_id', batch_custs, dtype=pl.String),
            })
            mapped_chunk = (
                combined
                .join(batch_cust_map, on='_u_idx', how='left')
                .join(self._catalog_nodes_df, on='node', how='left')
                .select(list(CANDIDATE_SCHEMA.keys()))
            )
            chunk_outputs.append(mapped_chunk)

        if not chunk_outputs:
            return pl.DataFrame(schema=CANDIDATE_SCHEMA)
        if len(chunk_outputs) == 1:
            return chunk_outputs[0]
        return pl.concat(chunk_outputs, rechunk=False)

    def retrieve_online(
        self,
        customer_id: str | None = None,
        *,
        historical_items: Sequence[tuple[str, float]] | Sequence[str] | None = None,
        live_events: Sequence[tuple[str, str]] | Sequence[Mapping[str, object]] | None = None,
        preferences: Mapping[str, str | None] | None = None,
        mode: Literal['balanced', 'discovery'] = 'balanced',
        eligible_articles: AbstractSet[str] | None = None,
        top_k: int | None = None,
    ) -> pl.DataFrame:
        if mode not in ('balanced', 'discovery'):
            raise ValueError(f'Unsupported mode: {mode}')
        if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0):
            raise ValueError('top_k must be >= 0')

        cfg = self.config
        sentinel = cfg.missing_rank_sentinel
        purchase_weights: dict[int, float] = {}
        all_weights: dict[int, float] = {}

        if historical_items is None:
            if customer_id is not None and customer_id in self._user_packed:
                h_row, _, _ = _unpack_user(self._user_packed[customer_id])
                if h_row >= 0:
                    s_pos = int(self.user_history_csr.indptr[h_row])
                    e_pos = int(self.user_history_csr.indptr[h_row + 1])
                    for node_i, val_f in zip(
                        self.user_history_csr.indices[s_pos:e_pos],
                        self.user_history_csr.data[s_pos:e_pos],
                    ):
                        node_int = int(node_i)
                        w_f = float(val_f)
                        if w_f > 0.0:
                            purchase_weights[node_int] = purchase_weights.get(node_int, 0.0) + w_f
                            all_weights[node_int] = all_weights.get(node_int, 0.0) + w_f
        else:
            for item in historical_items:
                if isinstance(item, str):
                    art_id, w_f = item, 1.0
                else:
                    art_id, w_f = str(item[0]), float(item[1])
                node_idx = self.article_to_idx.get(art_id)
                if node_idx is not None and math.isfinite(w_f) and w_f > 0.0:
                    purchase_weights[node_idx] = purchase_weights.get(node_idx, 0.0) + w_f
                    all_weights[node_idx] = all_weights.get(node_idx, 0.0) + w_f

        if live_events is not None:
            for ev in live_events:
                if isinstance(ev, Mapping):
                    art_id = str(ev.get('article_id', ''))
                    kind = str(ev.get('kind', ''))
                    raw_w = ev.get('weight')
                    mult = 1.0 if raw_w is None else float(raw_w)  # type: ignore[arg-type]
                else:
                    art_id = str(ev[0])
                    kind = str(ev[1])
                    mult = 1.0
                if kind not in EVENT_KIND_WEIGHTS:
                    raise ValueError(f'Unknown live event kind: {kind}')
                w_f = EVENT_KIND_WEIGHTS[kind] * mult
                node_idx = self.article_to_idx.get(art_id)
                if node_idx is not None and math.isfinite(w_f) and w_f > 0.0:
                    all_weights[node_idx] = all_weights.get(node_idx, 0.0) + w_f
                    if kind == 'purchase':
                        purchase_weights[node_idx] = purchase_weights.get(node_idx, 0.0) + w_f

        # Pool accumulator: node -> [7 scores, 7 ranks]
        pool: dict[int, tuple[list[float], list[int]]] = {}

        def _ensure_node(node_i: int) -> tuple[list[float], list[int]]:
            entry = pool.get(node_i)
            if entry is None:
                entry = ([0.0] * 7, [sentinel] * 7)
                pool[node_i] = entry
            return entry

        # 1. history_decay (purchases only)
        if purchase_weights:
            p_nodes = np.fromiter(purchase_weights.keys(), dtype=np.int32)
            p_vals = np.fromiter(purchase_weights.values(), dtype=np.float32)
            order = np.lexsort((p_nodes, -p_vals))[:cfg.history_limit]
            for rank_i, pos_i in enumerate(order, start=1):
                sc_list, rk_list = _ensure_node(int(p_nodes[pos_i]))
                sc_list[0] = float(p_vals[pos_i])
                rk_list[0] = rank_i

        # 2-4. Personalized expansion from combined purchase + click + view weights
        if all_weights:
            all_nodes = np.fromiter(all_weights.keys(), dtype=np.int32)
            all_vals = np.fromiter(all_weights.values(), dtype=np.float32)
            seed_csr = sparse.csr_matrix(
                (all_vals, (np.zeros(all_nodes.size, dtype=np.int32), all_nodes)),
                shape=(1, len(self.article_ids)),
                dtype=np.float32,
            )
            purchased_nodes = np.fromiter(purchase_weights.keys(), dtype=np.int32) if purchase_weights else None

            # 2. product_code_variants
            if self.variant_matrix.nnz > 0:
                var_row = (seed_csr @ self.variant_matrix).tocsr()
                if var_row.nnz > 0:
                    cols = var_row.indices
                    vals = var_row.data
                    mask = vals > 0.0
                    if purchased_nodes is not None and purchased_nodes.size:
                        mask = mask & (~np.isin(cols, purchased_nodes, assume_unique=False))
                    cols = cols[mask]
                    vals = vals[mask]
                    if cols.size:
                        order = np.lexsort((cols, -vals))[:cfg.variant_limit]
                        for rank_i, pos_i in enumerate(order, start=1):
                            sc_list, rk_list = _ensure_node(int(cols[pos_i]))
                            sc_list[1] = float(vals[pos_i])
                            rk_list[1] = rank_i

            # 3. cooccurrence
            if self.cooc_matrix.nnz > 0:
                seed_norm = _row_l1_normalize(seed_csr)
                cooc_row = (seed_norm @ self.cooc_matrix).tocsr()
                if cooc_row.nnz > 0:
                    cols = cooc_row.indices
                    vals = cooc_row.data
                    pos_mask = vals > 0.0
                    if not np.all(pos_mask):
                        cols = cols[pos_mask]
                        vals = vals[pos_mask]
                    if cols.size:
                        order = np.lexsort((cols, -vals))[:cfg.cooc_limit]
                        for rank_i, pos_i in enumerate(order, start=1):
                            sc_list, rk_list = _ensure_node(int(cols[pos_i]))
                            sc_list[2] = float(vals[pos_i])
                            rk_list[2] = rank_i

            # 4a. latent fold-in + query
            if self.latent_index.k > 0:
                u_vec = self.latent_index.fold_in_user(all_nodes, all_vals)
                l_idx, l_sc = self.latent_index.query_latent(
                    u_vec,
                    top_k=cfg.latent_limit,
                    exclude_indices=purchased_nodes,
                )
                for rank_i, (ni, sc) in enumerate(zip(l_idx, l_sc), start=1):
                    sc_list, rk_list = _ensure_node(int(ni))
                    sc_list[3] = float(sc)
                    rk_list[3] = rank_i

            # 4b. content similarity
            if self.latent_index.vocab_size > 0:
                c_idx, c_sc = self.latent_index.query_content(
                    all_nodes,
                    all_vals,
                    top_k=cfg.content_limit,
                    exclude_indices=purchased_nodes,
                )
                for rank_i, (ni, sc) in enumerate(zip(c_idx, c_sc), start=1):
                    sc_list, rk_list = _ensure_node(int(ni))
                    sc_list[4] = float(sc)
                    rk_list[4] = rank_i

        # 5a. segment_popularity (preferences override stored user metadata)
        age_b = 'unknown'
        pref_ig = ''
        if customer_id is not None and customer_id in self._user_packed:
            _, ig_c, age_c = _unpack_user(self._user_packed[customer_id])
            age_b = AGE_BINS[age_c] if age_c < len(AGE_BINS) else 'unknown'
            pref_ig = self._ig_list[ig_c] if ig_c < len(self._ig_list) else ''
        if preferences is not None:
            if preferences.get('age_bin'):
                cand_ab = str(preferences['age_bin'])
                age_b = cand_ab if cand_ab in _AGE_BIN_TO_CODE else 'unknown'
            elif preferences.get('age') is not None:
                age_b = _bin_age_scalar(preferences['age'])
            if preferences.get('index_group_name'):
                pref_ig = str(preferences['index_group_name'])

        seg_key = self._resolve_seg_key(age_b, pref_ig)
        seg_nodes, seg_scores, seg_ranks = self._seg_pop_arrays[seg_key]
        for ni, sc, rk in zip(
            seg_nodes[:cfg.segment_pop_limit],
            seg_scores[:cfg.segment_pop_limit],
            seg_ranks[:cfg.segment_pop_limit],
        ):
            sc_list, rk_list = _ensure_node(int(ni))
            sc_list[5] = float(sc)
            rk_list[5] = int(rk)

        # 5b. pop7d
        for ni, sc, rk in zip(
            self._pop7d_nodes[:cfg.global_pop_limit],
            self._pop7d_scores[:cfg.global_pop_limit],
            self._pop7d_ranks[:cfg.global_pop_limit],
        ):
            sc_list, rk_list = _ensure_node(int(ni))
            sc_list[6] = float(sc)
            rk_list[6] = int(rk)

        # Filter, attribute primary_reason, sort deterministically, and apply discovery / top_k rules.
        ranked_records: list[tuple[tuple[int, int, float, str], int, str, list[float], list[int], str]] = []
        for node_i, (sc_list, rk_list) in pool.items():
            art_id = self.article_ids[node_i]
            if eligible_articles is not None and art_id not in eligible_articles:
                continue
            if mode == 'discovery' and node_i in purchase_weights:
                continue

            primary_reason = 'popular_7d'
            reason_prio = len(REASON_PRIORITY) - 1
            for prio_idx, (rk_idx, reason_tag) in enumerate(
                zip(range(len(REASON_PRIORITY)), EXPLAINABLE_REASONS)
            ):
                if rk_list[rk_idx] < sentinel:
                    primary_reason = reason_tag
                    reason_prio = prio_idx
                    break

            best_rank = min(rk_list)
            best_score = max(sc_list)
            sort_key = (best_rank, reason_prio, -best_score, art_id)
            ranked_records.append((sort_key, node_i, art_id, sc_list, rk_list, primary_reason))

        ranked_records.sort(key=lambda rec: rec[0])

        if mode == 'discovery':
            deduped_records = []
            seen_codes: set[str] = set()
            for rec in ranked_records:
                p_code = self.product_codes[rec[1]]
                if p_code:
                    if p_code in seen_codes:
                        continue
                    seen_codes.add(p_code)
                deduped_records.append(rec)
            ranked_records = deduped_records

        if top_k is not None:
            ranked_records = ranked_records[:top_k]

        online_schema = {**CANDIDATE_SCHEMA, 'primary_reason': pl.String}
        if not ranked_records:
            return pl.DataFrame(schema=online_schema)

        cid_out = customer_id if customer_id is not None else '__online__'
        n_rows = len(ranked_records)
        data: dict[str, pl.Series] = {
            'customer_id': pl.Series('customer_id', [cid_out] * n_rows, dtype=pl.String),
            'article_id': pl.Series('article_id', [rec[2] for rec in ranked_records], dtype=pl.String),
        }
        for idx, col in enumerate(SCORE_COLUMNS):
            data[col] = pl.Series(col, [rec[3][idx] for rec in ranked_records], dtype=pl.Float32)
        for idx, col in enumerate(RANK_COLUMNS):
            data[col] = pl.Series(col, [rec[4][idx] for rec in ranked_records], dtype=pl.Int16)
        data['primary_reason'] = pl.Series('primary_reason', [rec[5] for rec in ranked_records], dtype=pl.String)
        return pl.DataFrame(data)

    def export_artifacts(
        self,
        directory: str | Path,
        *,
        include_retriever_state: bool = True,
    ) -> dict[str, Path]:
        out_dir = Path(directory)
        out_dir.mkdir(parents=True, exist_ok=True)

        artifacts: dict[str, Path] = {}
        cooc_path = out_dir / 'cooc.npz'
        sparse.save_npz(cooc_path, self.cooc_matrix.astype(np.float32, copy=False))
        artifacts['cooc'] = cooc_path

        seg_path = out_dir / 'segment_popularity.parquet'
        self.seg_pop_df.write_parquet(seg_path)
        artifacts['segment_popularity'] = seg_path

        artifacts.update(self.latent_index.save(out_dir))

        if include_retriever_state:
            cat_path = out_dir / 'catalog_index.parquet'
            self.catalog.select('node', 'article_id', 'product_code', 'index_group_name').write_parquet(cat_path)
            artifacts['catalog_index'] = cat_path

            var_path = out_dir / 'variant_matrix.npz'
            sparse.save_npz(var_path, self.variant_matrix.astype(np.float32, copy=False))
            artifacts['variant_matrix'] = var_path

            pop_path = out_dir / 'pop7d.parquet'
            self.pop7d_df.write_parquet(pop_path)
            artifacts['pop7d'] = pop_path

            hist_path = out_dir / 'user_histories.npz'
            sparse.save_npz(hist_path, self.user_history_csr.astype(np.float32, copy=False))
            artifacts['user_histories'] = hist_path

            cids: list[str] = []
            h_rows: list[int] = []
            age_bins: list[str] = []
            pref_igs: list[str] = []
            for cid, packed in self._user_packed.items():
                h_row, ig_c, age_c = _unpack_user(packed)
                cids.append(cid)
                h_rows.append(h_row)
                age_bins.append(AGE_BINS[age_c] if age_c < len(AGE_BINS) else 'unknown')
                pref_igs.append(self._ig_list[ig_c] if ig_c < len(self._ig_list) else '')
            user_meta_df = pl.DataFrame({
                'customer_id': pl.Series('customer_id', cids, dtype=pl.String),
                'hist_row': pl.Series('hist_row', h_rows, dtype=pl.Int32),
                'age_bin': pl.Series('age_bin', age_bins, dtype=pl.String),
                'pref_ig': pl.Series('pref_ig', pref_igs, dtype=pl.String),
            })
            user_meta_path = out_dir / 'user_meta.parquet'
            user_meta_df.write_parquet(user_meta_path)
            artifacts['user_meta'] = user_meta_path

            cfg_path = out_dir / 'retriever_config.json'
            cfg_path.write_text(
                json.dumps({'cutoff': self.cutoff.isoformat(), 'config': asdict(self.config)})
            )
            artifacts['retriever_config'] = cfg_path

        return artifacts

    @classmethod
    def load_artifacts(cls, directory: str | Path) -> 'MultiCandidateRetriever':
        in_dir = Path(directory)
        cfg_payload = json.loads((in_dir / 'retriever_config.json').read_text())
        cutoff = date.fromisoformat(cfg_payload['cutoff'])
        config = RetrievalConfig(**cfg_payload['config'])

        catalog = pl.read_parquet(in_dir / 'catalog_index.parquet')
        latent_index = LatentContentIndex.load(in_dir, article_ids=catalog['article_id'].to_list())
        cooc_matrix = sparse.load_npz(in_dir / 'cooc.npz').tocsr().astype(np.float32, copy=False)
        variant_matrix = sparse.load_npz(in_dir / 'variant_matrix.npz').tocsr().astype(np.float32, copy=False)
        user_history_csr = sparse.load_npz(in_dir / 'user_histories.npz').tocsr().astype(np.float32, copy=False)
        seg_pop_df = pl.read_parquet(in_dir / 'segment_popularity.parquet')
        pop7d_df = pl.read_parquet(in_dir / 'pop7d.parquet')

        user_meta_df = pl.read_parquet(in_dir / 'user_meta.parquet')
        distinct_igs = sorted({ig for ig in catalog['index_group_name'].fill_null('').to_list() if ig})
        ig_list = ('',) + tuple(distinct_igs)
        ig_to_code = {name: i for i, name in enumerate(ig_list)}

        user_packed: dict[str, int] = {}
        if not user_meta_df.is_empty():
            for cid, h_row, age_b, pref_ig in user_meta_df.iter_rows():
                age_c = _AGE_BIN_TO_CODE.get(age_b, _UNKNOWN_AGE_CODE)
                ig_c = ig_to_code.get(pref_ig, 0)
                if ig_c == 0 and pref_ig:
                    # Preserve unseen ig if present
                    ig_to_code[pref_ig] = len(ig_list)
                    ig_list = ig_list + (pref_ig,)
                    ig_c = ig_to_code[pref_ig]
                user_packed[str(cid)] = _pack_user(int(h_row), ig_c, age_c)

        return cls(
            cutoff=cutoff,
            config=config,
            catalog=catalog,
            latent_index=latent_index,
            cooc_matrix=cooc_matrix,
            variant_matrix=variant_matrix,
            user_history_csr=user_history_csr,
            user_packed=user_packed,
            ig_list=ig_list,
            seg_pop_df=seg_pop_df,
            pop7d_df=pop7d_df,
        )
