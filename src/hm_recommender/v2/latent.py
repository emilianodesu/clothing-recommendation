"""Pure NumPy/SciPy-sparse Stage-1 SVD latent & TF-IDF content index (zero sklearn/torch)."""
from __future__ import annotations

from array import array
from collections import Counter
from datetime import date, timedelta
import json
import math
from pathlib import Path
import re
from typing import Sequence

import numpy as np
import polars as pl
from scipy import sparse
from scipy.sparse.linalg import svds

CONTENT_FIELDS: tuple[str, ...] = (
    'product_type_name',
    'product_group_name',
    'colour_group_name',
    'graphical_appearance_name',
    'index_group_name',
    'garment_group_name',
    'detail_desc',
)
CATEGORICAL_CONTENT_FIELDS: tuple[str, ...] = CONTENT_FIELDS[:-1]
DEFAULT_LATENT_DIM: int = 64
DEFAULT_MAX_VOCAB: int = 16384
TOKEN_RE: re.Pattern[str] = re.compile(r'[a-z0-9]{2,}')
STOPWORDS: frozenset[str] = frozenset({
    'and', 'the', 'with', 'for', 'in', 'of', 'to', 'on', 'at',
    'is', 'are', 'an', 'as', 'by', 'or', 'from', 'that', 'this',
})

_CATEGORICAL_WEIGHT: float = 1.5
_DENSE_SVD_MAX_ELEMENTS: int = 262_144
_FALLBACK_DENSE_MAX_ELEMENTS: int = 4_194_304
_DEFAULT_BATCH_SIZE: int = 512


def _validate_cutoff(cutoff: date) -> None:
    if type(cutoff) is not date:
        raise ValueError('Explicit date cutoff required')


