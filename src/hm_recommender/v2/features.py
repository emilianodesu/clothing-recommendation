"""Stage-2 temporal feature engineering (strictly < cutoff) for H&M hybrid recommender v2."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
import math

import numpy as np
import polars as pl
import pyarrow as pa
from scipy import sparse

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import scan
from hm_recommender.v2.retrieval import (
    AGE_BINS,
    EVENT_KIND_WEIGHTS,
    MISSING_RANK_SENTINEL,
    RANK_COLUMNS,
    SCORE_COLUMNS,
    age_bin_expr,
    cast_age_expr,
)

V2_FEATURE_VERSION: int = 2

AFFINITY_ATTR_FIELDS: tuple[str, ...] = (
    'product_type_name',
    'product_group_name',
    'colour_group_name',
    'department_no',
    'index_group_name',
    'graphical_appearance_name',
)

CATALOG_COLUMNS: tuple[str, ...] = (
    'article_id',
    'first_seen',
    'product_code',
    'prod_name',
    'product_type_name',
    'product_group_name',
    'colour_group_name',
    'graphical_appearance_name',
    'index_group_name',
    'department_no',
    'garment_group_name',
    'detail_desc',
)

_USER_STAT_FEATURE_COLS: tuple[str, ...] = (
    'user_age',
    'user_age_missing',
    'user_club_active',
    'user_news_freq_code',
    'user_fn_active',
    'user_active_flag',
    'user_count7',
    'user_count28',
    'user_count90',
    'user_count180',
    'user_recency',
    'user_avg_price',
    'user_std_price',
    'user_online_ratio',
    'user_n_unique_prodcodes',
)

_ITEM_STAT_FEATURE_COLS: tuple[str, ...] = (
    'item_price',
    'item_count7',
    'item_count14',
    'item_count28',
    'item_count90',
    'item_trend_7_28',
    'item_trend_14_90',
    'item_online_ratio',
    'item_mean_age',
    'item_std_age',
    'item_repurchase_rate',
    'item_days_since_first_seen',
)

_PAIR_FEATURE_COLS: tuple[str, ...] = (
    'pair_count7',
    'pair_count28',
    'pair_count90',
    'pair_count180',
    'pair_recency',
    'pair_decay_score',
    'prodcode_count90',
    'prodcode_recency',
)

_AFFINITY_FEATURE_COLS: tuple[str, ...] = (
    'abs_age_diff',
    'price_ratio',
    'channel_affinity',
    'aff_product_type',
    'aff_product_group',
    'aff_colour_group',
    'aff_department',
    'aff_index_group',
    'aff_graphical_appearance',
)

_STAGE1_EXTRA_COLS: tuple[str, ...] = (
    'n_generators_hit',
    'min_gen_rank',
    'latent_x_cooc',
)

V2_FEATURE_NAMES: tuple[str, ...] = (
    *_USER_STAT_FEATURE_COLS,
    *_ITEM_STAT_FEATURE_COLS,
    *_PAIR_FEATURE_COLS,
    *_AFFINITY_FEATURE_COLS,
    *SCORE_COLUMNS,
    *RANK_COLUMNS,
    *_STAGE1_EXTRA_COLS,
)

_AGE_BIN_MIDPOINTS: dict[str, float] = {
    '<25': 21.0,
    '25-34': 29.5,
    '35-44': 39.5,
    '45-54': 49.5,
    '55+': 62.0,
}

_USER_DEFAULTS = np.array(
    [
        -1.0,   # user_age
        1.0,    # user_age_missing
        0.0,    # user_club_active
        0.0,    # user_news_freq_code
        0.0,    # user_fn_active
        0.0,    # user_active_flag
        0.0,    # user_count7
        0.0,    # user_count28
        0.0,    # user_count90
        0.0,    # user_count180
        181.0,  # user_recency
        0.0,    # user_avg_price
        0.0,    # user_std_price
        0.5,    # user_online_ratio
        0.0,    # user_n_unique_prodcodes
    ],
    dtype=np.float32,
)

_ITEM_DEFAULTS = np.array(
    [
        0.0,   # item_price
        0.0,   # item_count7
        0.0,   # item_count14
        0.0,   # item_count28
        0.0,   # item_count90
        0.0,   # item_trend_7_28
        0.0,   # item_trend_14_90
        0.5,   # item_online_ratio
        -1.0,  # item_mean_age
        0.0,   # item_std_age
        0.0,   # item_repurchase_rate
        0.0,   # item_days_since_first_seen
    ],
    dtype=np.float32,
)


def _validate_cutoff(cutoff: date) -> None:
    if type(cutoff) is not date:
        raise ValueError('Explicit date cutoff required')


def _parse_valid_age(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val < 10.0 or val > 110.0:
        return None
    return val


def _valid_age_expr(col: str = 'age') -> pl.Expr:
    age = cast_age_expr(col)
    return (
        pl.when(age.is_null() | age.is_nan() | age.is_infinite() | (age < 10.0) | (age > 110.0))
        .then(pl.lit(None, dtype=pl.Float32))
        .otherwise(age)
    )


def _parse_flag_expr(col: str, alias: str) -> pl.Expr:
    s = pl.col(col).cast(pl.String, strict=False).str.strip_chars().str.to_lowercase()
    num = s.cast(pl.Float64, strict=False)
    return (
        pl.when(
            s.is_in(['true', 'yes'])
            | (num.is_not_null() & num.is_finite() & (num > 0.0))
        )
        .then(pl.lit(1, dtype=pl.Int8))
        .otherwise(pl.lit(0, dtype=pl.Int8))
        .alias(alias)
    )


def _build_attr_vocab(
    catalog: pl.DataFrame,
) -> tuple[np.ndarray, int, dict[str, dict[str, int]]]:
    """Assign deterministic UInt16 attribute codes across AFFINITY_ATTR_FIELDS."""
    n_items = catalog.height
    item_attr_codes = np.full((n_items + 1, len(AFFINITY_ATTR_FIELDS)), -1, dtype=np.int32)
    field_maps: dict[str, dict[str, int]] = {}
    next_code = 0
    for f_idx, field in enumerate(AFFINITY_ATTR_FIELDS):
        if field in catalog.columns:
            col_vals = catalog[field].cast(pl.String, strict=False).fill_null('').to_list()
        else:
            col_vals = [''] * n_items
        distinct = sorted({v for v in col_vals if v != ''})
        vmap = {v: next_code + offset for offset, v in enumerate(distinct)}
        next_code += len(distinct)
        field_maps[field] = vmap
        if vmap:
            mapped = [vmap.get(v, -1) if v != '' else -1 for v in col_vals]
            item_attr_codes[:n_items, f_idx] = np.asarray(mapped, dtype=np.int32)
    return item_attr_codes, next_code, field_maps


def _build_user_affinities_table(
    user_id_series: pl.Series,
    u_arr: np.ndarray,
    i_arr: np.ndarray,
    h_cnt: np.ndarray,
    item_attr_codes: np.ndarray,
    n_attrs: int,
    user_count180: np.ndarray,
) -> pl.DataFrame:
    """Compute compact (customer_id, attr_code: List(UInt16), share: List(Float32)) table."""
    empty_schema = {
        'customer_id': pl.String,
        'attr_code': pl.List(pl.UInt16),
        'share': pl.List(pl.Float32),
    }
    n_users = len(user_id_series)
    if u_arr.size == 0 or n_users == 0 or n_attrs == 0:
        return pl.DataFrame(schema=empty_schema)

    valid_hist = (u_arr >= 0) & (i_arr >= 0) & (h_cnt > 0.0)
    if not np.all(valid_hist):
        u_arr = u_arr[valid_hist]
        i_arr = i_arr[valid_hist]
        h_cnt = h_cnt[valid_hist]
    if u_arr.size == 0:
        return pl.DataFrame(schema=empty_schema)

    aff_csr = sparse.csr_matrix((n_users, n_attrs), dtype=np.float32)
    for f_idx in range(len(AFFINITY_ATTR_FIELDS)):
        ac = item_attr_codes[i_arr, f_idx]
        m = ac >= 0
        if np.any(m):
            part = sparse.csr_matrix(
                (h_cnt[m], (u_arr[m], ac[m])),
                shape=(n_users, n_attrs),
                dtype=np.float32,
            )
            aff_csr = aff_csr + part

    aff_csr.sum_duplicates()
    aff_csr.sort_indices()
    if aff_csr.nnz == 0:
        return pl.DataFrame(schema=empty_schema)

    # Normalize each row by user_count180
    indptr = aff_csr.indptr
    row_nnz = np.diff(indptr)
    nonempty_rows = np.flatnonzero((row_nnz > 0) & (user_count180 > 0.0))
    if nonempty_rows.size == 0:
        return pl.DataFrame(schema=empty_schema)

    inv_denom = np.zeros(n_users, dtype=np.float32)
    nz = user_count180 > 0.0
    inv_denom[nz] = np.float32(1.0) / user_count180[nz]
    row_rep = np.repeat(np.arange(n_users, dtype=np.int32), row_nnz)
    norm_data = (aff_csr.data * inv_denom[row_rep]).astype(np.float32, copy=False)

    # Extract only non-empty users (already sorted by customer_id)
    if nonempty_rows.size == n_users:
        sub_indptr = indptr
        sub_indices = aff_csr.indices
        sub_data = norm_data
        sub_cid_arrow = user_id_series.to_arrow()
    else:
        sub_cid_arrow = user_id_series.gather(nonempty_rows.astype(np.int64, copy=False)).to_arrow()
        sub_indptr = np.zeros(nonempty_rows.size + 1, dtype=np.int64)
        idx_chunks: list[np.ndarray] = []
        val_chunks: list[np.ndarray] = []
        for pos, r in enumerate(nonempty_rows):
            s = int(indptr[r])
            e = int(indptr[r + 1])
            idx_chunks.append(aff_csr.indices[s:e])
            val_chunks.append(norm_data[s:e])
            sub_indptr[pos + 1] = sub_indptr[pos] + (e - s)
        sub_indices = np.concatenate(idx_chunks)
        sub_data = np.concatenate(val_chunks)

    offsets = pa.array(sub_indptr, type=pa.int32())
    attr_arr = pa.ListArray.from_arrays(offsets, pa.array(sub_indices.astype(np.uint16, copy=False), type=pa.uint16()))
    share_arr = pa.ListArray.from_arrays(offsets, pa.array(sub_data.astype(np.float32, copy=False), type=pa.float32()))
    table = pa.table({
        'customer_id': sub_cid_arrow,
        'attr_code': attr_arr,
        'share': share_arr,
    })
    return pl.from_arrow(table)


def _infer_cutoff(
    catalog: pl.DataFrame,
    item_stats: pl.DataFrame,
    histories: pl.DataFrame,
) -> date:
    if not catalog.is_empty() and not item_stats.is_empty():
        joined = (
            catalog.select('article_id', 'first_seen')
            .join(item_stats.select('article_id', 'item_days_since_first_seen'), on='article_id', how='inner')
            .filter(pl.col('first_seen').is_not_null() & (pl.col('item_days_since_first_seen') >= 1.0))
        )
        if not joined.is_empty():
            row = joined.row(0, named=True)
            return row['first_seen'] + timedelta(days=int(round(float(row['item_days_since_first_seen']))))
    if not histories.is_empty():
        max_lp = histories['last_purchase'].max()
        if max_lp is not None:
            return max_lp + timedelta(days=1)
    raise ValueError('Cannot infer cutoff from empty summary tables; provide explicit cutoff')


class V2FeatureContext:
    """Compact < cutoff summary tables and vectorized Stage-2 feature extractor."""

    def __init__(
        self,
        *,
        cutoff: date,
        catalog: pl.DataFrame,
        item_stats: pl.DataFrame,
        user_stats: pl.DataFrame,
        user_affinities: pl.DataFrame | None,
        histories: pl.DataFrame,
        retain_dataframes: bool = True,
    ) -> None:
        _validate_cutoff(cutoff)
        self.cutoff: date = cutoff

        # Ensure deterministic sorting & 1:1 catalog/item_stats alignment
        if not catalog.is_empty():
            catalog = catalog.unique('article_id', keep='first').sort('article_id')
        if not item_stats.is_empty():
            item_stats = item_stats.unique('article_id', keep='first')
        item_stats = catalog.select('article_id').join(item_stats, on='article_id', how='left')
        if not user_stats.is_empty():
            user_stats = user_stats.unique('customer_id', keep='first').sort('customer_id')

        n_items = catalog.height
        article_ids = catalog['article_id'].to_list() if n_items else []
        article_to_idx: dict[str, int] = {aid: i for i, aid in enumerate(article_ids)}
        self._article_ids: tuple[str, ...] = tuple(article_ids)
        self._article_to_idx: dict[str, int] = article_to_idx

        # Item feature matrix (n_items + 1, 12)
        item_feat_matrix = np.empty((n_items + 1, len(_ITEM_STAT_FEATURE_COLS)), dtype=np.float32)
        item_feat_matrix[n_items] = _ITEM_DEFAULTS
        if n_items > 0:
            for col_i, col_name in enumerate(_ITEM_STAT_FEATURE_COLS):
                arr = item_stats[col_name].fill_null(float(_ITEM_DEFAULTS[col_i])).to_numpy().astype(np.float32, copy=False)
                arr = np.where(np.isfinite(arr), arr, _ITEM_DEFAULTS[col_i])
                item_feat_matrix[:n_items, col_i] = arr
        self._item_feat_matrix: np.ndarray = item_feat_matrix

        # Product code index per item
        item_pc_idx = np.full(n_items + 1, -1, dtype=np.int32)
        pc_to_idx: dict[str, int] = {}
        if n_items > 0 and 'product_code' in catalog.columns:
            raw_pcs = catalog['product_code'].cast(pl.String, strict=False).fill_null('').to_list()
            for idx, pc in enumerate(raw_pcs):
                if pc:
                    code = pc_to_idx.get(pc)
                    if code is None:
                        code = len(pc_to_idx)
                        pc_to_idx[pc] = code
                    item_pc_idx[idx] = code
        self._item_pc_idx: np.ndarray = item_pc_idx
        self._n_prodcodes: int = len(pc_to_idx)

        # Attribute vocabulary & item attribute codes
        item_attr_codes, n_attrs, field_maps = _build_attr_vocab(catalog)
        self._item_attr_codes: np.ndarray = item_attr_codes
        self._n_attrs: int = n_attrs
        self._attr_field_maps: dict[str, dict[str, int]] = field_maps

        # User feature matrix (n_users + 1, 15)
        n_users = user_stats.height
        user_ids = user_stats['customer_id'].to_list() if n_users else []
        user_to_idx: dict[str, int] = {cid: i for i, cid in enumerate(user_ids)}
        self._user_to_idx: dict[str, int] = user_to_idx
        self._n_users: int = n_users
        self._n_items: int = n_items

        user_feat_matrix = np.empty((n_users + 1, len(_USER_STAT_FEATURE_COLS)), dtype=np.float32)
        user_feat_matrix[n_users] = _USER_DEFAULTS
        user_stats_col_map = {
            'user_age': 'age',
            'user_age_missing': 'age_missing',
            'user_club_active': 'club_active',
            'user_news_freq_code': 'news_freq_code',
            'user_fn_active': 'fn_active',
            'user_active_flag': 'user_active_flag',
            'user_count7': 'user_count7',
            'user_count28': 'user_count28',
            'user_count90': 'user_count90',
            'user_count180': 'user_count180',
            'user_recency': 'user_recency',
            'user_avg_price': 'user_avg_price',
            'user_std_price': 'user_std_price',
            'user_online_ratio': 'user_online_ratio',
            'user_n_unique_prodcodes': 'user_n_unique_prodcodes',
        }
        if n_users > 0:
            for col_i, feat_name in enumerate(_USER_STAT_FEATURE_COLS):
                src_col = user_stats_col_map[feat_name]
                if src_col in user_stats.columns:
                    arr = user_stats[src_col].fill_null(float(_USER_DEFAULTS[col_i])).to_numpy().astype(np.float32, copy=False)
                    arr = np.where(np.isfinite(arr), arr, _USER_DEFAULTS[col_i])
                else:
                    arr = np.full(n_users, _USER_DEFAULTS[col_i], dtype=np.float32)
                user_feat_matrix[:n_users, col_i] = arr
        self._user_feat_matrix: np.ndarray = user_feat_matrix

        user_count180_arr = user_feat_matrix[:n_users, 9].copy() if n_users > 0 else np.empty(0, dtype=np.float32)

        # Map histories to integer indices once via Polars join (avoids 6.98M Python str allocations)
        shift = max(24, int(max(n_items, self._n_prodcodes, 1)).bit_length() + 1)
        self._shift: int = shift

        if histories.is_empty() or n_users == 0 or n_items == 0:
            u_hist_arr = np.empty(0, dtype=np.int32)
            i_hist_arr = np.empty(0, dtype=np.int32)
            rec_days = np.empty(0, dtype=np.float32)
            c7 = np.empty(0, dtype=np.float32)
            c28 = np.empty(0, dtype=np.float32)
            c90 = np.empty(0, dtype=np.float32)
            c180 = np.empty(0, dtype=np.float32)
            dec = np.empty(0, dtype=np.float32)
        else:
            u_map_df = pl.DataFrame({
                'customer_id': user_stats['customer_id'],
                '_u': pl.Series('_u', np.arange(n_users, dtype=np.int32), dtype=pl.Int32),
            })
            i_map_df = pl.DataFrame({
                'article_id': catalog['article_id'],
                '_i': pl.Series('_i', np.arange(n_items, dtype=np.int32), dtype=pl.Int32),
            })
            hist_mapped = (
                histories
                .lazy()
                .join(u_map_df.lazy(), on='customer_id', how='inner')
                .join(i_map_df.lazy(), on='article_id', how='inner')
                .select(
                    '_u',
                    '_i',
                    (pl.lit(cutoff) - pl.col('last_purchase')).dt.total_days().cast(pl.Float32).clip(lower_bound=0.0).alias('_rec'),
                    pl.col('count_7').cast(pl.Float32),
                    pl.col('count_28').cast(pl.Float32),
                    pl.col('count_90').cast(pl.Float32),
                    pl.col('count_180').cast(pl.Float32),
                    pl.col('decay_score').cast(pl.Float32),
                )
                .collect(engine='streaming')
            )
            u_hist_arr = hist_mapped['_u'].to_numpy().astype(np.int32, copy=False)
            i_hist_arr = hist_mapped['_i'].to_numpy().astype(np.int32, copy=False)
            rec_days = hist_mapped['_rec'].to_numpy().astype(np.float32, copy=False)
            c7 = hist_mapped['count_7'].to_numpy().astype(np.float32, copy=False)
            c28 = hist_mapped['count_28'].to_numpy().astype(np.float32, copy=False)
            c90 = hist_mapped['count_90'].to_numpy().astype(np.float32, copy=False)
            c180 = hist_mapped['count_180'].to_numpy().astype(np.float32, copy=False)
            dec = hist_mapped['decay_score'].to_numpy().astype(np.float32, copy=False)
            del u_map_df, i_map_df, hist_mapped

        # Build user_affinities if omitted (e.g. from_tables without precomputed user_affinities)
        if user_affinities is None:
            user_affinities = _build_user_affinities_table(
                user_stats['customer_id'],
                u_hist_arr,
                i_hist_arr,
                c180,
                item_attr_codes,
                n_attrs,
                user_count180_arr,
            )

        # Build CSR matrix (n_users, n_attrs) from user_affinities
        if user_affinities.is_empty() or n_users == 0 or n_attrs == 0:
            self._aff_csr: sparse.csr_matrix = sparse.csr_matrix((n_users, max(n_attrs, 1)), dtype=np.float32)
        else:
            aff_cids = user_affinities['customer_id'].to_list()
            aff_rows = np.fromiter((user_to_idx.get(c, -1) for c in aff_cids), dtype=np.int32, count=len(aff_cids))
            del aff_cids
            # Use PyArrow list column offsets & flattened values for fast O(nnz) construction
            pa_codes = user_affinities['attr_code'].rechunk().to_arrow()
            if isinstance(pa_codes, pa.ChunkedArray):
                pa_codes = pa_codes.combine_chunks()
            pa_shares = user_affinities['share'].rechunk().to_arrow()
            if isinstance(pa_shares, pa.ChunkedArray):
                pa_shares = pa_shares.combine_chunks()
            if pa_codes.null_count > 0 or pa_shares.null_count > 0:
                raise ValueError('user_affinities attr_code and share must not contain null list entries')
            c_offsets = pa_codes.offsets.to_numpy(zero_copy_only=False)
            s_offsets = pa_shares.offsets.to_numpy(zero_copy_only=False)
            c_lengths = np.diff(c_offsets)
            s_lengths = np.diff(s_offsets)
            if not np.array_equal(c_lengths, s_lengths):
                raise ValueError('user_affinities attr_code and share list lengths must match per row')
            c_start, c_end = int(c_offsets[0]), int(c_offsets[-1])
            s_start, s_end = int(s_offsets[0]), int(s_offsets[-1])
            c_values = pa_codes.values.slice(c_start, c_end - c_start)
            s_values = pa_shares.values.slice(s_start, s_end - s_start)
            if c_values.null_count > 0 or s_values.null_count > 0:
                raise ValueError('user_affinities attr_code and share list elements must not be null')
            if len(c_values) == 0:
                flat_codes = np.empty(0, dtype=np.int32)
                flat_shares = np.empty(0, dtype=np.float32)
            else:
                flat_codes = c_values.to_numpy(zero_copy_only=False).astype(np.int32, copy=False)
                flat_shares = s_values.to_numpy(zero_copy_only=False).astype(np.float32, copy=False)

            valid_mask = (aff_rows >= 0)
            row_idx = np.repeat(aff_rows, c_lengths)
            if np.all(valid_mask) and np.all(flat_codes >= 0) and np.all(flat_codes < n_attrs):
                self._aff_csr = sparse.csr_matrix(
                    (flat_shares, (row_idx, flat_codes)),
                    shape=(n_users, n_attrs),
                    dtype=np.float32,
                )
            else:
                keep = (row_idx >= 0) & (flat_codes >= 0) & (flat_codes < n_attrs)
                self._aff_csr = sparse.csr_matrix(
                    (flat_shares[keep], (row_idx[keep], flat_codes[keep])),
                    shape=(n_users, n_attrs),
                    dtype=np.float32,
                )
            self._aff_csr.sum_duplicates()
            self._aff_csr.sort_indices()
            del (
                aff_rows,
                pa_codes,
                pa_shares,
                c_offsets,
                s_offsets,
                c_lengths,
                s_lengths,
                c_values,
                s_values,
                flat_codes,
                flat_shares,
                row_idx,
            )

        # Build packed int64 searchsorted indices for (u, i) histories and (u, pc) 90d stats
        if u_hist_arr.size == 0:
            self._hist_keys = np.empty(0, dtype=np.int64)
            self._hist_c7 = np.empty(0, dtype=np.float32)
            self._hist_c28 = np.empty(0, dtype=np.float32)
            self._hist_c90 = np.empty(0, dtype=np.float32)
            self._hist_c180 = np.empty(0, dtype=np.float32)
            self._hist_recency = np.empty(0, dtype=np.float32)
            self._hist_decay = np.empty(0, dtype=np.float32)
            self._pc_keys = np.empty(0, dtype=np.int64)
            self._pc_count90 = np.empty(0, dtype=np.float32)
            self._pc_recency = np.empty(0, dtype=np.float32)
        else:
            u64 = u_hist_arr.astype(np.int64, copy=False)
            i64 = i_hist_arr.astype(np.int64, copy=False)
            keys = (u64 << shift) | i64
            order = np.argsort(keys, kind='mergesort')
            self._hist_keys = keys[order]
            self._hist_c7 = c7[order]
            self._hist_c28 = c28[order]
            self._hist_c90 = c90[order]
            self._hist_c180 = c180[order]
            self._hist_recency = rec_days[order]
            self._hist_decay = dec[order]

            # Derive 90d product_code stats directly from histories + item_pc_idx
            pc_arr = item_pc_idx[i_hist_arr]
            pc_mask = (c90 > 0.0) & (pc_arr >= 0)
            if np.any(pc_mask):
                pc_df = pl.DataFrame({
                    'key': pl.Series('key', (u64[pc_mask] << shift) | pc_arr[pc_mask].astype(np.int64), dtype=pl.Int64),
                    'c90': pl.Series('c90', c90[pc_mask], dtype=pl.Float32),
                    'rec': pl.Series('rec', rec_days[pc_mask], dtype=pl.Float32),
                })
                pc_agg = (
                    pc_df
                    .group_by('key')
                    .agg(
                        pl.col('c90').sum().cast(pl.Float32).alias('c90'),
                        pl.col('rec').min().cast(pl.Float32).alias('rec'),
                    )
                    .sort('key')
                )
                self._pc_keys = pc_agg['key'].to_numpy().astype(np.int64, copy=False)
                self._pc_count90 = pc_agg['c90'].to_numpy().astype(np.float32, copy=False)
                self._pc_recency = pc_agg['rec'].to_numpy().astype(np.float32, copy=False)
            else:
                self._pc_keys = np.empty(0, dtype=np.int64)
                self._pc_count90 = np.empty(0, dtype=np.float32)
                self._pc_recency = np.empty(0, dtype=np.float32)

        if retain_dataframes:
            self.catalog: pl.DataFrame = catalog
            self.item_stats: pl.DataFrame = item_stats
            self.user_stats: pl.DataFrame = user_stats
            self.user_affinities: pl.DataFrame = user_affinities
            self.histories: pl.DataFrame = histories
        else:
            self.catalog = catalog
            self.item_stats = item_stats
            self.user_stats = user_stats.head(0)
            self.user_affinities = user_affinities.head(0)
            self.histories = histories.head(0)

    @classmethod
    def from_tables(
        cls,
        *,
        catalog: pl.DataFrame,
        item_stats: pl.DataFrame,
        user_stats: pl.DataFrame,
        user_affinities: pl.DataFrame | None = None,
        histories: pl.DataFrame,
        cutoff: date | None = None,
        retain_dataframes: bool = True,
    ) -> 'V2FeatureContext':
        resolved_cutoff = cutoff if cutoff is not None else _infer_cutoff(catalog, item_stats, histories)
        _validate_cutoff(resolved_cutoff)
        return cls(
            cutoff=resolved_cutoff,
            catalog=catalog,
            item_stats=item_stats,
            user_stats=user_stats,
            user_affinities=user_affinities,
            histories=histories,
            retain_dataframes=retain_dataframes,
        )

    @classmethod
    def from_dataset(
        cls,
        dataset: DatasetManifest,
        cutoff: date,
        *,
        retain_dataframes: bool = True,
    ) -> 'V2FeatureContext':
        _validate_cutoff(cutoff)
        has_customers = any(p.path.startswith('customers/') for p in dataset.partitions)
        return cls.from_frames(
            scan(dataset, 'transactions'),
            scan(dataset, 'articles'),
            scan(dataset, 'customers') if has_customers else None,
            cutoff=cutoff,
            retain_dataframes=retain_dataframes,
        )

    @classmethod
    def from_frames(
        cls,
        transactions: pl.LazyFrame | pl.DataFrame,
        articles: pl.LazyFrame | pl.DataFrame,
        customers: pl.LazyFrame | pl.DataFrame | None = None,
        *,
        cutoff: date,
        retain_dataframes: bool = True,
    ) -> 'V2FeatureContext':
        _validate_cutoff(cutoff)
        tx_lf = transactions.lazy() if isinstance(transactions, pl.DataFrame) else transactions
        art_lf = articles.lazy() if isinstance(articles, pl.DataFrame) else articles
        cust_lf = customers.lazy() if isinstance(customers, pl.DataFrame) else customers

        past_tx = tx_lf.filter(pl.col('t_dat') < pl.lit(cutoff))
        tx_cols = set(past_tx.collect_schema().names())

        # Normalize articles (deduplicated by article_id)
        art_cols = set(art_lf.collect_schema().names())
        art_exprs: list[pl.Expr] = [pl.col('article_id').cast(pl.String, strict=False)]
        if 'product_code' in art_cols:
            art_exprs.append(
                pl.when(pl.col('product_code').cast(pl.String, strict=False) == '')
                .then(pl.lit(None, dtype=pl.String))
                .otherwise(pl.col('product_code').cast(pl.String, strict=False))
                .alias('product_code')
            )
        else:
            art_exprs.append(pl.lit(None, dtype=pl.String).alias('product_code'))
        for col_name in CATALOG_COLUMNS:
            if col_name in ('article_id', 'first_seen', 'product_code'):
                continue
            if col_name in art_cols:
                art_exprs.append(pl.col(col_name).cast(pl.String, strict=False).fill_null('').alias(col_name))
            else:
                art_exprs.append(pl.lit('', dtype=pl.String).alias(col_name))
        norm_articles = (
            art_lf
            .select(art_exprs)
            .filter(pl.col('article_id').is_not_null())
            .unique('article_id', keep='first')
        )

        # Pass 1: first_seen & last_seen per article_id
        item_dates = (
            past_tx
            .filter(pl.col('article_id').is_not_null())
            .group_by('article_id')
            .agg(
                pl.col('t_dat').min().alias('first_seen'),
                pl.col('t_dat').max().alias('last_seen'),
            )
            .collect(engine='streaming')
        )
        if item_dates.is_empty():
            raise ValueError('No eligible catalog before cutoff')

        str_fill_cols = [c for c in CATALOG_COLUMNS if c not in ('article_id', 'first_seen', 'product_code')]
        catalog = (
            item_dates
            .select('article_id', 'first_seen')
            .lazy()
            .join(norm_articles, on='article_id', how='left')
            .with_columns(*[pl.col(c).fill_null('').alias(c) for c in str_fill_cols])
            .sort('article_id')
            .select(CATALOG_COLUMNS)
            .collect(engine='streaming')
        )

        # Pass 2: item_price via exact (article_id, last_seen) join (avoids nested lists)
        if 'price' in tx_cols:
            last_prices = (
                past_tx
                .select(
                    pl.col('article_id').cast(pl.String, strict=False),
                    pl.col('t_dat'),
                    pl.col('price').cast(pl.Float64, strict=False).alias('price'),
                )
                .filter(pl.col('price').is_not_null() & pl.col('price').is_finite() & (pl.col('price') > 0.0))
                .join(
                    item_dates.select('article_id', pl.col('last_seen').alias('t_dat')).lazy(),
                    on=['article_id', 't_dat'],
                    how='inner',
                )
                .group_by('article_id')
                .agg(pl.col('price').mean().cast(pl.Float32).alias('item_price'))
                .collect(engine='streaming')
            )
        else:
            last_prices = pl.DataFrame(schema={'article_id': pl.String, 'item_price': pl.Float32})

        # Normalized customers base & valid age lookup
        if cust_lf is not None:
            cust_cols = set(cust_lf.collect_schema().names())
            if 'customer_id' in cust_cols:
                c_exprs: list[pl.Expr] = [pl.col('customer_id').cast(pl.String, strict=False)]
                if 'age' in cust_cols:
                    v_age = _valid_age_expr('age')
                    c_exprs.extend([
                        v_age.fill_null(-1.0).cast(pl.Float32).alias('age'),
                        v_age.is_null().cast(pl.Int8).alias('age_missing'),
                        age_bin_expr('age'),
                    ])
                else:
                    c_exprs.extend([
                        pl.lit(-1.0, dtype=pl.Float32).alias('age'),
                        pl.lit(1, dtype=pl.Int8).alias('age_missing'),
                        pl.lit('unknown', dtype=pl.String).alias('age_bin'),
                    ])
                if 'club_member_status' in cust_cols:
                    c_exprs.append(
                        (
                            pl.col('club_member_status')
                            .cast(pl.String, strict=False)
                            .str.strip_chars()
                            .str.to_uppercase()
                            == 'ACTIVE'
                        )
                        .fill_null(False)
                        .cast(pl.Int8)
                        .alias('club_active')
                    )
                else:
                    c_exprs.append(pl.lit(0, dtype=pl.Int8).alias('club_active'))
                if 'fashion_news_frequency' in cust_cols:
                    fn_str = (
                        pl.col('fashion_news_frequency')
                        .cast(pl.String, strict=False)
                        .str.strip_chars()
                        .str.to_lowercase()
                    )
                    c_exprs.append(
                        pl.when(fn_str == 'regularly')
                        .then(pl.lit(2, dtype=pl.Int8))
                        .when(fn_str == 'monthly')
                        .then(pl.lit(1, dtype=pl.Int8))
                        .otherwise(pl.lit(0, dtype=pl.Int8))
                        .alias('news_freq_code')
                    )
                else:
                    c_exprs.append(pl.lit(0, dtype=pl.Int8).alias('news_freq_code'))
                if 'FN' in cust_cols:
                    c_exprs.append(_parse_flag_expr('FN', 'fn_active'))
                else:
                    c_exprs.append(pl.lit(0, dtype=pl.Int8).alias('fn_active'))
                if 'Active' in cust_cols:
                    c_exprs.append(_parse_flag_expr('Active', 'user_active_flag'))
                else:
                    c_exprs.append(pl.lit(0, dtype=pl.Int8).alias('user_active_flag'))

                cust_base = (
                    cust_lf
                    .filter(pl.col('customer_id').is_not_null())
                    .select(c_exprs)
                    .unique('customer_id', keep='first')
                    .collect(engine='streaming')
                )
            else:
                cust_base = None
        else:
            cust_base = None

        cust_valid_age = (
            cust_base
            .filter(pl.col('age_missing') == 0)
            .select('customer_id', pl.col('age').alias('_valid_age'))
            if cust_base is not None and not cust_base.is_empty()
            else None
        )

        # 90d item stats
        start7 = cutoff - timedelta(days=7)
        start14 = cutoff - timedelta(days=14)
        start28 = cutoff - timedelta(days=28)
        start90 = cutoff - timedelta(days=90)
        start180 = cutoff - timedelta(days=180)

        tx90 = past_tx.filter(pl.col('t_dat') >= pl.lit(start90))
        item_agg_exprs: list[pl.Expr] = [
            (pl.col('t_dat') >= pl.lit(start7)).sum().cast(pl.UInt32).alias('item_count7'),
            (pl.col('t_dat') >= pl.lit(start14)).sum().cast(pl.UInt32).alias('item_count14'),
            (pl.col('t_dat') >= pl.lit(start28)).sum().cast(pl.UInt32).alias('item_count28'),
            pl.len().cast(pl.UInt32).alias('item_count90'),
        ]
        if 'sales_channel_id' in tx_cols:
            ch = pl.col('sales_channel_id').cast(pl.Int64, strict=False)
            item_agg_exprs.append(
                pl.when(ch.is_in([1, 2]))
                .then((ch == 2).cast(pl.Float64))
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .mean()
                .fill_null(0.5)
                .cast(pl.Float32)
                .alias('item_online_ratio')
            )
        else:
            item_agg_exprs.append(pl.lit(0.5, dtype=pl.Float32).alias('item_online_ratio'))

        tx90_for_age = tx90
        if cust_valid_age is not None and not cust_valid_age.is_empty():
            tx90_for_age = tx90.join(cust_valid_age.lazy(), on='customer_id', how='left')
            item_agg_exprs.extend([
                pl.col('_valid_age').cast(pl.Float64).mean().fill_null(-1.0).fill_nan(-1.0).cast(pl.Float32).alias('item_mean_age'),
                pl.col('_valid_age').cast(pl.Float64).std(ddof=0).fill_null(0.0).fill_nan(0.0).cast(pl.Float32).alias('item_std_age'),
            ])
        else:
            item_agg_exprs.extend([
                pl.lit(-1.0, dtype=pl.Float32).alias('item_mean_age'),
                pl.lit(0.0, dtype=pl.Float32).alias('item_std_age'),
            ])

        item_counts_90 = tx90_for_age.group_by('article_id').agg(item_agg_exprs).collect(engine='streaming')

        item_repurchase = (
            tx90
            .filter(pl.col('customer_id').is_not_null())
            .group_by('article_id', 'customer_id')
            .agg(pl.len().alias('_cnt'))
            .group_by('article_id')
            .agg((pl.col('_cnt') >= 2).mean().fill_null(0.0).cast(pl.Float32).alias('item_repurchase_rate'))
            .collect(engine='streaming')
        )

        item_stats = (
            catalog
            .select('article_id', 'first_seen')
            .join(last_prices, on='article_id', how='left')
            .join(item_counts_90, on='article_id', how='left')
            .join(item_repurchase, on='article_id', how='left')
            .with_columns(
                pl.col('item_price').fill_null(0.0).cast(pl.Float32),
                pl.col('item_count7').fill_null(0).cast(pl.UInt32),
                pl.col('item_count14').fill_null(0).cast(pl.UInt32),
                pl.col('item_count28').fill_null(0).cast(pl.UInt32),
                pl.col('item_count90').fill_null(0).cast(pl.UInt32),
                pl.col('item_online_ratio').fill_null(0.5).cast(pl.Float32),
                pl.col('item_mean_age').fill_null(-1.0).cast(pl.Float32),
                pl.col('item_std_age').fill_null(0.0).cast(pl.Float32),
                pl.col('item_repurchase_rate').fill_null(0.0).cast(pl.Float32),
                pl.when(pl.col('first_seen').is_not_null())
                .then((pl.lit(cutoff) - pl.col('first_seen')).dt.total_days().cast(pl.Float32).clip(lower_bound=1.0))
                .otherwise(pl.lit(0.0, dtype=pl.Float32))
                .alias('item_days_since_first_seen'),
            )
            .with_columns(
                (
                    pl.col('item_count7').cast(pl.Float64)
                    / (pl.col('item_count28').cast(pl.Float64) / 4.0 + 1.0)
                ).cast(pl.Float32).alias('item_trend_7_28'),
                (
                    pl.col('item_count14').cast(pl.Float64)
                    / (pl.col('item_count90').cast(pl.Float64) / (90.0 / 14.0) + 1.0)
                ).cast(pl.Float32).alias('item_trend_14_90'),
            )
            .select(
                'article_id',
                'item_price',
                'item_count7',
                'item_count14',
                'item_count28',
                'item_count90',
                'item_trend_7_28',
                'item_trend_14_90',
                'item_online_ratio',
                'item_mean_age',
                'item_std_age',
                'item_repurchase_rate',
                'item_days_since_first_seen',
            )
        )

        # 180d histories & user behavioral aggregates
        tx180 = (
            past_tx
            .filter((pl.col('t_dat') >= pl.lit(start180)) & pl.col('customer_id').is_not_null())
            .join(catalog.select('article_id', 'product_code', 'index_group_name').lazy(), on='article_id', how='inner')
        )

        days_expr = (pl.lit(cutoff) - pl.col('t_dat')).dt.total_days().cast(pl.Float64)
        decay_expr = (
            0.6 * (-days_expr / 14.0).exp()
            + 0.4 * (-days_expr / 60.0).exp()
        )

        histories = (
            tx180
            .with_columns(decay_expr.alias('_decay'))
            .group_by('customer_id', 'article_id')
            .agg(
                pl.col('t_dat').max().alias('last_purchase'),
                (pl.col('t_dat') >= pl.lit(start7)).sum().clip(upper_bound=65535).cast(pl.UInt16).alias('count_7'),
                (pl.col('t_dat') >= pl.lit(start28)).sum().clip(upper_bound=65535).cast(pl.UInt16).alias('count_28'),
                (pl.col('t_dat') >= pl.lit(start90)).sum().clip(upper_bound=65535).cast(pl.UInt16).alias('count_90'),
                pl.len().cast(pl.UInt32).alias('count_180'),
                pl.col('_decay').sum().cast(pl.Float32).alias('decay_score'),
            )
            .sort(['customer_id', 'last_purchase', 'count_180', 'article_id'], descending=[False, True, True, False])
            .collect(engine='streaming')
        )

        user_agg_exprs: list[pl.Expr] = [
            (pl.col('t_dat') >= pl.lit(start7)).sum().cast(pl.UInt32).alias('user_count7'),
            (pl.col('t_dat') >= pl.lit(start28)).sum().cast(pl.UInt32).alias('user_count28'),
            (pl.col('t_dat') >= pl.lit(start90)).sum().cast(pl.UInt32).alias('user_count90'),
            pl.len().cast(pl.UInt32).alias('user_count180'),
            (pl.lit(cutoff) - pl.col('t_dat').max()).dt.total_days().cast(pl.Float32).alias('user_recency'),
            pl.col('product_code')
            .filter(pl.col('product_code').is_not_null() & (pl.col('product_code') != ''))
            .n_unique()
            .cast(pl.UInt32)
            .alias('user_n_unique_prodcodes'),
        ]
        if 'price' in tx_cols:
            valid_p = (
                pl.when(
                    pl.col('price').cast(pl.Float64, strict=False).is_not_null()
                    & pl.col('price').cast(pl.Float64, strict=False).is_finite()
                    & (pl.col('price').cast(pl.Float64, strict=False) > 0.0)
                )
                .then(pl.col('price').cast(pl.Float64, strict=False))
                .otherwise(pl.lit(None, dtype=pl.Float64))
            )
            user_agg_exprs.extend([
                valid_p.mean().fill_null(0.0).fill_nan(0.0).cast(pl.Float32).alias('user_avg_price'),
                valid_p.std(ddof=0).fill_null(0.0).fill_nan(0.0).cast(pl.Float32).alias('user_std_price'),
            ])
        else:
            user_agg_exprs.extend([
                pl.lit(0.0, dtype=pl.Float32).alias('user_avg_price'),
                pl.lit(0.0, dtype=pl.Float32).alias('user_std_price'),
            ])
        if 'sales_channel_id' in tx_cols:
            ch = pl.col('sales_channel_id').cast(pl.Int64, strict=False)
            user_agg_exprs.append(
                pl.when(ch.is_in([1, 2]))
                .then((ch == 2).cast(pl.Float64))
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .mean()
                .fill_null(0.5)
                .cast(pl.Float32)
                .alias('user_online_ratio')
            )
        else:
            user_agg_exprs.append(pl.lit(0.5, dtype=pl.Float32).alias('user_online_ratio'))

        user_beh = tx180.group_by('customer_id').agg(user_agg_exprs).collect(engine='streaming')

        if not histories.is_empty():
            pref_ig_df = (
                histories
                .select('customer_id', 'article_id', 'decay_score')
                .join(catalog.select('article_id', 'index_group_name'), on='article_id', how='left')
                .filter(pl.col('index_group_name') != '')
                .group_by('customer_id', 'index_group_name')
                .agg(pl.col('decay_score').cast(pl.Float64).sum().round(6).alias('_ig_score'))
                .sort(['customer_id', '_ig_score', 'index_group_name'], descending=[False, True, False])
                .group_by('customer_id', maintain_order=True)
                .first()
                .select('customer_id', pl.col('index_group_name').alias('pref_index_group'))
            )
        else:
            pref_ig_df = pl.DataFrame(schema={'customer_id': pl.String, 'pref_index_group': pl.String})

        if cust_base is not None:
            # Union customers table with any active 180d customers missing from customers table
            if not user_beh.is_empty():
                extra_users = user_beh.select('customer_id').join(cust_base.select('customer_id'), on='customer_id', how='anti')
                if not extra_users.is_empty():
                    extra_padded = extra_users.with_columns(
                        pl.lit(-1.0, dtype=pl.Float32).alias('age'),
                        pl.lit(1, dtype=pl.Int8).alias('age_missing'),
                        pl.lit('unknown', dtype=pl.String).alias('age_bin'),
                        pl.lit(0, dtype=pl.Int8).alias('club_active'),
                        pl.lit(0, dtype=pl.Int8).alias('news_freq_code'),
                        pl.lit(0, dtype=pl.Int8).alias('fn_active'),
                        pl.lit(0, dtype=pl.Int8).alias('user_active_flag'),
                    )
                    cust_all = pl.concat([cust_base, extra_padded], rechunk=False)
                else:
                    cust_all = cust_base
            else:
                cust_all = cust_base
        else:
            cust_all = user_beh.select('customer_id').with_columns(
                pl.lit(-1.0, dtype=pl.Float32).alias('age'),
                pl.lit(1, dtype=pl.Int8).alias('age_missing'),
                pl.lit('unknown', dtype=pl.String).alias('age_bin'),
                pl.lit(0, dtype=pl.Int8).alias('club_active'),
                pl.lit(0, dtype=pl.Int8).alias('news_freq_code'),
                pl.lit(0, dtype=pl.Int8).alias('fn_active'),
                pl.lit(0, dtype=pl.Int8).alias('user_active_flag'),
            )

        user_stats = (
            cust_all
            .join(pref_ig_df, on='customer_id', how='left')
            .join(user_beh, on='customer_id', how='left')
            .with_columns(
                pl.col('pref_index_group').fill_null('').cast(pl.String),
                pl.col('user_count7').fill_null(0).cast(pl.UInt32),
                pl.col('user_count28').fill_null(0).cast(pl.UInt32),
                pl.col('user_count90').fill_null(0).cast(pl.UInt32),
                pl.col('user_count180').fill_null(0).cast(pl.UInt32),
                pl.col('user_recency').fill_null(181.0).cast(pl.Float32),
                pl.col('user_avg_price').fill_null(0.0).cast(pl.Float32),
                pl.col('user_std_price').fill_null(0.0).cast(pl.Float32),
                pl.col('user_online_ratio').fill_null(0.5).cast(pl.Float32),
                pl.col('user_n_unique_prodcodes').fill_null(0).cast(pl.UInt32),
            )
            .sort('customer_id')
            .select(
                'customer_id',
                'age',
                'age_missing',
                'age_bin',
                'club_active',
                'news_freq_code',
                'fn_active',
                'user_active_flag',
                'pref_index_group',
                'user_count7',
                'user_count28',
                'user_count90',
                'user_count180',
                'user_recency',
                'user_avg_price',
                'user_std_price',
                'user_online_ratio',
                'user_n_unique_prodcodes',
            )
        )

        return cls(
            cutoff=cutoff,
            catalog=catalog,
            item_stats=item_stats,
            user_stats=user_stats,
            user_affinities=None,
            histories=histories,
            retain_dataframes=retain_dataframes,
        )

    def _compute_batch_matrix(
        self,
        u_idx: np.ndarray,
        i_idx: np.ndarray,
        cand_scores: np.ndarray,
        cand_ranks: np.ndarray,
    ) -> np.ndarray:
        n_rows = u_idx.shape[0]
        n_users = self._n_users
        n_items = self._n_items
        shift = self._shift

        u_feats = self._user_feat_matrix[u_idx]  # (N, 15) float32
        i_feats = self._item_feat_matrix[i_idx]  # (N, 12) float32

        # Pair stats via packed int64 searchsorted
        pair_feats = np.empty((n_rows, 8), dtype=np.float32)
        pair_feats[:, 0:4] = 0.0
        pair_feats[:, 4] = 181.0
        pair_feats[:, 5] = 0.0
        pair_feats[:, 6] = 0.0
        pair_feats[:, 7] = 91.0

        valid_pair = (u_idx < n_users) & (i_idx < n_items)
        if self._hist_keys.size > 0 and np.any(valid_pair):
            vp_pos = np.flatnonzero(valid_pair)
            q_keys = (u_idx[vp_pos] << shift) | i_idx[vp_pos]
            pos = np.searchsorted(self._hist_keys, q_keys)
            in_bounds = pos < self._hist_keys.size
            matched = np.zeros(vp_pos.size, dtype=bool)
            if np.any(in_bounds):
                ib_idx = np.flatnonzero(in_bounds)
                matched[ib_idx] = self._hist_keys[pos[ib_idx]] == q_keys[ib_idx]
            if np.any(matched):
                m_rows = vp_pos[matched]
                h_pos = pos[matched]
                pair_feats[m_rows, 0] = self._hist_c7[h_pos]
                pair_feats[m_rows, 1] = self._hist_c28[h_pos]
                pair_feats[m_rows, 2] = self._hist_c90[h_pos]
                pair_feats[m_rows, 3] = self._hist_c180[h_pos]
                pair_feats[m_rows, 4] = self._hist_recency[h_pos]
                pair_feats[m_rows, 5] = self._hist_decay[h_pos]

        # Product code 90d stats
        pc_idx = self._item_pc_idx[i_idx]
        valid_pc = (u_idx < n_users) & (pc_idx >= 0)
        if self._pc_keys.size > 0 and np.any(valid_pc):
            vpc_pos = np.flatnonzero(valid_pc)
            q_pc = (u_idx[vpc_pos] << shift) | pc_idx[vpc_pos].astype(np.int64)
            pos = np.searchsorted(self._pc_keys, q_pc)
            in_bounds = pos < self._pc_keys.size
            matched = np.zeros(vpc_pos.size, dtype=bool)
            if np.any(in_bounds):
                ib_idx = np.flatnonzero(in_bounds)
                matched[ib_idx] = self._pc_keys[pos[ib_idx]] == q_pc[ib_idx]
            if np.any(matched):
                m_rows = vpc_pos[matched]
                p_pos = pos[matched]
                pair_feats[m_rows, 6] = self._pc_count90[p_pos]
                pair_feats[m_rows, 7] = self._pc_recency[p_pos]

        # Cross & affinity features (9)
        aff_feats = np.zeros((n_rows, 9), dtype=np.float32)
        user_age = u_feats[:, 0]
        user_age_missing = u_feats[:, 1]
        user_avg_price = u_feats[:, 11]
        user_online_ratio = u_feats[:, 13]

        item_price = i_feats[:, 0]
        item_online_ratio = i_feats[:, 7]
        item_mean_age = i_feats[:, 8]

        age_ok = (user_age_missing == 0.0) & (item_mean_age >= 0.0)
        aff_feats[:, 0] = np.where(age_ok, np.abs(user_age - item_mean_age), np.float32(-1.0))

        price_ok = (user_avg_price > 0.0) & (item_price > 0.0)
        aff_feats[:, 1] = np.where(
            price_ok,
            item_price / np.maximum(user_avg_price, np.float32(1e-4)),
            np.float32(1.0),
        )
        aff_feats[:, 2] = np.float32(1.0) - np.abs(user_online_ratio - item_online_ratio)

        if self._aff_csr.nnz > 0:
            item_ac = self._item_attr_codes[i_idx]  # (N, 6)
            valid_u = u_idx < n_users
            for f_idx in range(len(AFFINITY_ATTR_FIELDS)):
                ac_col = item_ac[:, f_idx]
                m = valid_u & (ac_col >= 0)
                if np.any(m):
                    m_pos = np.flatnonzero(m)
                    vals = np.asarray(self._aff_csr[u_idx[m_pos], ac_col[m_pos]]).ravel().astype(np.float32, copy=False)
                    aff_feats[m_pos, 3 + f_idx] = vals

        # Stage-1 extra (3)
        n_hits = (cand_ranks < MISSING_RANK_SENTINEL).sum(axis=1).astype(np.float32)
        min_rank = cand_ranks.min(axis=1).astype(np.float32)
        latent_x_cooc = (cand_scores[:, 3] * cand_scores[:, 2]).astype(np.float32, copy=False)
        stage1_extra = np.column_stack((n_hits, min_rank, latent_x_cooc)).astype(np.float32, copy=False)

        out = np.hstack((
            u_feats,
            i_feats,
            pair_feats,
            aff_feats,
            cand_scores.astype(np.float32, copy=False),
            cand_ranks.astype(np.float32, copy=False),
            stage1_extra,
        )).astype(np.float32, copy=False)
        return out

    def transform(self, candidates: pl.DataFrame) -> pl.DataFrame:
        if not isinstance(candidates, pl.DataFrame):
            raise ValueError('candidates must be a polars.DataFrame')
        required = ('customer_id', 'article_id', *SCORE_COLUMNS, *RANK_COLUMNS)
        missing = [c for c in required if c not in candidates.columns]
        if missing:
            raise ValueError(f'candidates missing required columns: {missing}')

        if candidates.is_empty():
            schema = {
                'customer_id': pl.String,
                'article_id': pl.String,
                **{name: pl.Float32 for name in V2_FEATURE_NAMES},
            }
            return pl.DataFrame(schema=schema)

        n_rows = candidates.height
        cids = candidates['customer_id'].cast(pl.String, strict=False).to_list()
        aids = candidates['article_id'].cast(pl.String, strict=False).to_list()
        u_map = self._user_to_idx
        a_map = self._article_to_idx
        nu = self._n_users
        ni = self._n_items
        u_idx = np.fromiter((u_map.get(c, nu) for c in cids), dtype=np.int64, count=n_rows)
        i_idx = np.fromiter((a_map.get(a, ni) for a in aids), dtype=np.int64, count=n_rows)

        cand_scores = np.column_stack([
            candidates[c].cast(pl.Float32, strict=False).to_numpy()
            for c in SCORE_COLUMNS
        ]).astype(np.float32, copy=False)
        cand_ranks = np.column_stack([
            candidates[c].cast(pl.Float32, strict=False).to_numpy()
            for c in RANK_COLUMNS
        ]).astype(np.float32, copy=False)

        mat = self._compute_batch_matrix(u_idx, i_idx, cand_scores, cand_ranks)
        if not np.isfinite(mat).all():
            raise ValueError('Nonfinite Stage-2 ranker features')

        data: dict[str, pl.Series] = {
            'customer_id': candidates['customer_id'].cast(pl.String, strict=False),
            'article_id': candidates['article_id'].cast(pl.String, strict=False),
        }
        for idx, name in enumerate(V2_FEATURE_NAMES):
            data[name] = pl.Series(name, mat[:, idx], dtype=pl.Float32)
        return pl.DataFrame(data)

    def transform_online(
        self,
        candidates: pl.DataFrame,
        *,
        customer_id: str,
        live_events: Sequence[tuple[str, str]] | Sequence[Mapping[str, object]] | None = None,
        preferences: Mapping[str, object] | None = None,
    ) -> tuple[np.ndarray, bool]:
        if not isinstance(candidates, pl.DataFrame):
            raise ValueError('candidates must be a polars.DataFrame')
        required = ('article_id', *SCORE_COLUMNS, *RANK_COLUMNS)
        missing = [c for c in required if c not in candidates.columns]
        if missing:
            raise ValueError(f'candidates missing required columns: {missing}')

        # Validate live_events upfront regardless of candidate count
        parsed_events: list[tuple[int, str, float]] = []
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
                i_idx = self._article_to_idx.get(art_id)
                if i_idx is not None and math.isfinite(w_f) and w_f > 0.0:
                    parsed_events.append((i_idx, kind, float(w_f)))

        u_i = self._user_to_idx.get(str(customer_id), self._n_users)
        base_user = self._user_feat_matrix[u_i].copy()
        base_count180 = float(base_user[9])
        has_valid_live = len(parsed_events) > 0
        is_cold_start = (base_count180 <= 0.0) and (not has_valid_live)

        n_rows = candidates.height
        if n_rows == 0:
            return np.empty((0, len(V2_FEATURE_NAMES)), dtype=np.float32), is_cold_start

        a_map = self._article_to_idx
        ni = self._n_items
        aids = candidates['article_id'].cast(pl.String, strict=False).to_list()
        i_idx_arr = np.fromiter((a_map.get(a, ni) for a in aids), dtype=np.int64, count=n_rows)

        cand_scores = np.column_stack([
            candidates[c].cast(pl.Float32, strict=False).to_numpy()
            for c in SCORE_COLUMNS
        ]).astype(np.float32, copy=False)
        cand_ranks = np.column_stack([
            candidates[c].cast(pl.Float32, strict=False).to_numpy()
            for c in RANK_COLUMNS
        ]).astype(np.float32, copy=False)

        # Fast exact parity path when neither live_events nor preferences are active
        if live_events is None and preferences is None:
            u_idx_arr = np.full(n_rows, u_i, dtype=np.int64)
            mat = self._compute_batch_matrix(u_idx_arr, i_idx_arr, cand_scores, cand_ranks)
            if not np.isfinite(mat).all():
                raise ValueError('Nonfinite online ranker features')
            return mat, is_cold_start

        # Preferences fallback for missing age
        if preferences is not None:
            if base_user[1] == 1.0:
                p_age = _parse_valid_age(preferences.get('age'))
                if p_age is not None:
                    base_user[0] = np.float32(p_age)
                    base_user[1] = np.float32(0.0)
                else:
                    ab = preferences.get('age_bin')
                    if ab is not None and str(ab) in _AGE_BIN_MIDPOINTS:
                        base_user[0] = np.float32(_AGE_BIN_MIDPOINTS[str(ab)])
                        base_user[1] = np.float32(0.0)

        # Build single-user dense affinity vector
        dense_aff = np.zeros(max(self._n_attrs, 1), dtype=np.float32)
        if u_i < self._n_users and self._aff_csr.nnz > 0:
            s = int(self._aff_csr.indptr[u_i])
            e = int(self._aff_csr.indptr[u_i + 1])
            if e > s:
                dense_aff[self._aff_csr.indices[s:e]] = self._aff_csr.data[s:e]

        # Base pair & product_code lookups for this single user
        pair_feats = np.empty((n_rows, 8), dtype=np.float32)
        pair_feats[:, 0:4] = 0.0
        pair_feats[:, 4] = 181.0
        pair_feats[:, 5] = 0.0
        pair_feats[:, 6] = 0.0
        pair_feats[:, 7] = 91.0

        shift = self._shift
        if u_i < self._n_users and self._hist_keys.size > 0:
            valid_i = i_idx_arr < ni
            if np.any(valid_i):
                vi_pos = np.flatnonzero(valid_i)
                q_keys = (np.int64(u_i) << shift) | i_idx_arr[vi_pos]
                pos = np.searchsorted(self._hist_keys, q_keys)
                in_b = pos < self._hist_keys.size
                matched = np.zeros(vi_pos.size, dtype=bool)
                if np.any(in_b):
                    ib = np.flatnonzero(in_b)
                    matched[ib] = self._hist_keys[pos[ib]] == q_keys[ib]
                if np.any(matched):
                    mr = vi_pos[matched]
                    hp = pos[matched]
                    pair_feats[mr, 0] = self._hist_c7[hp]
                    pair_feats[mr, 1] = self._hist_c28[hp]
                    pair_feats[mr, 2] = self._hist_c90[hp]
                    pair_feats[mr, 3] = self._hist_c180[hp]
                    pair_feats[mr, 4] = self._hist_recency[hp]
                    pair_feats[mr, 5] = self._hist_decay[hp]

        pc_idx_arr = self._item_pc_idx[i_idx_arr]
        if u_i < self._n_users and self._pc_keys.size > 0:
            valid_pc = pc_idx_arr >= 0
            if np.any(valid_pc):
                vp_pos = np.flatnonzero(valid_pc)
                q_pc = (np.int64(u_i) << shift) | pc_idx_arr[vp_pos].astype(np.int64)
                pos = np.searchsorted(self._pc_keys, q_pc)
                in_b = pos < self._pc_keys.size
                matched = np.zeros(vp_pos.size, dtype=bool)
                if np.any(in_b):
                    ib = np.flatnonzero(in_b)
                    matched[ib] = self._pc_keys[pos[ib]] == q_pc[ib]
                if np.any(matched):
                    mr = vp_pos[matched]
                    pp = pos[matched]
                    pair_feats[mr, 6] = self._pc_count90[pp]
                    pair_feats[mr, 7] = self._pc_recency[pp]

        # Apply live events in-session
        if has_valid_live:
            numer = dense_aff * np.float32(base_count180)
            total_live_w = 0.0
            live_item_weights: dict[int, float] = {}
            live_purchase_counts: dict[int, float] = {}
            live_pc_weights: dict[int, float] = {}
            live_price_w = 0.0
            live_price_sum = 0.0
            n_live_purchases = 0.0

            for ev_item, kind, w_f in parsed_events:
                total_live_w += w_f
                live_item_weights[ev_item] = live_item_weights.get(ev_item, 0.0) + w_f
                for f_idx in range(len(AFFINITY_ATTR_FIELDS)):
                    ac = int(self._item_attr_codes[ev_item, f_idx])
                    if ac >= 0:
                        numer[ac] += np.float32(w_f)
                pc_c = int(self._item_pc_idx[ev_item])
                if pc_c >= 0:
                    live_pc_weights[pc_c] = live_pc_weights.get(pc_c, 0.0) + w_f
                p_val = float(self._item_feat_matrix[ev_item, 0])
                if p_val > 0.0:
                    live_price_w += w_f
                    live_price_sum += w_f * p_val
                if kind == 'purchase':
                    n_live_purchases += 1.0
                    live_purchase_counts[ev_item] = live_purchase_counts.get(ev_item, 0.0) + 1.0

            denom = np.float32(base_count180 + total_live_w)
            if denom > 0.0:
                dense_aff = (numer / denom).astype(np.float32, copy=False)

            base_user[10] = np.float32(0.0)  # user_recency = 0.0 (live session)
            if n_live_purchases > 0.0:
                base_user[6] += np.float32(n_live_purchases)
                base_user[7] += np.float32(n_live_purchases)
                base_user[8] += np.float32(n_live_purchases)
                base_user[9] += np.float32(n_live_purchases)
            if live_price_w > 0.0:
                if base_count180 > 0.0 and float(base_user[11]) > 0.0:
                    base_user[11] = np.float32(
                        (float(base_user[11]) * base_count180 + live_price_sum)
                        / (base_count180 + live_price_w)
                    )
                else:
                    base_user[11] = np.float32(live_price_sum / live_price_w)

            # Update pair and product_code features for candidate rows matching live events
            for r_i in range(n_rows):
                item_i = int(i_idx_arr[r_i])
                lw = live_item_weights.get(item_i)
                if lw is not None:
                    pair_feats[r_i, 4] = np.float32(0.0)
                    pair_feats[r_i, 5] += np.float32(lw)
                    lp = live_purchase_counts.get(item_i)
                    if lp is not None:
                        pair_feats[r_i, 0] += np.float32(lp)
                        pair_feats[r_i, 1] += np.float32(lp)
                        pair_feats[r_i, 2] += np.float32(lp)
                        pair_feats[r_i, 3] += np.float32(lp)
                pc_c = int(pc_idx_arr[r_i])
                if pc_c >= 0:
                    lpc = live_pc_weights.get(pc_c)
                    if lpc is not None:
                        pair_feats[r_i, 6] += np.float32(lpc)
                        pair_feats[r_i, 7] = np.float32(0.0)

        if preferences is not None and not has_valid_live and base_count180 <= 0.0:
            pref_ig = preferences.get('index_group_name')
            if pref_ig:
                ig_code = self._attr_field_maps.get('index_group_name', {}).get(str(pref_ig))
                if ig_code is not None:
                    dense_aff[ig_code] = np.float32(1.0)

        u_feats = np.broadcast_to(base_user[None, :], (n_rows, len(_USER_STAT_FEATURE_COLS))).astype(np.float32, copy=True)
        i_feats = self._item_feat_matrix[i_idx_arr]

        aff_feats = np.zeros((n_rows, 9), dtype=np.float32)
        age_ok = (u_feats[:, 1] == 0.0) & (i_feats[:, 8] >= 0.0)
        aff_feats[:, 0] = np.where(age_ok, np.abs(u_feats[:, 0] - i_feats[:, 8]), np.float32(-1.0))
        price_ok = (u_feats[:, 11] > 0.0) & (i_feats[:, 0] > 0.0)
        aff_feats[:, 1] = np.where(
            price_ok,
            i_feats[:, 0] / np.maximum(u_feats[:, 11], np.float32(1e-4)),
            np.float32(1.0),
        )
        aff_feats[:, 2] = np.float32(1.0) - np.abs(u_feats[:, 13] - i_feats[:, 7])

        item_ac = self._item_attr_codes[i_idx_arr]
        for f_idx in range(len(AFFINITY_ATTR_FIELDS)):
            ac_col = item_ac[:, f_idx]
            m = ac_col >= 0
            if np.any(m):
                aff_feats[m, 3 + f_idx] = dense_aff[ac_col[m]]

        n_hits = (cand_ranks < MISSING_RANK_SENTINEL).sum(axis=1).astype(np.float32)
        min_rank = cand_ranks.min(axis=1).astype(np.float32)
        latent_x_cooc = (cand_scores[:, 3] * cand_scores[:, 2]).astype(np.float32, copy=False)
        stage1_extra = np.column_stack((n_hits, min_rank, latent_x_cooc)).astype(np.float32, copy=False)

        out = np.hstack((
            u_feats,
            i_feats,
            pair_feats,
            aff_feats,
            cand_scores,
            cand_ranks,
            stage1_extra,
        )).astype(np.float32, copy=False)
        if not np.isfinite(out).all():
            raise ValueError('Nonfinite online ranker features')
        return out, is_cold_start


__all__ = [
    'AFFINITY_ATTR_FIELDS',
    'AGE_BINS',
    'CATALOG_COLUMNS',
    'V2_FEATURE_NAMES',
    'V2_FEATURE_VERSION',
    'V2FeatureContext',
]
