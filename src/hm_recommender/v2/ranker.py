"""Stage-2 LightGBM LambdaRank training, out-of-core streaming, and cold-start routing."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import gc
import hashlib
import math
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import scan
from hm_recommender.v2.features import V2_FEATURE_NAMES, V2FeatureContext
from hm_recommender.v2.retrieval import (
    MultiCandidateRetriever,
    RANK_COLUMNS,
    RetrievalConfig,
    SCORE_COLUMNS,
)


def _lgb() -> Any:
    import lightgbm as lgb
    return lgb


def _validate_cutoff(cutoff: date) -> None:
    if type(cutoff) is not date:
        raise ValueError('Explicit date cutoff required')


def _check_int(name: str, val: object, *, min_val: int, max_val: int | None = None) -> None:
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f'{name} must be an int, got {type(val).__name__}')
    if val < min_val or (max_val is not None and val > max_val):
        raise ValueError(f'{name}={val} out of range [{min_val}, {max_val}]')


def _check_float(
    name: str,
    val: object,
    *,
    min_val: float | None = None,
    min_inclusive: bool = True,
    max_val: float | None = None,
    max_inclusive: bool = True,
) -> None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f'{name} must be a float, got {type(val).__name__}')
    fval = float(val)
    if not math.isfinite(fval):
        raise ValueError(f'{name} must be finite, got {val}')
    if min_val is not None:
        if (min_inclusive and fval < min_val) or (not min_inclusive and fval <= min_val):
            raise ValueError(f'{name}={val} below minimum {min_val}')
    if max_val is not None:
        if (max_inclusive and fval > max_val) or (not max_inclusive and fval >= max_val):
            raise ValueError(f'{name}={val} above maximum {max_val}')


@dataclass(frozen=True)
class RankerConfig:
    # Sliding-window protocol
    weeks: int = 5
    val_weeks: int = 1
    # Smart negative sampling (training + refit)
    negatives_per_group: int = 40
    hard_negatives_per_group: int = 20
    exclude_cold_start_train: bool = True
    max_train_buyers_per_week: int | None = None
    # LightGBM core lambdarank hyperparameters
    objective: str = 'lambdarank'
    label_gain: tuple[int, ...] = (0, 1)
    lambdarank_truncation_level: int = 15
    num_boost_round: int = 600
    early_stopping_rounds: int = 40
    learning_rate: float = 0.05
    num_leaves: int = 63
    min_data_in_leaf: int = 100
    max_bin: int = 255
    lambda_l2: float = 1.0
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    # Reproducibility, threading, routing & memory
    seed: int = 42
    num_threads: int = 2
    inference_num_threads: int = 1
    deterministic: bool = True
    force_col_wise: bool = True
    cold_start_col: str = 'user_count180'
    batch_users: int = 4096
    dataset_batch_size: int = 65536
    cleanup_work_files: bool = True

    def __post_init__(self) -> None:
        _check_int('weeks', self.weeks, min_val=2)
        _check_int('val_weeks', self.val_weeks, min_val=1, max_val=self.weeks - 1)
        _check_int('negatives_per_group', self.negatives_per_group, min_val=1)
        _check_int(
            'hard_negatives_per_group',
            self.hard_negatives_per_group,
            min_val=0,
            max_val=self.negatives_per_group,
        )
        if self.max_train_buyers_per_week is not None:
            _check_int('max_train_buyers_per_week', self.max_train_buyers_per_week, min_val=1)
        _check_int('lambdarank_truncation_level', self.lambdarank_truncation_level, min_val=1)
        _check_int('num_boost_round', self.num_boost_round, min_val=1)
        _check_int('early_stopping_rounds', self.early_stopping_rounds, min_val=1)
        _check_int('num_leaves', self.num_leaves, min_val=2)
        _check_int('min_data_in_leaf', self.min_data_in_leaf, min_val=1)
        _check_int('max_bin', self.max_bin, min_val=2, max_val=255)
        _check_int('bagging_freq', self.bagging_freq, min_val=0)
        _check_int('seed', self.seed, min_val=0)
        _check_int('num_threads', self.num_threads, min_val=1)
        _check_int('inference_num_threads', self.inference_num_threads, min_val=1)
        _check_int('batch_users', self.batch_users, min_val=1)
        _check_int('dataset_batch_size', self.dataset_batch_size, min_val=1)

        _check_float('learning_rate', self.learning_rate, min_val=0.0, min_inclusive=False)
        _check_float('lambda_l2', self.lambda_l2, min_val=0.0, min_inclusive=True)
        _check_float(
            'feature_fraction',
            self.feature_fraction,
            min_val=0.0,
            min_inclusive=False,
            max_val=1.0,
            max_inclusive=True,
        )
        _check_float(
            'bagging_fraction',
            self.bagging_fraction,
            min_val=0.0,
            min_inclusive=False,
            max_val=1.0,
            max_inclusive=True,
        )
        if not self.objective or not isinstance(self.objective, str):
            raise ValueError('objective must be a non-empty string')
        if not self.cold_start_col or not isinstance(self.cold_start_col, str):
            raise ValueError('cold_start_col must be a non-empty string')
        for b_name, b_val in (
            ('exclude_cold_start_train', self.exclude_cold_start_train),
            ('deterministic', self.deterministic),
            ('force_col_wise', self.force_col_wise),
            ('cleanup_work_files', self.cleanup_work_files),
        ):
            if not isinstance(b_val, bool):
                raise ValueError(f'{b_name} must be a bool, got {type(b_val).__name__}')
        if (
            not isinstance(self.label_gain, (tuple, list))
            or len(self.label_gain) < 2
            or any(
                isinstance(g, bool) or not isinstance(g, (int, float)) or not math.isfinite(float(g))
                for g in self.label_gain
            )
        ):
            raise ValueError('label_gain must be a sequence of at least 2 finite numeric entries')

    def to_lgb_params(self) -> dict[str, Any]:
        return {
            'objective': self.objective,
            'label_gain': list(self.label_gain),
            'metric': 'None',
            'lambdarank_truncation_level': self.lambdarank_truncation_level,
            'learning_rate': self.learning_rate,
            'num_leaves': self.num_leaves,
            'min_data_in_leaf': self.min_data_in_leaf,
            'max_bin': self.max_bin,
            'lambda_l2': self.lambda_l2,
            'feature_fraction': self.feature_fraction,
            'bagging_fraction': self.bagging_fraction,
            'bagging_freq': self.bagging_freq,
            'device_type': 'cpu',
            'deterministic': self.deterministic,
            'force_col_wise': self.force_col_wise,
            'seed': self.seed,
            'data_random_seed': self.seed,
            'feature_fraction_seed': self.seed,
            'bagging_seed': self.seed,
            'drop_seed': self.seed,
            'extra_seed': self.seed,
            'objective_seed': self.seed,
            'num_threads': self.num_threads,
            'verbosity': -1,
        }


@dataclass(frozen=True)
class TrainedRankerResult:
    model_path: Path
    best_iteration: int
    feature_names: tuple[str, ...]
    val_ndcg_at_12: float
    week_stats: tuple[dict[str, Any], ...]
    training_metadata: dict[str, Any]
    booster: Any | None = None


def _validate_ndcg_inputs(
    labels: np.ndarray,
    groups: np.ndarray,
    relevant: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    labels_arr = np.asarray(labels)
    groups_arr = np.asarray(groups, dtype=np.int64)
    rel_arr = np.asarray(relevant, dtype=np.int64)
    if (
        groups_arr.size == 0
        or len(groups_arr) != len(rel_arr)
        or np.any(groups_arr <= 0)
        or np.any(rel_arr <= 0)
        or int(groups_arr.sum()) != len(labels_arr)
        or not np.isin(labels_arr, [0, 1]).all()
    ):
        raise ValueError('Invalid NDCG groups, labels, or relevant denominators')
    return labels_arr, groups_arr, rel_arr


def project_ndcg_at_12(
    scores: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    relevant: np.ndarray,
) -> float:
    labels_arr, groups_arr, rel_arr = _validate_ndcg_inputs(labels, groups, relevant)
    scores_arr = np.asarray(scores, dtype=np.float64)
    if scores_arr.shape != labels_arr.shape or not np.isfinite(scores_arr).all():
        raise ValueError('Invalid evaluation predictions')

    discounts = 1.0 / np.log2(np.arange(2, 14, dtype=np.float64))
    idcg = np.cumsum(discounts)[np.minimum(rel_arr, 12) - 1]

    if np.all(groups_arr == groups_arr[0]):
        g_size = int(groups_arr[0])
        k = min(12, g_size)
        matrix = scores_arr.reshape(len(groups_arr), g_size)
        order = np.argsort(-matrix, axis=1, kind='stable')[:, :k]
        hits = np.take_along_axis(labels_arr.reshape(matrix.shape), order, axis=1)
        dcg = (hits * discounts[:k]).sum(axis=1)
    else:
        offsets = np.r_[0, np.cumsum(groups_arr)]
        dcg = np.empty(len(groups_arr), dtype=np.float64)
        for idx, (a, b) in enumerate(zip(offsets[:-1], offsets[1:])):
            k = min(12, int(b - a))
            order = np.argsort(-scores_arr[a:b], kind='stable')[:k]
            dcg[idx] = np.dot(labels_arr[a:b][order], discounts[:k])
    return float(np.mean(dcg / idcg))


def project_ndcg_evaluator(
    labels: np.ndarray,
    groups: np.ndarray,
    relevant: np.ndarray,
) -> Callable[[np.ndarray, Any], tuple[str, float, bool]]:
    labels_arr, groups_arr, rel_arr = _validate_ndcg_inputs(labels, groups, relevant)
    discounts = 1.0 / np.log2(np.arange(2, 14, dtype=np.float64))
    idcg = np.cumsum(discounts)[np.minimum(rel_arr, 12) - 1]
    equal = bool(np.all(groups_arr == groups_arr[0]))
    offsets = None if equal else np.r_[0, np.cumsum(groups_arr)]

    def evaluate(scores: np.ndarray, _dataset: Any) -> tuple[str, float, bool]:
        scores_arr = np.asarray(scores, dtype=np.float64)
        if scores_arr.shape != labels_arr.shape or not np.isfinite(scores_arr).all():
            raise ValueError('Invalid inner evaluation predictions')
        if equal:
            g_size = int(groups_arr[0])
            k = min(12, g_size)
            matrix = scores_arr.reshape(len(groups_arr), g_size)
            order = np.argsort(-matrix, axis=1, kind='stable')[:, :k]
            hits = np.take_along_axis(labels_arr.reshape(matrix.shape), order, axis=1)
            dcg = (hits * discounts[:k]).sum(axis=1)
        else:
            assert offsets is not None
            dcg = np.empty(len(groups_arr), dtype=np.float64)
            for idx, (a, b) in enumerate(zip(offsets[:-1], offsets[1:])):
                k = min(12, int(b - a))
                order = np.argsort(-scores_arr[a:b], kind='stable')[:k]
                dcg[idx] = np.dot(labels_arr[a:b][order], discounts[:k])
        return 'project_ndcg_at_12', float(np.mean(dcg / idcg)), True

    return evaluate


def sample_group(
    week: str,
    customer_id: str,
    articles: Sequence[str] | np.ndarray,
    labels: np.ndarray,
    *,
    min_gen_rank: np.ndarray | None = None,
    max_gen_score: np.ndarray | None = None,
    negatives_per_group: int = 40,
    hard_negatives_per_group: int = 20,
    seed: int = 42,
) -> np.ndarray:
    """Smart negative sampler: all positives + hard Stage-1 negatives + SHA-256 negatives."""
    labels_arr = np.asarray(labels)
    positives = np.flatnonzero(labels_arr == 1)
    negatives = np.flatnonzero(labels_arr == 0)
    if positives.size == 0 or negatives.size == 0:
        return np.array([], dtype=np.int64)

    art_list = [str(a) for a in articles]
    hard_cap = min(hard_negatives_per_group, negatives_per_group)
    hard_selected: list[int] = []
    if hard_cap > 0 and min_gen_rank is not None and max_gen_score is not None:
        neg_sorted = sorted(
            (int(i) for i in negatives),
            key=lambda idx: (int(min_gen_rank[idx]), -float(max_gen_score[idx]), art_list[idx]),
        )
        hard_selected = neg_sorted[:hard_cap]

    hard_set = set(hard_selected)
    rem_cap = max(0, negatives_per_group - len(hard_selected))
    hash_selected: list[int] = []
    if rem_cap > 0:
        rem_negs = [int(i) for i in negatives if int(i) not in hard_set]
        if rem_negs:
            rem_sorted = sorted(
                rem_negs,
                key=lambda idx: (
                    hashlib.sha256(f'{seed}:{week}:{customer_id}:{art_list[idx]}'.encode()).digest(),
                    art_list[idx],
                ),
            )
            hash_selected = rem_sorted[:rem_cap]

    selected = list(positives.astype(np.int64)) + hard_selected + hash_selected
    selected.sort(key=lambda idx: art_list[idx])
    return np.asarray(selected, dtype=np.int64)


def _normalize_dataset_or_frames(
    dataset_or_frames: (
        DatasetManifest
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame]
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame | None]
    ),
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame | None]:
    if isinstance(dataset_or_frames, DatasetManifest):
        has_customers = any(p.path.startswith('customers/') for p in dataset_or_frames.partitions)
        return (
            scan(dataset_or_frames, 'transactions'),
            scan(dataset_or_frames, 'articles'),
            scan(dataset_or_frames, 'customers') if has_customers else None,
        )
    if isinstance(dataset_or_frames, tuple):
        if len(dataset_or_frames) == 2:
            tx, art = dataset_or_frames
            cust = None
        elif len(dataset_or_frames) == 3:
            tx, art, cust = dataset_or_frames
        else:
            raise ValueError('dataset_or_frames tuple must have length 2 or 3')
        tx_lf = tx.lazy() if isinstance(tx, pl.DataFrame) else tx
        art_lf = art.lazy() if isinstance(art, pl.DataFrame) else art
        cust_lf = cust.lazy() if isinstance(cust, pl.DataFrame) else cust
        return tx_lf, art_lf, cust_lf
    raise ValueError('Unsupported dataset_or_frames type')


def _build_lgb_dataset(
    parquet_paths: Sequence[Path],
    group_paths: Sequence[Path],
    *,
    work_dir: Path,
    name: str,
    params: dict[str, Any],
    batch_size: int,
    full: bool,
    exclude_cold_start: bool,
    reference: Any | None = None,
) -> tuple[Any, np.ndarray, np.ndarray, np.ndarray | None]:
    """Stream Parquet batches into a flat .f32 file and build an out-of-core lgb.Dataset."""
    lgb = _lgb()

    group_sizes_list: list[np.ndarray] = []
    relevant_list: list[np.ndarray] = []
    total_rows = 0

    for p_path, g_path in zip(parquet_paths, group_paths):
        g_df = pl.read_parquet(g_path)
        if full:
            if exclude_cold_start:
                g_df = g_df.filter(~pl.col('is_cold_start'))
            sizes = g_df['full_rows'].to_numpy().astype(np.int32, copy=False)
        else:
            g_df = g_df.filter(pl.col('sampled_rows') > 0)
            sizes = g_df['sampled_rows'].to_numpy().astype(np.int32, copy=False)
        if sizes.size:
            group_sizes_list.append(sizes)
            relevant_list.append(g_df['relevant_items'].to_numpy().astype(np.int32, copy=False))
        p_rows = pq.ParquetFile(p_path).metadata.num_rows
        if int(sizes.sum()) != p_rows:
            raise ValueError(f'Group row sum ({int(sizes.sum())}) != Parquet rows ({p_rows}) for {p_path}')
        total_rows += p_rows

    if total_rows == 0 or not group_sizes_list:
        raise ValueError(f'No trainable/validation rows in {name} dataset')

    group_sizes = np.concatenate(group_sizes_list)
    relevant = np.concatenate(relevant_list)
    n_features = len(V2_FEATURE_NAMES)

    f32_path = work_dir / f'{name}.features.f32'
    labels_chunks: list[np.ndarray] = []
    written_rows = 0

    with f32_path.open('wb') as fout:
        for p_path in parquet_paths:
            pf = pq.ParquetFile(p_path)
            if pf.metadata.num_rows == 0:
                continue
            for batch in pf.iter_batches(batch_size=batch_size, columns=[*V2_FEATURE_NAMES, 'label']):
                b_df = pl.from_arrow(batch)
                x_chunk = np.ascontiguousarray(
                    b_df.select(V2_FEATURE_NAMES).to_numpy(),
                    dtype=np.float32,
                )
                fout.write(x_chunk.tobytes())
                labels_chunks.append(b_df['label'].to_numpy().astype(np.uint8 if full else np.float32, copy=True))
                written_rows += b_df.height

    if written_rows != total_rows:
        raise ValueError(f'Incomplete streamed feature file for {name}: {written_rows} != {total_rows}')

    labels_arr = np.concatenate(labels_chunks)
    labels_f32 = labels_arr.astype(np.float32, copy=False)
    labels_out = labels_arr if full else None

    class _Float32FileSequence(lgb.Sequence):
        def __init__(self, path: Path, n_rows: int, n_cols: int, b_size: int) -> None:
            self._path = path
            self._n_rows = n_rows
            self._n_cols = n_cols
            self.batch_size = b_size
            self._fh = path.open('rb')
            self._row_bytes = n_cols * 4

        def __getitem__(self, idx: int | slice | list[int]) -> np.ndarray:
            if isinstance(idx, slice):
                start, stop, step = idx.indices(self._n_rows)
                if step != 1:
                    rows = range(start, stop, step)
                    out = np.empty((len(rows), self._n_cols), dtype=np.float64)
                    for pos, r in enumerate(rows):
                        self._fh.seek(r * self._row_bytes)
                        out[pos] = np.frombuffer(self._fh.read(self._row_bytes), dtype=np.float32)
                    return out
                count = max(0, stop - start)
                self._fh.seek(start * self._row_bytes)
                raw = self._fh.read(count * self._row_bytes)
                return np.frombuffer(raw, dtype=np.float32).reshape(count, self._n_cols).astype(np.float64)
            if isinstance(idx, (int, np.integer)):
                self._fh.seek(int(idx) * self._row_bytes)
                raw = self._fh.read(self._row_bytes)
                return np.frombuffer(raw, dtype=np.float32).astype(np.float64)
            out = np.empty((len(idx), self._n_cols), dtype=np.float64)
            for pos, r in enumerate(idx):
                self._fh.seek(int(r) * self._row_bytes)
                out[pos] = np.frombuffer(self._fh.read(self._row_bytes), dtype=np.float32)
            return out

        def __len__(self) -> int:
            return self._n_rows

        def close(self) -> None:
            if not self._fh.closed:
                self._fh.close()

    seq = _Float32FileSequence(f32_path, total_rows, n_features, batch_size)
    try:
        ds = lgb.Dataset(
            seq,
            label=labels_f32,
            group=group_sizes,
            feature_name=list(V2_FEATURE_NAMES),
            params=params,
            reference=reference,
            free_raw_data=True,
        )
        ds.construct()
    finally:
        seq.close()
        if f32_path.exists():
            f32_path.unlink()
        gc.collect()

    return ds, group_sizes, relevant, labels_out


def train_sliding_ranker(
    dataset_or_frames: (
        DatasetManifest
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame]
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame | None]
    ),
    cutoff: date,
    config: RankerConfig | None = None,
    retrieval_config: RetrievalConfig | None = None,
    work_dir: Path | str | None = None,
) -> TrainedRankerResult:
    """Train LightGBM LambdaRank on 5 sliding weeks (4 inner-train + 1 full val -> 5-week refit)."""
    _validate_cutoff(cutoff)
    cfg = config if config is not None else RankerConfig()
    r_cfg = retrieval_config if retrieval_config is not None else RetrievalConfig()
    tx_lf, art_lf, cust_lf = _normalize_dataset_or_frames(dataset_or_frames)

    out_dir = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix='hm_v2_ranker_'))
    out_dir.mkdir(parents=True, exist_ok=True)

    prep_start = time.monotonic()
    week_dirs: list[Path] = []
    sample_paths: list[Path] = []
    val_full_paths: list[Path] = []
    group_paths: list[Path] = []
    val_group_paths: list[Path] = []
    week_stats: list[dict[str, Any]] = []

    try:
        for w_idx, dist in enumerate(range(cfg.weeks, 0, -1)):
            week_cutoff = cutoff - timedelta(days=7 * dist)
            week_end = week_cutoff + timedelta(days=7)
            is_val = dist <= cfg.val_weeks

            labels_df = (
                tx_lf
                .filter(
                    (pl.col('t_dat') >= pl.lit(week_cutoff))
                    & (pl.col('t_dat') < pl.lit(week_end))
                    & pl.col('customer_id').is_not_null()
                    & pl.col('article_id').is_not_null()
                )
                .select(
                    pl.col('customer_id').cast(pl.String, strict=False),
                    pl.col('article_id').cast(pl.String, strict=False),
                )
                .unique()
                .collect(engine='streaming')
            )
            if labels_df.is_empty():
                raise ValueError(f'Empty label window [{week_cutoff}, {week_end})')

            truth: dict[str, set[str]] = {
                str(c): set(items)
                for c, items in labels_df.group_by('customer_id').agg('article_id').iter_rows()
            }
            buyers = sorted(truth)
            total_buyers = len(buyers)
            if (
                not is_val
                and cfg.max_train_buyers_per_week is not None
                and len(buyers) > cfg.max_train_buyers_per_week
            ):
                buyers = sorted(
                    sorted(
                        buyers,
                        key=lambda c: (hashlib.sha256(f'{cfg.seed}:{week_cutoff}:{c}'.encode()).digest(), c),
                    )[:cfg.max_train_buyers_per_week]
                )

            retriever = MultiCandidateRetriever.fit_frames(
                tx_lf,
                art_lf,
                cust_lf,
                cutoff=week_cutoff,
                config=r_cfg,
            )
            feat_ctx = V2FeatureContext.from_frames(
                tx_lf,
                art_lf,
                cust_lf,
                cutoff=week_cutoff,
                retain_dataframes=False,
            )

            w_dir = out_dir / f'week-{w_idx}'
            w_dir.mkdir(parents=True, exist_ok=True)
            week_dirs.append(w_dir)

            sample_path = w_dir / 'sample.parquet'
            full_path = w_dir / 'full.parquet'
            groups_path = w_dir / 'groups.parquet'

            sample_writer: pq.ParquetWriter | None = None
            full_writer: pq.ParquetWriter | None = None
            group_records: list[tuple[str, int, int, int, int, bool]] = []
            w_sampled_rows = 0
            w_full_rows = 0
            w_positives = 0
            w_cold_buyers = 0
            w_warm_buyers = 0

            # Pre-create Arrow schema with label column for empty fallback
            out_schema = None

            try:
                for start in range(0, len(buyers), cfg.batch_users):
                    batch_buyers = buyers[start:start + cfg.batch_users]
                    cand_batch = retriever.retrieve(batch_buyers)
                    feat_batch = feat_ctx.transform(cand_batch)

                    if not cand_batch.is_empty():
                        # Verify alignment
                        if (
                            not cand_batch['customer_id'].cast(pl.String, strict=False).equals(feat_batch['customer_id'].cast(pl.String, strict=False))
                            or not cand_batch['article_id'].cast(pl.String, strict=False).equals(feat_batch['article_id'].cast(pl.String, strict=False))
                        ):
                            raise ValueError('Candidate and feature row alignment mismatch')

                        min_rank_arr = (
                            cand_batch
                            .select(pl.min_horizontal(*RANK_COLUMNS))
                            .to_series()
                            .to_numpy()
                        )
                        max_score_arr = (
                            cand_batch
                            .select(pl.max_horizontal(*SCORE_COLUMNS))
                            .to_series()
                            .to_numpy()
                        )
                        cold_col_arr = feat_batch[cfg.cold_start_col].to_numpy()
                        cust_list = feat_batch['customer_id'].to_list()
                        art_list = feat_batch['article_id'].to_list()

                        # Find contiguous group spans in sorted feat_batch
                        sample_indices: list[int] = []
                        full_indices: list[int] = []
                        labels_all = np.zeros(feat_batch.height, dtype=np.uint8)

                        pos = 0
                        n_b = feat_batch.height
                        while pos < n_b:
                            cid = cust_list[pos]
                            end_pos = pos + 1
                            while end_pos < n_b and cust_list[end_pos] == cid:
                                end_pos += 1

                            t_set = truth[cid]
                            g_arts = art_list[pos:end_pos]
                            g_bin = np.fromiter(
                                (1 if a in t_set else 0 for a in g_arts),
                                dtype=np.uint8,
                                count=end_pos - pos,
                            )
                            labels_all[pos:end_pos] = g_bin
                            is_cold = bool(cold_col_arr[pos] <= 0.0)
                            if is_cold:
                                w_cold_buyers += 1
                            else:
                                w_warm_buyers += 1

                            g_pos = int(g_bin.sum())
                            w_positives += g_pos

                            if cfg.exclude_cold_start_train and is_cold:
                                sel = np.array([], dtype=np.int64)
                            else:
                                sel = sample_group(
                                    str(week_cutoff),
                                    cid,
                                    g_arts,
                                    g_bin,
                                    min_gen_rank=min_rank_arr[pos:end_pos],
                                    max_gen_score=max_score_arr[pos:end_pos],
                                    negatives_per_group=cfg.negatives_per_group,
                                    hard_negatives_per_group=cfg.hard_negatives_per_group,
                                    seed=cfg.seed,
                                )

                            if sel.size > 0:
                                sample_indices.extend((pos + sel).tolist())

                            keep_in_val = not (cfg.exclude_cold_start_train and is_cold)
                            if is_val and keep_in_val:
                                full_indices.extend(range(pos, end_pos))

                            group_records.append((
                                cid,
                                len(t_set),
                                int(sel.size),
                                int(end_pos - pos),
                                g_pos,
                                is_cold,
                            ))
                            pos = end_pos

                        labeled_batch = feat_batch.select(*V2_FEATURE_NAMES).with_columns(
                            pl.Series('label', labels_all, dtype=pl.UInt8)
                        )
                        if out_schema is None:
                            out_schema = labeled_batch.head(0).to_arrow().schema

                        if sample_indices:
                            s_arrow = labeled_batch[sample_indices].to_arrow()
                            if sample_writer is None:
                                sample_writer = pq.ParquetWriter(sample_path, s_arrow.schema, compression='zstd')
                            sample_writer.write_table(s_arrow)
                            w_sampled_rows += len(sample_indices)

                        if is_val and full_indices:
                            f_arrow = labeled_batch[full_indices].to_arrow()
                            if full_writer is None:
                                full_writer = pq.ParquetWriter(full_path, f_arrow.schema, compression='zstd')
                            full_writer.write_table(f_arrow)
                            w_full_rows += len(full_indices)

                if sample_writer is None:
                    empty_tbl = pl.DataFrame(
                        schema={**{f: pl.Float32 for f in V2_FEATURE_NAMES}, 'label': pl.UInt8}
                    ).to_arrow()
                    sample_writer = pq.ParquetWriter(sample_path, empty_tbl.schema, compression='zstd')
                    sample_writer.write_table(empty_tbl)

                if is_val and full_writer is None:
                    empty_tbl = pl.DataFrame(
                        schema={**{f: pl.Float32 for f in V2_FEATURE_NAMES}, 'label': pl.UInt8}
                    ).to_arrow()
                    full_writer = pq.ParquetWriter(full_path, empty_tbl.schema, compression='zstd')
                    full_writer.write_table(empty_tbl)
            finally:
                if sample_writer is not None:
                    sample_writer.close()
                if full_writer is not None:
                    full_writer.close()

            groups_df = pl.DataFrame(
                group_records,
                schema={
                    'customer_id': pl.String,
                    'relevant_items': pl.UInt32,
                    'sampled_rows': pl.UInt32,
                    'full_rows': pl.UInt32,
                    'positives': pl.UInt32,
                    'is_cold_start': pl.Boolean,
                },
                orient='row',
            )
            groups_df.write_parquet(groups_path, compression='zstd')

            sample_paths.append(sample_path)
            group_paths.append(groups_path)
            if is_val:
                val_full_paths.append(full_path)
                val_group_paths.append(groups_path)

            week_stats.append({
                'week_index': w_idx,
                'cutoff': str(week_cutoff),
                'label_end': str(week_end),
                'role': 'validation' if is_val else 'inner_train',
                'buyers_total': total_buyers,
                'buyers_used': len(buyers),
                'warm_buyers': w_warm_buyers,
                'cold_buyers': w_cold_buyers,
                'positives_in_candidates': w_positives,
                'sampled_rows': w_sampled_rows,
                'full_rows': w_full_rows,
            })

            del retriever, feat_ctx, labels_df, truth, buyers
            gc.collect()

        prep_seconds = time.monotonic() - prep_start

        lgb = _lgb()
        params = cfg.to_lgb_params()
        n_train_weeks = cfg.weeks - cfg.val_weeks

        train_ds, train_groups, _, _ = _build_lgb_dataset(
            sample_paths[:n_train_weeks],
            group_paths[:n_train_weeks],
            work_dir=out_dir,
            name='inner_train',
            params=params,
            batch_size=cfg.dataset_batch_size,
            full=False,
            exclude_cold_start=cfg.exclude_cold_start_train,
        )
        val_ds, val_groups, val_relevant, val_labels_u8 = _build_lgb_dataset(
            val_full_paths,
            val_group_paths,
            work_dir=out_dir,
            name='inner_val',
            params=params,
            batch_size=cfg.dataset_batch_size,
            full=True,
            exclude_cold_start=cfg.exclude_cold_start_train,
            reference=train_ds,
        )
        assert val_labels_u8 is not None
        evaluator = project_ndcg_evaluator(val_labels_u8, val_groups, val_relevant)
        history: dict[str, dict[str, list[float]]] = {}

        fit_start = time.monotonic()
        selected = lgb.train(
            params,
            train_ds,
            num_boost_round=cfg.num_boost_round,
            valid_sets=[val_ds],
            valid_names=['inner'],
            feval=evaluator,
            callbacks=[
                lgb.early_stopping(cfg.early_stopping_rounds, first_metric_only=True, verbose=False),
                lgb.record_evaluation(history),
            ],
        )
        best_iteration = int(selected.best_iteration)
        if best_iteration <= 0:
            best_iteration = int(selected.current_iteration())
        if not (1 <= best_iteration <= cfg.num_boost_round):
            raise ValueError(f'Invalid selected best_iteration={best_iteration}')

        inner_history = list(history.get('inner', {}).get('project_ndcg_at_12', []))
        val_ndcg = float(selected.best_score.get('inner', {}).get('project_ndcg_at_12', inner_history[best_iteration - 1]))
        fit_seconds = time.monotonic() - fit_start

        inner_train_groups = int(len(train_groups))
        inner_train_rows = int(train_groups.sum())
        inner_val_groups = int(len(val_groups))
        inner_val_rows = int(val_groups.sum())

        del selected, val_ds, train_ds, val_labels_u8, evaluator
        gc.collect()

        # 5-week refit for best_iteration rounds
        refit_ds, refit_groups, _, _ = _build_lgb_dataset(
            sample_paths,
            group_paths,
            work_dir=out_dir,
            name='refit',
            params=params,
            batch_size=cfg.dataset_batch_size,
            full=False,
            exclude_cold_start=cfg.exclude_cold_start_train,
        )
        refit_start = time.monotonic()
        final_booster = lgb.train(params, refit_ds, num_boost_round=best_iteration)
        refit_seconds = time.monotonic() - refit_start

        refit_groups_count = int(len(refit_groups))
        refit_rows_count = int(refit_groups.sum())
        del refit_ds
        gc.collect()

        model_path = out_dir / 'model.txt'
        partial_path = out_dir / 'model.txt.partial'
        final_booster.save_model(str(partial_path), num_iteration=best_iteration)
        partial_path.replace(model_path)

        metadata = {
            'cutoff': cutoff.isoformat(),
            'lightgbm_version': str(lgb.__version__),
            'weeks': cfg.weeks,
            'val_weeks': cfg.val_weeks,
            'inner_train_weeks': n_train_weeks,
            'inner_train_groups': inner_train_groups,
            'inner_train_rows': inner_train_rows,
            'inner_val_groups': inner_val_groups,
            'inner_val_rows': inner_val_rows,
            'refit_groups': refit_groups_count,
            'refit_rows': refit_rows_count,
            'best_iteration': best_iteration,
            'val_ndcg_at_12': val_ndcg,
            'inner_ndcg_history': inner_history,
            'prep_seconds': prep_seconds,
            'fit_seconds': fit_seconds,
            'refit_seconds': refit_seconds,
            'params': params,
            'config': asdict(cfg),
        }
        return TrainedRankerResult(
            model_path=model_path,
            best_iteration=best_iteration,
            feature_names=V2_FEATURE_NAMES,
            val_ndcg_at_12=val_ndcg,
            week_stats=tuple(week_stats),
            training_metadata=metadata,
            booster=final_booster,
        )
    finally:
        if cfg.cleanup_work_files:
            for w_dir in week_dirs:
                if w_dir.exists():
                    shutil.rmtree(w_dir, ignore_errors=True)
            for leftover in out_dir.glob('*.features.f32'):
                leftover.unlink(missing_ok=True)


def load_booster(
    model_path: Path | str,
    *,
    expected_feature_names: Sequence[str] | None = V2_FEATURE_NAMES,
) -> Any:
    lgb = _lgb()
    booster = lgb.Booster(model_file=str(model_path))
    if expected_feature_names is not None and booster.feature_name() != list(expected_feature_names):
        raise ValueError(
            f'Booster feature schema mismatch: expected {len(expected_feature_names)} features, '
            f'got {len(booster.feature_name())}'
        )
    return booster


def predict_scores(
    X: np.ndarray,
    *,
    booster: Any | None,
    is_cold_start: bool | np.ndarray = False,
    rank_seg_pop: np.ndarray | None = None,
    rank_pop7d: np.ndarray | None = None,
    num_threads: int = 1,
) -> np.ndarray:
    """Fast Float64 scoring with cold-start routing to segment_popularity -> pop7d."""
    X_arr = np.asarray(X)
    if X_arr.ndim != 2:
        raise ValueError(f'X must be 2D, got shape {X_arr.shape}')
    n_rows = X_arr.shape[0]
    if n_rows == 0:
        return np.empty(0, dtype=np.float64)
    if not np.isfinite(X_arr).all():
        raise ValueError('Nonfinite features passed to predict_scores')

    if isinstance(is_cold_start, (bool, np.bool_)):
        cold_mask = np.full(n_rows, bool(is_cold_start), dtype=bool)
    else:
        cold_mask = np.asarray(is_cold_start, dtype=bool)
        if cold_mask.shape != (n_rows,):
            raise ValueError(f'is_cold_start shape {cold_mask.shape} != ({n_rows},)')

    scores = np.empty(n_rows, dtype=np.float64)
    if np.any(cold_mask):
        if rank_seg_pop is None or rank_pop7d is None:
            raise ValueError('rank_seg_pop and rank_pop7d are required when cold-start rows are present')
        seg_arr = np.asarray(rank_seg_pop, dtype=np.float64)
        pop_arr = np.asarray(rank_pop7d, dtype=np.float64)
        if seg_arr.shape != (n_rows,) or pop_arr.shape != (n_rows,):
            raise ValueError('rank_seg_pop and rank_pop7d must match X row count')
        c_seg = seg_arr[cold_mask]
        c_pop = pop_arr[cold_mask]
        if not (np.isfinite(c_seg).all() and np.isfinite(c_pop).all() and np.all(c_seg >= 1.0) and np.all(c_pop >= 1.0)):
            raise ValueError('Cold-start ranks must be finite and >= 1')
        scores[cold_mask] = 1.0 / (c_seg * 100_000.0 + c_pop)

    warm_mask = ~cold_mask
    if np.any(warm_mask):
        if booster is None:
            raise ValueError('booster is required when warm rows are present')
        warm_x = np.ascontiguousarray(X_arr[warm_mask], dtype=np.float32)
        warm_preds = np.asarray(booster.predict(warm_x, num_threads=num_threads), dtype=np.float64)
        if warm_preds.shape != (int(warm_mask.sum()),) or not np.isfinite(warm_preds).all():
            raise ValueError('Nonfinite or invalid ranker predictions')
        scores[warm_mask] = warm_preds

    return scores


def score_candidates(
    candidates: pl.DataFrame,
    features: pl.DataFrame,
    *,
    booster: Any | None,
    cold_start_col: str = 'user_count180',
    is_cold_start: bool | Sequence[bool] | np.ndarray | None = None,
    feature_names: Sequence[str] = V2_FEATURE_NAMES,
    num_threads: int = 1,
) -> pl.DataFrame:
    """Score Stage-1 candidates with cold-start routing and deterministic (customer_id, score, article_id) rank."""
    if candidates.height != features.height:
        raise ValueError(f'candidates height ({candidates.height}) != features height ({features.height})')
    if (
        not candidates['customer_id'].cast(pl.String, strict=False).equals(features['customer_id'].cast(pl.String, strict=False))
        or not candidates['article_id'].cast(pl.String, strict=False).equals(features['article_id'].cast(pl.String, strict=False))
    ):
        raise ValueError('candidates and features (customer_id, article_id) alignment mismatch')

    has_reason = 'primary_reason' in candidates.columns
    if candidates.is_empty():
        schema: dict[str, pl.DataType] = {
            'customer_id': pl.String,
            'article_id': pl.String,
            'score': pl.Float64,
            'rank': pl.Int32,
            'is_cold_start': pl.Boolean,
            'score_source': pl.String,
        }
        if has_reason:
            schema['primary_reason'] = pl.String
        return pl.DataFrame(schema=schema)

    if is_cold_start is None:
        if cold_start_col not in features.columns:
            raise ValueError(f'Missing cold_start_col {cold_start_col!r} in features')
        cold_mask = features[cold_start_col].to_numpy() <= 0.0
    elif isinstance(is_cold_start, (bool, np.bool_)):
        cold_mask = np.full(candidates.height, bool(is_cold_start), dtype=bool)
    else:
        cold_mask = np.asarray(is_cold_start, dtype=bool)
        if cold_mask.shape != (candidates.height,):
            raise ValueError(f'is_cold_start length mismatch: {cold_mask.shape} != ({candidates.height},)')

    rank_seg_pop = (
        candidates['rank_seg_pop'].to_numpy()
        if 'rank_seg_pop' in candidates.columns
        else features['rank_seg_pop'].to_numpy()
    )
    rank_pop7d = (
        candidates['rank_pop7d'].to_numpy()
        if 'rank_pop7d' in candidates.columns
        else features['rank_pop7d'].to_numpy()
    )

    X = features.select(feature_names).to_numpy().astype(np.float32, copy=False)
    scores = predict_scores(
        X,
        booster=booster,
        is_cold_start=cold_mask,
        rank_seg_pop=rank_seg_pop,
        rank_pop7d=rank_pop7d,
        num_threads=num_threads,
    )

    sources = np.where(cold_mask, 'segment_popularity', 'lambdarank')
    cols: dict[str, pl.Series] = {
        'customer_id': candidates['customer_id'].cast(pl.String, strict=False),
        'article_id': candidates['article_id'].cast(pl.String, strict=False),
        'score': pl.Series('score', scores, dtype=pl.Float64),
        'is_cold_start': pl.Series('is_cold_start', cold_mask, dtype=pl.Boolean),
        'score_source': pl.Series('score_source', sources, dtype=pl.String),
    }
    if has_reason:
        cols['primary_reason'] = candidates['primary_reason'].cast(pl.String, strict=False)

    out = (
        pl.DataFrame(cols)
        .sort(['customer_id', 'score', 'article_id'], descending=[False, True, False])
        .with_columns(
            pl.col('article_id').cum_count().over('customer_id').cast(pl.Int32).alias('rank')
        )
    )
    ordered_cols = ['customer_id', 'article_id', 'score', 'rank', 'is_cold_start', 'score_source']
    if has_reason:
        ordered_cols.append('primary_reason')
    return out.select(ordered_cols)


__all__ = [
    'RankerConfig',
    'TrainedRankerResult',
    'load_booster',
    'predict_scores',
    'project_ndcg_at_12',
    'project_ndcg_evaluator',
    'sample_group',
    'score_candidates',
    'train_sliding_ranker',
]