def _top_k_1d(scores: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
    """Select top_k positive-score indices ordered by (-score, +index) using O(N) 1D partition."""
    if top_k <= 0 or scores.size == 0:
        return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

    pos = np.flatnonzero(scores > 0.0)
    n_pos = pos.size
    if n_pos == 0:
        return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

    if n_pos <= top_k:
        cand_idx = pos.astype(np.int32, copy=False)
    else:
        pos_scores = scores[pos]
        threshold = -np.partition(-pos_scores, top_k - 1)[top_k - 1]
        strict = pos[pos_scores > threshold]
        need = top_k - strict.size
        if need > 0:
            # pos is sorted ascending, so slicing [:need] preserves ascending index tie-breaking.
            tied = pos[pos_scores == threshold][:need]
            cand_idx = np.concatenate((strict, tied)).astype(np.int32, copy=False)
        else:
            cand_idx = strict.astype(np.int32, copy=False)

    cand_scores = scores[cand_idx].astype(np.float32, copy=False)
    order = np.lexsort((cand_idx, -cand_scores))
    return cand_idx[order].astype(np.int32, copy=False), cand_scores[order].astype(np.float32, copy=False)


def _extract_article_tokens(
    cat_columns: list[list[str | None]],
    desc_col: list[str | None],
    row_idx: int,
) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    for field_name, col_vals in zip(CATEGORICAL_CONTENT_FIELDS, cat_columns):
        raw = col_vals[row_idx]
        if raw is not None:
            val = raw.strip().lower()
            if val:
                tokens.append((f'{field_name}={val}', _CATEGORICAL_WEIGHT))
    desc = desc_col[row_idx]
    if desc:
        words = [tok for tok in TOKEN_RE.findall(desc.lower()) if tok not in STOPWORDS]
        if words:
            counts = Counter(words)
            for tok, cnt in counts.items():
                tokens.append((f'desc={tok}', 1.0 + math.log(cnt)))
    return tokens


def _build_content_tfidf(
    catalog: pl.DataFrame,
    *,
    max_vocab: int,
) -> sparse.csr_matrix:
    n_articles = catalog.height
    if n_articles == 0 or max_vocab <= 0:
        return sparse.csr_matrix((n_articles, 0), dtype=np.float32)

    cols_available = set(catalog.columns)
    cat_columns: list[list[str | None]] = []
    for field_name in CATEGORICAL_CONTENT_FIELDS:
        if field_name in cols_available:
            cat_columns.append(catalog[field_name].cast(pl.String, strict=False).to_list())
        else:
            cat_columns.append([None] * n_articles)

    if 'detail_desc' in cols_available:
        desc_col: list[str | None] = catalog['detail_desc'].cast(pl.String, strict=False).to_list()
    else:
        desc_col = [None] * n_articles

    # Pass 1: document frequency across catalog articles.
    df_counts: dict[str, int] = {}
    for row_idx in range(n_articles):
        seen_in_doc: set[str] = set()
        for field_name, col_vals in zip(CATEGORICAL_CONTENT_FIELDS, cat_columns):
            raw = col_vals[row_idx]
            if raw is not None:
                val = raw.strip().lower()
                if val:
                    seen_in_doc.add(f'{field_name}={val}')
        desc = desc_col[row_idx]
        if desc:
            for tok in TOKEN_RE.findall(desc.lower()):
                if tok not in STOPWORDS:
                    seen_in_doc.add(f'desc={tok}')
        for tok in seen_in_doc:
            df_counts[tok] = df_counts.get(tok, 0) + 1

    if not df_counts:
        return sparse.csr_matrix((n_articles, 0), dtype=np.float32)

    sorted_vocab = sorted(df_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_vocab]
    vocab = {tok: idx for idx, (tok, _) in enumerate(sorted_vocab)}
    vocab_size = len(vocab)

    # Pass 2: stream (row, col, tf) into compact primitive arrays.
    rows_buf = array('i')
    cols_buf = array('i')
    vals_buf = array('f')
    for row_idx in range(n_articles):
        for tok, tf_val in _extract_article_tokens(cat_columns, desc_col, row_idx):
            col_idx = vocab.get(tok)
            if col_idx is not None:
                rows_buf.append(row_idx)
                cols_buf.append(col_idx)
                vals_buf.append(tf_val)

    if not rows_buf:
        return sparse.csr_matrix((n_articles, 0), dtype=np.float32)

    row_arr = np.frombuffer(rows_buf, dtype=np.int32)
    col_arr = np.frombuffer(cols_buf, dtype=np.int32)
    val_arr = np.frombuffer(vals_buf, dtype=np.float32).copy()

    df_arr = np.array([df_counts[tok] for tok, _ in sorted_vocab], dtype=np.float32)
    idf_arr = (
        np.log((np.float32(n_articles) + np.float32(1.0)) / (df_arr + np.float32(1.0)))
        + np.float32(1.0)
    ).astype(np.float32)
    val_arr *= idf_arr[col_arr]

    tfidf = sparse.csr_matrix(
        (val_arr, (row_arr, col_arr)),
        shape=(n_articles, vocab_size),
        dtype=np.float32,
    )
    tfidf.sum_duplicates()
    tfidf.sort_indices()

    row_sq = np.asarray(tfidf.power(2).sum(axis=1)).ravel().astype(np.float32)
    inv_norms = np.zeros(n_articles, dtype=np.float32)
    nz = row_sq > 0.0
    inv_norms[nz] = np.float32(1.0) / np.sqrt(row_sq[nz])
    row_rep = np.repeat(np.arange(n_articles, dtype=np.int32), np.diff(tfidf.indptr))
    tfidf.data *= inv_norms[row_rep]
    return tfidf


def _build_bm25_interaction_csr(
    tx_lf: pl.LazyFrame,
    catalog: pl.DataFrame,
    cutoff: date,
    *,
    window_days: int,
    half_life_days: float,
    k1: float,
    b: float,
) -> sparse.csr_matrix:
    n_articles = catalog.height
    if n_articles == 0:
        return sparse.csr_matrix((0, 0), dtype=np.float32)

    lower = cutoff - timedelta(days=window_days)
    nodes_df = catalog.select('article_id').with_row_index('node').with_columns(pl.col('node').cast(pl.Int32))

    pairs = (
        tx_lf
        .filter(
            (pl.col('t_dat') >= pl.lit(lower))
            & (pl.col('t_dat') < pl.lit(cutoff))
            & pl.col('customer_id').is_not_null()
        )
        .join(nodes_df.lazy(), on='article_id', how='inner')
        .with_columns(
            (
                2.0 ** (-(pl.lit(cutoff) - pl.col('t_dat')).dt.total_days().cast(pl.Float64) / float(half_life_days))
            )
            .cast(pl.Float64)
            .alias('w')
        )
        .group_by('customer_id', 'node')
        .agg(pl.col('w').sum().cast(pl.Float32).alias('tf'))
        .sort('customer_id', 'node')
        .collect(engine='streaming')
    )

    if pairs.is_empty():
        return sparse.csr_matrix((0, n_articles), dtype=np.float32)

    users = (
        pairs.select('customer_id')
        .unique()
        .sort('customer_id')
        .with_row_index('uid')
        .with_columns(pl.col('uid').cast(pl.Int32))
    )
    n_users = users.height
    mapped = pairs.join(users, on='customer_id', how='left').select('uid', 'node', 'tf').sort('uid', 'node')
    del pairs, users

    u_idx = mapped['uid'].to_numpy().astype(np.int32, copy=False)
    i_idx = mapped['node'].to_numpy().astype(np.int32, copy=False)
    tf = mapped['tf'].to_numpy().astype(np.float32, copy=False)
    del mapped

    df = np.bincount(i_idx, minlength=n_articles).astype(np.float32)
    idf = (
        np.log((np.float32(n_users) + np.float32(1.0)) / (df + np.float32(1.0)))
        + np.float32(1.0)
    ).astype(np.float32)

    user_len = np.bincount(u_idx, weights=tf, minlength=n_users).astype(np.float32)
    avg_len = max(float(user_len.mean(dtype=np.float64)), 1e-6)
    norm_u = np.float32(1.0 - b) + np.float32(b / avg_len) * user_len
    denom = tf + np.float32(k1) * norm_u[u_idx]
    vals = (tf * np.float32(k1 + 1.0) / np.maximum(denom, np.float32(1e-6))) * idf[i_idx]
    vals = vals.astype(np.float32, copy=False)

    mat = sparse.csr_matrix((vals, (u_idx, i_idx)), shape=(n_users, n_articles), dtype=np.float32)
    mat.sum_duplicates()
    mat.sort_indices()
    return mat


def _compute_truncated_svd(
    mat: sparse.csr_matrix,
    *,
    k: int,
) -> tuple[np.ndarray, np.ndarray]:
    n_users, n_articles = mat.shape
    if n_articles == 0 or n_users < 2 or n_articles < 2 or mat.nnz == 0 or k <= 0:
        return np.zeros((n_articles, 0), dtype=np.float32), np.zeros((0,), dtype=np.float32)

    effective_k = min(int(k), min(n_users, n_articles) - 1)
    if effective_k <= 0:
        return np.zeros((n_articles, 0), dtype=np.float32), np.zeros((0,), dtype=np.float32)

    if n_users * n_articles <= _DENSE_SVD_MAX_ELEMENTS:
        _, s_full, vt_full = np.linalg.svd(mat.toarray(), full_matrices=False)
        svd_sigma = np.maximum(s_full[:effective_k], 0.0).astype(np.float32, copy=False)
        svd_v = np.ascontiguousarray(vt_full[:effective_k, :].T, dtype=np.float32)
        return svd_v, svd_sigma

    try:
        _, s, vt = svds(mat, k=effective_k, rng=42)
        order = np.argsort(s)[::-1]
        svd_sigma = np.maximum(s[order], 0.0).astype(np.float32, copy=False)
        svd_v = np.ascontiguousarray(vt[order, :].T, dtype=np.float32)
        return svd_v, svd_sigma
    except Exception:
        if n_users * n_articles <= _FALLBACK_DENSE_MAX_ELEMENTS:
            _, s_full, vt_full = np.linalg.svd(mat.toarray(), full_matrices=False)
            svd_sigma = np.maximum(s_full[:effective_k], 0.0).astype(np.float32, copy=False)
            svd_v = np.ascontiguousarray(vt_full[:effective_k, :].T, dtype=np.float32)
            return svd_v, svd_sigma
        raise


class LatentContentIndex:
    """Compact float32 SVD latent + sparse TF-IDF content index for Stage-1 retrieval and live fold-in."""

    def __init__(
        self,
        *,
        article_ids: Sequence[str],
        svd_v: np.ndarray,
        svd_sigma: np.ndarray,
        content_tfidf: sparse.csr_matrix,
        window_days: int = 180,
        half_life_days: float = 30.0,
    ) -> None:
        self.article_ids: tuple[str, ...] = tuple(article_ids)
        self.n_articles: int = len(self.article_ids)
        self.article_to_idx: dict[str, int] = {aid: i for i, aid in enumerate(self.article_ids)}

        v = np.ascontiguousarray(svd_v, dtype=np.float32)
        if v.ndim != 2 or v.shape[0] != self.n_articles:
            raise ValueError(f'svd_v shape {v.shape} incompatible with {self.n_articles} articles')
        sigma = np.asarray(svd_sigma, dtype=np.float32)
        if sigma.ndim != 1 or sigma.shape[0] != v.shape[1]:
            raise ValueError(f'svd_sigma shape {sigma.shape} incompatible with latent dim {v.shape[1]}')

        tfidf = content_tfidf.tocsr().astype(np.float32, copy=False)
        if tfidf.shape[0] != self.n_articles:
            raise ValueError(f'content_tfidf rows {tfidf.shape[0]} incompatible with {self.n_articles} articles')

        self.svd_v: np.ndarray = v
        self.svd_sigma: np.ndarray = np.maximum(sigma, np.float32(0.0))
        self.sqrt_sigma: np.ndarray = np.sqrt(self.svd_sigma).astype(np.float32, copy=False)
        self.k: int = int(v.shape[1])
        self.content_tfidf: sparse.csr_matrix = tfidf
        self.vocab_size: int = int(tfidf.shape[1])
        self.window_days: int = int(window_days)
        self.half_life_days: float = float(half_life_days)

    @classmethod
    def fit(
        cls,
        transactions: pl.LazyFrame | pl.DataFrame,
        catalog: pl.DataFrame,
        cutoff: date,
        *,
        window_days: int = 180,
        half_life_days: float = 30.0,
        k: int = DEFAULT_LATENT_DIM,
        max_vocab: int = DEFAULT_MAX_VOCAB,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> 'LatentContentIndex':
        _validate_cutoff(cutoff)
        if window_days < 1:
            raise ValueError('window_days must be >= 1')
        if half_life_days <= 0:
            raise ValueError('half_life_days must be > 0')

        if catalog.height > 0 and not catalog['article_id'].is_sorted():
            catalog = catalog.sort('article_id')

        article_ids = tuple(catalog['article_id'].to_list()) if catalog.height > 0 else ()
        tx_lf = transactions.lazy() if isinstance(transactions, pl.DataFrame) else transactions

        mat = _build_bm25_interaction_csr(
            tx_lf,
            catalog,
            cutoff,
            window_days=window_days,
            half_life_days=half_life_days,
            k1=k1,
            b=b,
        )
        svd_v, svd_sigma = _compute_truncated_svd(mat, k=k)
        del mat

        content_tfidf = _build_content_tfidf(catalog, max_vocab=max_vocab)
        return cls(
            article_ids=article_ids,
            svd_v=svd_v,
            svd_sigma=svd_sigma,
            content_tfidf=content_tfidf,
            window_days=window_days,
            half_life_days=half_life_days,
        )

    def fold_in_user(
        self,
        article_indices: Sequence[int] | np.ndarray,
        weights: Sequence[float] | np.ndarray,
    ) -> np.ndarray:
        """Project weighted item interactions into the normalized k-dim latent space in O(len(indices) * k)."""
        if self.k == 0 or self.n_articles == 0:
            return np.zeros((self.k,), dtype=np.float32)

        idx = np.asarray(article_indices, dtype=np.int64)
        w = np.asarray(weights, dtype=np.float32)
        if idx.shape != w.shape or idx.ndim != 1:
            raise ValueError('article_indices and weights must be 1D arrays of equal length')

        valid = (idx >= 0) & (idx < self.n_articles) & np.isfinite(w) & (w != 0.0)
        if not np.any(valid):
            return np.zeros((self.k,), dtype=np.float32)

        idx_v = idx[valid]
        w_v = w[valid]
        raw = (w_v @ self.svd_v[idx_v]) * self.sqrt_sigma
        norm = float(np.linalg.norm(raw))
        if norm <= 1e-12:
            return np.zeros((self.k,), dtype=np.float32)
        return (raw / np.float32(norm)).astype(np.float32, copy=False)

    def query_latent(
        self,
        user_vec: Sequence[float] | np.ndarray,
        top_k: int = 60,
        *,
        exclude_indices: Sequence[int] | np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return top_k (article_indices, scores) in latent SVD space ordered by (-score, +idx)."""
        if self.k == 0 or self.n_articles == 0 or top_k <= 0:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

        u = np.asarray(user_vec, dtype=np.float32)
        if u.shape != (self.k,) or float(np.linalg.norm(u)) <= 1e-12:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

        scores = (self.svd_v @ (u * self.sqrt_sigma)).astype(np.float32, copy=False)
        if exclude_indices is not None:
            ex = np.asarray(exclude_indices, dtype=np.int64)
            ex = ex[(ex >= 0) & (ex < self.n_articles)]
            if ex.size:
                scores[ex] = -np.inf
        return _top_k_1d(scores, top_k)

    def query_content(
        self,
        article_indices: Sequence[int] | np.ndarray,
        weights: Sequence[float] | np.ndarray,
        top_k: int = 60,
        *,
        exclude_indices: Sequence[int] | np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return top_k (article_indices, scores) by TF-IDF cosine similarity ordered by (-score, +idx)."""
        if self.vocab_size == 0 or self.n_articles == 0 or top_k <= 0:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

        idx = np.asarray(article_indices, dtype=np.int64)
        w = np.asarray(weights, dtype=np.float32)
        if idx.shape != w.shape or idx.ndim != 1:
            raise ValueError('article_indices and weights must be 1D arrays of equal length')

        valid = (idx >= 0) & (idx < self.n_articles) & np.isfinite(w) & (w != 0.0)
        if not np.any(valid):
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

        profile = np.asarray(w[valid] @ self.content_tfidf[idx[valid]], dtype=np.float32).ravel()
        norm = float(np.linalg.norm(profile))
        if norm <= 1e-12:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

        profile /= np.float32(norm)
        scores = np.asarray(self.content_tfidf.dot(profile), dtype=np.float32).ravel()
        if exclude_indices is not None:
            ex = np.asarray(exclude_indices, dtype=np.int64)
            ex = ex[(ex >= 0) & (ex < self.n_articles)]
            if ex.size:
                scores[ex] = -np.inf
        return _top_k_1d(scores, top_k)

    def batch_query_latent(
        self,
        user_item_csr: sparse.csr_matrix,
        top_k: int = 60,
        *,
        exclude_purchased: bool = True,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Batched latent retrieval capped at chunk size <= 512 with 1D per-row argpartition."""
        if batch_size < 1:
            raise ValueError('batch_size must be >= 1')
        if user_item_csr.shape[1] != self.n_articles:
            raise ValueError(
                f'user_item_csr columns {user_item_csr.shape[1]} != n_articles {self.n_articles}'
            )

        n_queries = user_item_csr.shape[0]
        eff_k = max(int(top_k), 0)
        out_idx = np.full((n_queries, eff_k), -1, dtype=np.int32)
        out_scores = np.zeros((n_queries, eff_k), dtype=np.float32)
        if n_queries == 0 or eff_k == 0 or self.k == 0 or self.n_articles == 0:
            return out_idx, out_scores

        eff_batch = min(int(batch_size), _DEFAULT_BATCH_SIZE)
        v_scaled_t = np.ascontiguousarray((self.svd_v * self.sqrt_sigma).T, dtype=np.float32)

        for start in range(0, n_queries, eff_batch):
            end = min(start + eff_batch, n_queries)
            sub = user_item_csr[start:end].astype(np.float32, copy=False)
            raw_u = np.asarray(sub @ self.svd_v, dtype=np.float32) * self.sqrt_sigma
            norms = np.linalg.norm(raw_u, axis=1, keepdims=True)
            valid_rows = norms[:, 0] > 1e-12
            if not np.any(valid_rows):
                continue
            unit_u = np.zeros_like(raw_u)
            unit_u[valid_rows] = raw_u[valid_rows] / norms[valid_rows]
            scores_chunk = unit_u @ v_scaled_t  # (<=512, N) float32 (~216 MB max)

            indptr = sub.indptr
            indices = sub.indices
            for r in range(end - start):
                if not valid_rows[r]:
                    continue
                row_scores = scores_chunk[r]
                if exclude_purchased:
                    cols = indices[indptr[r]:indptr[r + 1]]
                    if cols.size:
                        row_scores[cols] = -np.inf
                c_idx, c_sc = _top_k_1d(row_scores, eff_k)
                cnt = c_idx.size
                if cnt:
                    out_idx[start + r, :cnt] = c_idx
                    out_scores[start + r, :cnt] = c_sc

        return out_idx, out_scores

    def batch_query_content(
        self,
        user_item_csr: sparse.csr_matrix,
        top_k: int = 60,
        *,
        exclude_purchased: bool = True,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Batched TF-IDF content retrieval capped at chunk size <= 512 with 1D per-row argpartition."""
        if batch_size < 1:
            raise ValueError('batch_size must be >= 1')
        if user_item_csr.shape[1] != self.n_articles:
            raise ValueError(
                f'user_item_csr columns {user_item_csr.shape[1]} != n_articles {self.n_articles}'
            )

        n_queries = user_item_csr.shape[0]
        eff_k = max(int(top_k), 0)
        out_idx = np.full((n_queries, eff_k), -1, dtype=np.int32)
        out_scores = np.zeros((n_queries, eff_k), dtype=np.float32)
        if n_queries == 0 or eff_k == 0 or self.vocab_size == 0 or self.n_articles == 0:
            return out_idx, out_scores

        eff_batch = min(int(batch_size), _DEFAULT_BATCH_SIZE)

        for start in range(0, n_queries, eff_batch):
            end = min(start + eff_batch, n_queries)
            sub = user_item_csr[start:end].astype(np.float32, copy=False)
            profiles = (sub @ self.content_tfidf).toarray().astype(np.float32, copy=False)
            norms = np.linalg.norm(profiles, axis=1, keepdims=True)
            valid_rows = norms[:, 0] > 1e-12
            if not np.any(valid_rows):
                continue
            profiles[valid_rows] /= norms[valid_rows]
            # (N, <=512) float32 (~216 MB max); slice column r into 422 KB 1D buffer per row.
            scores_nb = np.asarray(self.content_tfidf.dot(profiles.T), dtype=np.float32)

            indptr = sub.indptr
            indices = sub.indices
            for r in range(end - start):
                if not valid_rows[r]:
                    continue
                row_scores = np.array(scores_nb[:, r], dtype=np.float32, copy=True)
                if exclude_purchased:
                    cols = indices[indptr[r]:indptr[r + 1]]
                    if cols.size:
                        row_scores[cols] = -np.inf
                c_idx, c_sc = _top_k_1d(row_scores, eff_k)
                cnt = c_idx.size
                if cnt:
                    out_idx[start + r, :cnt] = c_idx
                    out_scores[start + r, :cnt] = c_sc

        return out_idx, out_scores

    def save(self, directory: str | Path) -> dict[str, Path]:
        out_dir = Path(directory)
        out_dir.mkdir(parents=True, exist_ok=True)
        svd_v_path = out_dir / 'svd_v.npy'
        svd_sigma_path = out_dir / 'svd_sigma.npy'
        tfidf_path = out_dir / 'content_tfidf.npz'
        meta_path = out_dir / 'latent_meta.json'

        np.save(svd_v_path, self.svd_v.astype(np.float32, copy=False))
        np.save(svd_sigma_path, self.svd_sigma.astype(np.float32, copy=False))
        sparse.save_npz(tfidf_path, self.content_tfidf.astype(np.float32, copy=False))
        meta = {
            'k': int(self.k),
            'vocab_size': int(self.vocab_size),
            'n_articles': int(self.n_articles),
            'window_days': int(self.window_days),
            'half_life_days': float(self.half_life_days),
            'article_ids': list(self.article_ids),
        }
        meta_path.write_text(json.dumps(meta))
        return {
            'svd_v': svd_v_path,
            'svd_sigma': svd_sigma_path,
            'content_tfidf': tfidf_path,
            'latent_meta': meta_path,
        }

    @classmethod
    def load(
        cls,
        directory: str | Path,
        *,
        article_ids: Sequence[str] | None = None,
    ) -> 'LatentContentIndex':
        in_dir = Path(directory)
        meta_path = in_dir / 'latent_meta.json'
        meta: dict[str, object] = json.loads(meta_path.read_text()) if meta_path.exists() else {}

        resolved_ids: Sequence[str]
        if article_ids is not None:
            resolved_ids = article_ids
        elif 'article_ids' in meta:
            resolved_ids = [str(a) for a in meta['article_ids']]  # type: ignore[union-attr]
        else:
            raise ValueError('article_ids must be provided or present in latent_meta.json')

        svd_v = np.load(in_dir / 'svd_v.npy').astype(np.float32, copy=False)
        sigma_path = in_dir / 'svd_sigma.npy'
        if sigma_path.exists():
            svd_sigma = np.load(sigma_path).astype(np.float32, copy=False)
        else:
            svd_sigma = np.ones(svd_v.shape[1], dtype=np.float32)

        content_tfidf = sparse.load_npz(in_dir / 'content_tfidf.npz').tocsr().astype(np.float32, copy=False)
        return cls(
            article_ids=resolved_ids,
            svd_v=svd_v,
            svd_sigma=svd_sigma,
            content_tfidf=content_tfidf,
            window_days=int(meta.get('window_days', 180)),  # type: ignore[arg-type]
            half_life_days=float(meta.get('half_life_days', 30.0)),  # type: ignore[arg-type]
        )
