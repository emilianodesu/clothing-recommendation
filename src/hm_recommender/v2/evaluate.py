"""Offline quality-v2 benchmark & gate evaluator for H&M hybrid recommender v2."""
from __future__ import annotations

import os

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')

from collections import Counter
from collections.abc import Mapping, Sequence, Set as AbstractSet
import ctypes
from datetime import date, datetime, timedelta, timezone
import gc
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Literal

import numpy as np
import polars as pl
from pydantic import Field, model_validator

from hm_recommender.contracts import Contract, DatasetManifest
from hm_recommender.data.ingest import load_dataset, scan
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.quality.contracts import digest
from hm_recommender.v2.features import V2FeatureContext
from hm_recommender.v2.ranker import (
    RankerConfig,
    load_booster,
    score_candidates,
    train_sliding_ranker,
)
from hm_recommender.v2.retrieval import MultiCandidateRetriever, RetrievalConfig

SEGMENTS: tuple[str, ...] = ('recent28d', 'history29_90d', 'older_history', 'no_history')
FOLDS_V1_V3: tuple[str, ...] = ('V1', 'V2', 'V3')
CORE_METRICS: tuple[str, ...] = ('map_at_12', 'ndcg_at_12', 'recall_at_200')


def _malloc_trim() -> None:
    try:
        libc = ctypes.CDLL('libc.so.6')
        libc.malloc_trim(0)
    except OSError:
        pass


def _peak_rss_bytes() -> int:
    status_path = Path('/proc/self/status')
    if status_path.is_file():
        for line in status_path.read_text().splitlines():
            if line.startswith('VmHWM:'):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) * 1024
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == 'darwin':
        return int(maxrss)
    return int(maxrss) * 1024


class DescriptiveWindowConfig(Contract):
    fold_id: str = 'TEST'
    cutoff: date
    label_end: date

    @model_validator(mode='after')
    def check_dates(self) -> 'DescriptiveWindowConfig':
        if self.label_end <= self.cutoff:
            raise ValueError('descriptive_test_window label_end must be > cutoff')
        return self


class QualityV2Config(Contract):
    protocol: Literal['quality-v2'] = 'quality-v2'
    seed: int = Field(default=42, ge=0, strict=True)
    label_days: int = Field(default=7, ge=1, strict=True)
    recall_ks: list[int] = Field(default_factory=lambda: [200, 300])
    min_segment_users: int = Field(default=1000, ge=1, strict=True)
    segments: list[str] = Field(default_factory=lambda: list(SEGMENTS))
    folds: dict[str, date]
    descriptive_test_window: DescriptiveWindowConfig
    retrieval: dict[str, Any] = Field(default_factory=dict)
    ranker: dict[str, Any] = Field(default_factory=dict)
    gates: dict[str, Any] = Field(default_factory=dict)
    baselines_v1: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_config(self) -> 'QualityV2Config':
        if not self.folds:
            raise ValueError('folds must be non-empty')
        if not self.recall_ks or any(isinstance(k, bool) or not isinstance(k, int) or k < 1 for k in self.recall_ks):
            raise ValueError('recall_ks must contain positive integers')
        self.to_retrieval_config()
        self.to_ranker_config()
        return self

    def to_retrieval_config(self) -> RetrievalConfig:
        return RetrievalConfig(**self.retrieval)

    def to_ranker_config(self) -> RankerConfig:
        params = dict(self.ranker)
        if 'label_gain' in params and isinstance(params['label_gain'], list):
            params['label_gain'] = tuple(int(v) for v in params['label_gain'])
        return RankerConfig(**params)


def load_quality_v2_config(path: Path | str) -> QualityV2Config:
    return QualityV2Config.model_validate_json(Path(path).read_text())


def compute_buyer_metrics(
    ranked_articles: Sequence[str],
    relevant: AbstractSet[str],
    ks: Sequence[int] = (200, 300),
) -> dict[str, float]:
    if not relevant:
        raise ValueError('Users without purchases must be counted as excluded')
    n_rel = len(relevant)
    hits = np.asarray([value in relevant for value in ranked_articles], dtype=np.float64)
    cut = hits[:12]
    if cut.size == 0:
        ap = 0.0
        dcg = 0.0
    else:
        ap = float(np.sum(cut * np.cumsum(cut) / np.arange(1, len(cut) + 1)) / min(12, n_rel))
        dcg = float(np.sum(cut / np.log2(np.arange(2, len(cut) + 2))))
    ideal = float(np.sum(1.0 / np.log2(np.arange(2, min(12, n_rel) + 2))))
    out: dict[str, float] = {
        'map_at_12': ap,
        'ndcg_at_12': float(dcg / ideal) if ideal > 0.0 else 0.0,
    }
    for k in ks:
        out[f'recall_at_{k}'] = float(hits[:k].sum() / n_rel)
    return out


def _normalize_dataset_or_frames(
    dataset_or_frames: (
        DatasetManifest
        | Path
        | str
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame]
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame | None]
    ),
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame | None, DatasetManifest | None]:
    if isinstance(dataset_or_frames, (str, Path)):
        ds = load_dataset(Path(dataset_or_frames))
        has_customers = any(p.path.startswith('customers/') for p in ds.partitions)
        return (
            scan(ds, 'transactions'),
            scan(ds, 'articles'),
            scan(ds, 'customers') if has_customers else None,
            ds,
        )
    if isinstance(dataset_or_frames, DatasetManifest):
        has_customers = any(p.path.startswith('customers/') for p in dataset_or_frames.partitions)
        return (
            scan(dataset_or_frames, 'transactions'),
            scan(dataset_or_frames, 'articles'),
            scan(dataset_or_frames, 'customers') if has_customers else None,
            dataset_or_frames,
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
        return tx_lf, art_lf, cust_lf, None
    raise ValueError('Unsupported dataset_or_frames type')


def read_fold_labels(
    tx_lf: pl.LazyFrame,
    cutoff: date,
    label_end: date,
) -> tuple[list[str], dict[str, tuple[set[str], set[str]]]]:
    if type(cutoff) is not date or type(label_end) is not date or label_end <= cutoff:
        raise ValueError('Explicit ordered date cutoff and label_end required')
    labels_lf = (
        tx_lf
        .filter(
            (pl.col('t_dat') >= pl.lit(cutoff))
            & (pl.col('t_dat') < pl.lit(label_end))
            & pl.col('customer_id').is_not_null()
            & pl.col('article_id').is_not_null()
        )
        .select(
            pl.col('customer_id').cast(pl.String, strict=False),
            pl.col('article_id').cast(pl.String, strict=False),
        )
        .unique()
    )
    seen_lf = (
        tx_lf
        .filter(
            (pl.col('t_dat') < pl.lit(cutoff))
            & pl.col('customer_id').is_not_null()
            & pl.col('article_id').is_not_null()
        )
        .select(
            pl.col('customer_id').cast(pl.String, strict=False),
            pl.col('article_id').cast(pl.String, strict=False),
        )
        .join(labels_lf, on=['customer_id', 'article_id'], how='semi')
        .unique()
        .with_columns(pl.lit(True).alias('seen_before'))
    )
    joined = (
        labels_lf
        .join(seen_lf, on=['customer_id', 'article_id'], how='left')
        .with_columns(pl.col('seen_before').fill_null(False))
        .collect(engine='streaming')
    )
    if joined.is_empty():
        return [], {}
    grouped = joined.group_by('customer_id').agg('article_id', 'seen_before').sort('customer_id')
    buyers: list[str] = []
    truth: dict[str, tuple[set[str], set[str]]] = {}
    for cid, items, seen in grouped.iter_rows():
        c_str = str(cid)
        rel = {str(a) for a in items}
        rep = {str(a) for a, s in zip(items, seen) if s}
        buyers.append(c_str)
        truth[c_str] = (rel, rep)
    return buyers, truth


def compute_user_segments(
    tx_lf: pl.LazyFrame,
    cutoff: date,
    buyers: Sequence[str],
) -> dict[str, str]:
    if not buyers:
        return {}
    buyers_lf = pl.DataFrame({'customer_id': list(buyers)}).lazy()
    last_dates = (
        tx_lf
        .filter((pl.col('t_dat') < pl.lit(cutoff)) & pl.col('customer_id').is_not_null())
        .select(pl.col('customer_id').cast(pl.String, strict=False), pl.col('t_dat'))
        .join(buyers_lf, on='customer_id', how='semi')
        .group_by('customer_id')
        .agg(pl.col('t_dat').max().alias('last_purchase'))
        .collect(engine='streaming')
    )
    start28 = cutoff - timedelta(days=28)
    start90 = cutoff - timedelta(days=90)
    seg_df = last_dates.with_columns(
        pl.when(pl.col('last_purchase') >= pl.lit(start28))
        .then(pl.lit('recent28d'))
        .when(pl.col('last_purchase') >= pl.lit(start90))
        .then(pl.lit('history29_90d'))
        .otherwise(pl.lit('older_history'))
        .alias('segment')
    )
    seg_map = dict(seg_df.select('customer_id', 'segment').iter_rows())
    return {b: seg_map.get(b, 'no_history') for b in buyers}


def _past_catalog_and_pop(
    tx_lf: pl.LazyFrame,
    cutoff: date,
    *,
    limit: int = 300,
) -> tuple[set[str], list[str]]:
    past_tx = tx_lf.filter(
        (pl.col('t_dat') < pl.lit(cutoff))
        & pl.col('article_id').is_not_null()
    ).select(
        pl.col('article_id').cast(pl.String, strict=False),
        pl.col('t_dat'),
    )
    catalog_df = past_tx.select('article_id').unique().sort('article_id').collect(engine='streaming')
    if catalog_df.is_empty():
        raise ValueError(f'No eligible catalog before cutoff {cutoff}')
    start7 = cutoff - timedelta(days=7)
    counts_7d = (
        past_tx
        .filter(pl.col('t_dat') >= pl.lit(start7))
        .group_by('article_id')
        .agg(pl.len().cast(pl.UInt32).alias('frequency'))
        .collect(engine='streaming')
    )
    pop_df = (
        catalog_df
        .join(counts_7d, on='article_id', how='left')
        .with_columns(pl.col('frequency').fill_null(0).cast(pl.UInt32))
        .sort(['frequency', 'article_id'], descending=[True, False])
    )
    catalog_set = set(catalog_df['article_id'].to_list())
    pop_list = pop_df['article_id'].head(min(limit, len(catalog_set))).to_list()
    return catalog_set, pop_list


def _exposure_summary(
    counters: dict[int, Counter[str]],
    ordered: dict[int, set[tuple[str, ...]]],
    unordered: dict[int, set[tuple[str, ...]]],
    catalog_size: int,
    max_list_len: int,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for k in sorted(counters):
        counter = counters[k]
        if not counter:
            out[str(k)] = {
                'k': min(k, max_list_len),
                'distinct_items': 0,
                'coverage': 0.0,
                'unique_ordered_lists': 0,
                'unique_sets': 0,
                'top_exposure_share': {'1': 0.0, '5': 0.0, '10': 0.0, '50': 0.0},
                'effective_items': 0.0,
            }
            continue
        counts = np.array(sorted(counter.values(), reverse=True), dtype=np.float64)
        total = float(counts.sum())
        probs = counts / total
        out[str(k)] = {
            'k': min(k, max_list_len),
            'distinct_items': int(len(counts)),
            'coverage': float(len(counts) / catalog_size) if catalog_size > 0 else 0.0,
            'unique_ordered_lists': int(len(ordered[k])),
            'unique_sets': int(len(unordered[k])),
            'top_exposure_share': {str(n): float(counts[:n].sum() / total) for n in (1, 5, 10, 50)},
            'effective_items': float(np.exp(-np.sum(probs * np.log(probs)))),
        }
    return out


class _MetricAccumulator:
    def __init__(self, ks: Sequence[int], catalog_set: AbstractSet[str]) -> None:
        self.ks = tuple(sorted(set(ks)))
        self.metric_keys = ('map_at_12', 'ndcg_at_12', *(f'recall_at_{k}' for k in self.ks))
        self.catalog_set = catalog_set
        self.catalog_size = len(catalog_set)
        self.exposure_ks = tuple(sorted({12, *self.ks}))
        self.counters: dict[int, Counter[str]] = {k: Counter() for k in self.exposure_ks}
        self.ordered: dict[int, set[tuple[str, ...]]] = {k: set() for k in self.exposure_ks}
        self.unordered: dict[int, set[tuple[str, ...]]] = {k: set() for k in self.exposure_ks}

        self.n_users = 0
        self.sum_metrics = {m: 0.0 for m in self.metric_keys}
        self.seg_counts = {s: 0 for s in SEGMENTS}
        self.seg_sums = {s: {m: 0.0 for m in self.metric_keys} for s in SEGMENTS}
        self.target_counts = {'repeat': 0, 'discovery': 0}
        self.target_rel_items = {'repeat': 0, 'discovery': 0}
        self.target_sums = {
            'repeat': {m: 0.0 for m in self.metric_keys},
            'discovery': {m: 0.0 for m in self.metric_keys},
        }
        self.total_rel_items = 0
        self.out_of_catalog_rel = 0
        self.avail_ceiling_sum = 0.0
        self.per_buyer_rows: list[tuple[str, float, float, float]] = []

    def add(
        self,
        customer_id: str,
        ranked_articles: Sequence[str],
        relevant: AbstractSet[str],
        repeated: AbstractSet[str],
        segment: str,
        *,
        record_per_buyer: bool = True,
    ) -> dict[str, float]:
        m = compute_buyer_metrics(ranked_articles, relevant, ks=self.ks)
        self.n_users += 1
        for k in self.metric_keys:
            self.sum_metrics[k] += m[k]
        if segment not in self.seg_counts:
            segment = 'no_history'
        self.seg_counts[segment] += 1
        for k in self.metric_keys:
            self.seg_sums[segment][k] += m[k]

        n_rel = len(relevant)
        n_avail = len(relevant & self.catalog_set)
        self.total_rel_items += n_rel
        self.out_of_catalog_rel += n_rel - n_avail
        self.avail_ceiling_sum += n_avail / n_rel

        discovery = relevant - repeated
        for t_name, subset in (('repeat', repeated), ('discovery', discovery)):
            if subset:
                tm = compute_buyer_metrics(ranked_articles, subset, ks=self.ks)
                self.target_counts[t_name] += 1
                self.target_rel_items[t_name] += len(subset)
                for k in self.metric_keys:
                    self.target_sums[t_name][k] += tm[k]

        for ek in self.exposure_ks:
            vals = tuple(ranked_articles[:ek])
            self.counters[ek].update(vals)
            self.ordered[ek].add(vals)
            self.unordered[ek].add(tuple(sorted(vals)))

        if record_per_buyer:
            self.per_buyer_rows.append((
                customer_id,
                m['map_at_12'],
                m['ndcg_at_12'],
                m.get('recall_at_200', 0.0),
            ))
        return m

    def _avg_dict(self, sums: dict[str, float], count: int) -> dict[str, float] | None:
        if count <= 0:
            return None
        return {k: float(sums[k] / count) for k in self.metric_keys}

    def finalize(self, max_list_len: int) -> dict[str, Any]:
        return {
            'population': {'users': self.n_users, 'catalog_size': self.catalog_size},
            'metrics': self._avg_dict(self.sum_metrics, self.n_users),
            'segments': {
                s: {
                    'users': self.seg_counts[s],
                    'metrics': self._avg_dict(self.seg_sums[s], self.seg_counts[s]),
                }
                for s in SEGMENTS
            },
            'targets': {
                t_name: {
                    'users': self.target_counts[t_name],
                    'relevant_items': self.target_rel_items[t_name],
                    'metrics': self._avg_dict(self.target_sums[t_name], self.target_counts[t_name]),
                }
                for t_name in ('repeat', 'discovery')
            },
            'denominators': {
                'relevant_items': self.total_rel_items,
                'out_of_catalog_relevant_items': self.out_of_catalog_rel,
                'mean_available_recall_ceiling': (
                    float(self.avail_ceiling_sum / self.n_users) if self.n_users > 0 else 0.0
                ),
            },
            'exposure': _exposure_summary(
                self.counters, self.ordered, self.unordered, self.catalog_size, max_list_len
            ),
        }


def evaluate_hp_baselines_for_fold(
    tx_lf: pl.LazyFrame,
    cutoff: date,
    buyers: Sequence[str],
    truth: Mapping[str, tuple[set[str], set[str]]],
    segments: Mapping[str, str],
    catalog_set: AbstractSet[str],
    *,
    ks: Sequence[int] = (200, 300),
    record_per_buyer: bool = True,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[tuple[str, float, float, float]]]]:
    """Pure Polars/Python H (28d history + 7d pop) & P (7d pop) baselines without ranking.history."""
    _, pop_list = _past_catalog_and_pop(tx_lf, cutoff, limit=max(200, *ks))
    pop_200 = pop_list[:min(200, len(catalog_set))]

    buyers_lf = pl.DataFrame({'customer_id': list(buyers)}).lazy()
    start28 = cutoff - timedelta(days=28)
    hist_df = (
        tx_lf
        .filter(
            (pl.col('t_dat') >= pl.lit(start28))
            & (pl.col('t_dat') < pl.lit(cutoff))
            & pl.col('customer_id').is_not_null()
            & pl.col('article_id').is_not_null()
        )
        .select(
            pl.col('customer_id').cast(pl.String, strict=False),
            pl.col('article_id').cast(pl.String, strict=False),
            pl.col('t_dat'),
        )
        .join(buyers_lf, on='customer_id', how='semi')
        .group_by('customer_id', 'article_id')
        .agg(pl.col('t_dat').max().alias('last_purchase'), pl.len().alias('frequency'))
        .sort(['customer_id', 'last_purchase', 'frequency', 'article_id'], descending=[False, True, True, False])
        .group_by('customer_id', maintain_order=True)
        .agg(pl.col('article_id'))
        .collect(engine='streaming')
    )
    hist_map: dict[str, list[str]] = {
        str(cid): [str(a) for a in arts[:200]]
        for cid, arts in hist_df.iter_rows()
    }
    del hist_df

    limit_200 = len(pop_200)
    acc_h = _MetricAccumulator(ks, catalog_set)
    acc_p = _MetricAccumulator(ks, catalog_set)

    for cid in buyers:
        rel, rep = truth[cid]
        seg = segments.get(cid, 'no_history')
        acc_p.add(cid, pop_200, rel, rep, seg, record_per_buyer=record_per_buyer)

        u_hist = hist_map.get(cid)
        if not u_hist:
            h_list = pop_200
        else:
            seen: set[str] = set()
            h_list: list[str] = []
            for a in u_hist:
                if a in catalog_set and a not in seen:
                    seen.add(a)
                    h_list.append(a)
                    if len(h_list) == limit_200:
                        break
            if len(h_list) < limit_200:
                for a in pop_200:
                    if a not in seen:
                        seen.add(a)
                        h_list.append(a)
                        if len(h_list) == limit_200:
                            break
        acc_h.add(cid, h_list, rel, rep, seg, record_per_buyer=record_per_buyer)

    del hist_map
    reports = {
        'H': acc_h.finalize(limit_200),
        'P': acc_p.finalize(limit_200),
    }
    per_buyer = {
        'H': acc_h.per_buyer_rows,
        'P': acc_p.per_buyer_rows,
    }
    return reports, per_buyer


def _sample_buyers(buyers: list[str], pilot_users: int | None, seed: int, fold_id: str) -> list[str]:
    if pilot_users is None or len(buyers) <= pilot_users:
        return buyers
    sampled = sorted(
        buyers,
        key=lambda c: (hashlib.sha256(f'{seed}:{fold_id}:{c}'.encode()).digest(), c),
    )[:pilot_users]
    return sorted(sampled)


def evaluate_fold(
    dataset_or_frames: (
        DatasetManifest
        | Path
        | str
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame]
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame | None]
    ),
    fold_id: str,
    cutoff: date,
    *,
    label_days: int = 7,
    retrieval_config: RetrievalConfig | None = None,
    ranker_config: RankerConfig | None = None,
    work_dir: Path | str | None = None,
    pilot_users: int | None = None,
    seed: int = 42,
    eval_batch_users: int = 2048,
    ks: Sequence[int] = (200, 300),
    compute_baselines: bool = True,
    per_buyer_out: Path | None = None,
) -> dict[str, Any]:
    if type(cutoff) is not date:
        raise ValueError('Explicit date cutoff required')
    if label_days < 1:
        raise ValueError('label_days must be >= 1')

    t_start = time.monotonic()
    r_cfg = retrieval_config if retrieval_config is not None else RetrievalConfig()
    rk_cfg = ranker_config if ranker_config is not None else RankerConfig()
    ks_tuple = tuple(sorted(set(int(k) for k in ks)))
    max_k = max(ks_tuple)

    tx_lf, art_lf, cust_lf, ds = _normalize_dataset_or_frames(dataset_or_frames)
    label_end = cutoff + timedelta(days=label_days)

    owned_tmp: Path | None = None
    if work_dir is None:
        owned_tmp = Path(tempfile.mkdtemp(prefix=f'hm_v2_eval_{fold_id}_'))
        ranker_work_dir = owned_tmp
    else:
        ranker_work_dir = Path(work_dir)
        ranker_work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Train LambdaRank strictly < cutoff
        train_input: Any = ds if ds is not None else (tx_lf, art_lf, cust_lf)
        train_res = train_sliding_ranker(
            train_input,
            cutoff=cutoff,
            config=rk_cfg,
            retrieval_config=r_cfg,
            work_dir=ranker_work_dir,
        )
        model_path = train_res.model_path
        training_summary = {
            'best_iteration': int(train_res.best_iteration),
            'val_ndcg_at_12': float(train_res.val_ndcg_at_12),
            'inner_train_groups': int(train_res.training_metadata.get('inner_train_groups', 0)),
            'inner_train_rows': int(train_res.training_metadata.get('inner_train_rows', 0)),
            'inner_val_groups': int(train_res.training_metadata.get('inner_val_groups', 0)),
            'inner_val_rows': int(train_res.training_metadata.get('inner_val_rows', 0)),
            'refit_groups': int(train_res.training_metadata.get('refit_groups', 0)),
            'refit_rows': int(train_res.training_metadata.get('refit_rows', 0)),
            'prep_seconds': float(train_res.training_metadata.get('prep_seconds', 0.0)),
            'fit_seconds': float(train_res.training_metadata.get('fit_seconds', 0.0)),
            'refit_seconds': float(train_res.training_metadata.get('refit_seconds', 0.0)),
        }
        del train_res
        gc.collect()
        _malloc_trim()

        # Step 2: Load ground-truth labels, segments, and < cutoff pop300
        buyers, truth = read_fold_labels(tx_lf, cutoff, label_end)
        if not buyers:
            raise ValueError(f'Empty label window [{cutoff}, {label_end}) for fold {fold_id}')
        buyers = _sample_buyers(buyers, pilot_users, seed, fold_id)
        segments = compute_user_segments(tx_lf, cutoff, buyers)
        catalog_set, pop_tail = _past_catalog_and_pop(tx_lf, cutoff, limit=max_k)
        target_list_len = min(max_k, len(catalog_set))
        pop_tail = pop_tail[:target_list_len]

        # Step 3: Fit Stage-1 retriever & Stage-2 feature context strictly < cutoff
        retriever = MultiCandidateRetriever.fit_frames(
            tx_lf,
            art_lf,
            cust_lf,
            cutoff=cutoff,
            config=r_cfg,
        )
        feat_ctx = V2FeatureContext.from_frames(
            tx_lf,
            art_lf,
            cust_lf,
            cutoff=cutoff,
            retain_dataframes=False,
        )
        booster = load_booster(model_path)

        # Step 4: Stream buyers in eval_batch_users batches
        acc_v2 = _MetricAccumulator(ks_tuple, catalog_set)
        total_candidates = 0
        union_recall_sum = 0.0
        cold_users = 0
        warm_users = 0
        cold_map_sum = 0.0
        warm_map_sum = 0.0
        record_per_buyer = per_buyer_out is not None or compute_baselines

        for start in range(0, len(buyers), eval_batch_users):
            batch_buyers = buyers[start:start + eval_batch_users]
            cand_batch = retriever.retrieve(batch_buyers)
            feat_batch = feat_ctx.transform(cand_batch)
            scored_batch = score_candidates(
                cand_batch,
                feat_batch,
                booster=booster,
                cold_start_col=rk_cfg.cold_start_col,
                num_threads=rk_cfg.inference_num_threads,
            )
            del cand_batch, feat_batch

            user_lists: dict[str, tuple[list[str], bool]] = {}
            if not scored_batch.is_empty():
                grouped_scored = (
                    scored_batch
                    .group_by('customer_id', maintain_order=True)
                    .agg(
                        pl.col('article_id'),
                        pl.col('is_cold_start').first().alias('is_cold_start'),
                    )
                )
                for cid, arts, is_cold in grouped_scored.iter_rows():
                    user_lists[str(cid)] = ([str(a) for a in arts], bool(is_cold))
            del scored_batch

            for cid in batch_buyers:
                rel, rep = truth[cid]
                seg = segments.get(cid, 'no_history')
                scored_arts, is_cold = user_lists.get(cid, ([], True))

                total_candidates += len(scored_arts)
                union_recall_sum += len(set(scored_arts) & rel) / len(rel)

                # Tail-fill beyond Stage-2 scored list up to target_list_len using < cutoff pop300
                if len(scored_arts) < target_list_len:
                    seen_set = set(scored_arts)
                    filled = list(scored_arts)
                    for a in pop_tail:
                        if a not in seen_set:
                            seen_set.add(a)
                            filled.append(a)
                            if len(filled) == target_list_len:
                                break
                else:
                    filled = scored_arts[:target_list_len]

                m_buyer = acc_v2.add(cid, filled, rel, rep, seg, record_per_buyer=record_per_buyer)
                if is_cold:
                    cold_users += 1
                    cold_map_sum += m_buyer['map_at_12']
                else:
                    warm_users += 1
                    warm_map_sum += m_buyer['map_at_12']

        del retriever, feat_ctx, booster
        gc.collect()
        _malloc_trim()

        v2_report = acc_v2.finalize(target_list_len)
        n_buyers = len(buyers)
        stage1_diagnostics = {
            'mean_candidates_per_user': float(total_candidates / n_buyers) if n_buyers > 0 else 0.0,
            'stage1_union_recall': float(union_recall_sum / n_buyers) if n_buyers > 0 else 0.0,
        }
        cold_start_routing = {
            'cold_users': cold_users,
            'warm_users': warm_users,
            'cold_share': float(cold_users / n_buyers) if n_buyers > 0 else 0.0,
            'cold_map_at_12': float(cold_map_sum / cold_users) if cold_users > 0 else None,
            'warm_map_at_12': float(warm_map_sum / warm_users) if warm_users > 0 else None,
        }

        baselines_report: dict[str, dict[str, Any]] = {}
        hp_per_buyer: dict[str, list[tuple[str, float, float, float]]] = {}
        if compute_baselines:
            baselines_report, hp_per_buyer = evaluate_hp_baselines_for_fold(
                tx_lf,
                cutoff,
                buyers,
                truth,
                segments,
                catalog_set,
                ks=ks_tuple,
                record_per_buyer=(per_buyer_out is not None),
            )

        if per_buyer_out is not None:
            per_buyer_out.parent.mkdir(parents=True, exist_ok=True)
            v2_df = pl.DataFrame(
                acc_v2.per_buyer_rows,
                schema={
                    'customer_id': pl.String,
                    'v2_map_at_12': pl.Float64,
                    'v2_ndcg_at_12': pl.Float64,
                    'v2_recall_at_200': pl.Float64,
                },
                orient='row',
            )
            if compute_baselines and 'H' in hp_per_buyer and 'P' in hp_per_buyer:
                h_df = pl.DataFrame(
                    hp_per_buyer['H'],
                    schema={
                        'customer_id': pl.String,
                        'h_map_at_12': pl.Float64,
                        'h_ndcg_at_12': pl.Float64,
                        'h_recall_at_200': pl.Float64,
                    },
                    orient='row',
                )
                p_df = pl.DataFrame(
                    hp_per_buyer['P'],
                    schema={
                        'customer_id': pl.String,
                        'p_map_at_12': pl.Float64,
                        'p_ndcg_at_12': pl.Float64,
                        'p_recall_at_200': pl.Float64,
                    },
                    orient='row',
                )
                v2_df = v2_df.join(h_df, on='customer_id', how='inner').join(p_df, on='customer_id', how='inner')
            v2_df.sort('customer_id').write_parquet(per_buyer_out, compression='zstd')

        peak_rss = _peak_rss_bytes()
        duration_seconds = float(time.monotonic() - t_start)

        return {
            'fold_id': fold_id,
            'cutoff': cutoff.isoformat(),
            'label_end': label_end.isoformat(),
            'pilot_users': pilot_users,
            'v2': v2_report,
            'baselines': baselines_report,
            'stage1_diagnostics': stage1_diagnostics,
            'cold_start_routing': cold_start_routing,
            'training_summary': training_summary,
            'peak_rss_bytes': peak_rss,
            'duration_seconds': duration_seconds,
        }
    finally:
        if owned_tmp is not None and owned_tmp.exists():
            shutil.rmtree(owned_tmp, ignore_errors=True)


def _mean_v1_v3(fold_reports: Mapping[str, Mapping[str, Any]], key_path: tuple[str, ...]) -> float:
    vals: list[float] = []
    for f in FOLDS_V1_V3:
        cur: Any = fold_reports[f]
        for k in key_path:
            cur = cur[k]
        vals.append(float(cur))
    return float(np.mean(vals))


def evaluate_quality_gates(
    fold_reports: Mapping[str, Mapping[str, Any]],
    config: QualityV2Config,
) -> dict[str, Any]:
    gates_cfg = config.gates
    min_seg_users = int(gates_cfg.get('min_segment_users', config.min_segment_users))
    max_seg_loss = float(gates_cfg.get('max_segment_relative_loss', 0.02))
    target_avg_map = float(gates_cfg.get('target_avg_map_at_12', 0.031))

    has_all_v1_v3 = all(f in fold_reports for f in FOLDS_V1_V3)
    if has_all_v1_v3:
        avg_map = _mean_v1_v3(fold_reports, ('v2', 'metrics', 'map_at_12'))
        avg_ndcg = _mean_v1_v3(fold_reports, ('v2', 'metrics', 'ndcg_at_12'))
        avg_r200 = _mean_v1_v3(fold_reports, ('v2', 'metrics', 'recall_at_200'))
        avg_disc_map = _mean_v1_v3(fold_reports, ('v2', 'targets', 'discovery', 'metrics', 'map_at_12'))
        avg_rep_map = _mean_v1_v3(fold_reports, ('v2', 'targets', 'repeat', 'metrics', 'map_at_12'))
    else:
        eval_folds = [f for f in fold_reports if f != config.descriptive_test_window.fold_id] or list(fold_reports)
        avg_map = float(np.mean([fold_reports[f]['v2']['metrics']['map_at_12'] for f in eval_folds]))
        avg_ndcg = float(np.mean([fold_reports[f]['v2']['metrics']['ndcg_at_12'] for f in eval_folds]))
        avg_r200 = float(np.mean([fold_reports[f]['v2']['metrics']['recall_at_200'] for f in eval_folds]))
        disc_vals = [
            fold_reports[f]['v2']['targets']['discovery']['metrics']['map_at_12']
            for f in eval_folds
            if fold_reports[f]['v2']['targets']['discovery']['metrics'] is not None
        ]
        rep_vals = [
            fold_reports[f]['v2']['targets']['repeat']['metrics']['map_at_12']
            for f in eval_folds
            if fold_reports[f]['v2']['targets']['repeat']['metrics'] is not None
        ]
        avg_disc_map = float(np.mean(disc_vals)) if disc_vals else 0.0
        avg_rep_map = float(np.mean(rep_vals)) if rep_vals else 0.0

    # Baseline references (prefer live H/P if computed across all V1..V3, fallback to config.baselines_v1)
    q1_avg = config.baselines_v1.get('quality_v1_avg', {})
    h_avg_map = float(gates_cfg.get('min_avg_map_over_h', q1_avg.get('H', {}).get('map_at_12', 0.02222467)))
    rg_avg_map = float(gates_cfg.get('min_avg_map_over_rg', q1_avg.get('RG', {}).get('map_at_12', 0.02544361)))
    h_disc_map = float(q1_avg.get('H', {}).get('discovery_map_at_12', 0.00458949))
    p_disc_map = float(q1_avg.get('P', {}).get('discovery_map_at_12', 0.00643740))

    if has_all_v1_v3 and all('H' in fold_reports[f].get('baselines', {}) for f in FOLDS_V1_V3):
        live_h_map = _mean_v1_v3(fold_reports, ('baselines', 'H', 'metrics', 'map_at_12'))
        live_h_disc = _mean_v1_v3(fold_reports, ('baselines', 'H', 'targets', 'discovery', 'metrics', 'map_at_12'))
        live_p_disc = _mean_v1_v3(fold_reports, ('baselines', 'P', 'targets', 'discovery', 'metrics', 'map_at_12'))
        h_avg_map = max(h_avg_map, live_h_map)
        h_disc_map = max(h_disc_map, live_h_disc)
        p_disc_map = max(p_disc_map, live_p_disc)

    beat_h_map = avg_map > h_avg_map
    beat_rg_map = avg_map > rg_avg_map
    reached_target_map = avg_map >= target_avg_map
    beat_h_discovery = avg_disc_map > h_disc_map
    beat_p_discovery = avg_disc_map > p_disc_map

    # Segment regression checks (>= min_seg_users, <= max_seg_loss relative vs H)
    segment_checks: list[dict[str, Any]] = []
    zero_segment_regressions = True
    ref_folds = config.baselines_v1.get('folds', {})

    for fold_id, rep in fold_reports.items():
        v2_segs = rep['v2']['segments']
        live_h_segs = rep.get('baselines', {}).get('H', {}).get('segments', {})
        ref_h_segs = ref_folds.get(fold_id, {}).get('H', {}).get('segments', {})
        for seg_name in config.segments:
            v2_s = v2_segs.get(seg_name)
            if not v2_s or int(v2_s['users']) < min_seg_users or v2_s['metrics'] is None:
                continue
            v2_val = float(v2_s['metrics']['map_at_12'])
            h_s = live_h_segs.get(seg_name) or ref_h_segs.get(seg_name)
            if not h_s or h_s.get('metrics') is None:
                continue
            h_val = float(h_s['metrics']['map_at_12'])
            rel_diff = (v2_val - h_val) / h_val if h_val > 0.0 else 0.0
            passed = v2_val >= h_val * (1.0 - max_seg_loss)
            if not passed:
                zero_segment_regressions = False
            segment_checks.append({
                'fold_id': fold_id,
                'segment': seg_name,
                'users': int(v2_s['users']),
                'v2_map_at_12': v2_val,
                'h_map_at_12': h_val,
                'relative_diff': float(rel_diff),
                'passed': bool(passed),
            })

    test_id = config.descriptive_test_window.fold_id
    test_beat_h_map: bool | None = None
    if test_id in fold_reports:
        test_h_ref = float(
            gates_cfg.get(
                'min_test_map_over_h',
                ref_folds.get(test_id, {}).get('H', {}).get('metrics', {}).get('map_at_12', 0.02628451),
            )
        )
        live_test_h = fold_reports[test_id].get('baselines', {}).get('H', {}).get('metrics', {}).get('map_at_12')
        if live_test_h is not None:
            test_h_ref = max(test_h_ref, float(live_test_h))
        test_beat_h_map = float(fold_reports[test_id]['v2']['metrics']['map_at_12']) > test_h_ref

    all_passed = (
        beat_h_map
        and beat_rg_map
        and beat_h_discovery
        and beat_p_discovery
        and zero_segment_regressions
        and (test_beat_h_map if test_beat_h_map is not None else True)
    )

    return {
        'v1_v3_averages': {
            'map_at_12': avg_map,
            'ndcg_at_12': avg_ndcg,
            'recall_at_200': avg_r200,
            'discovery_map_at_12': avg_disc_map,
            'repeat_map_at_12': avg_rep_map,
        },
        'thresholds': {
            'h_avg_map_at_12': h_avg_map,
            'rg_avg_map_at_12': rg_avg_map,
            'target_avg_map_at_12': target_avg_map,
            'h_discovery_map_at_12': h_disc_map,
            'p_discovery_map_at_12': p_disc_map,
            'max_segment_relative_loss': max_seg_loss,
            'min_segment_users': min_seg_users,
        },
        'checks': {
            'beat_h_map': beat_h_map,
            'beat_rg_map': beat_rg_map,
            'reached_target_map': reached_target_map,
            'beat_h_discovery': beat_h_discovery,
            'beat_p_discovery': beat_p_discovery,
            'zero_segment_regressions': zero_segment_regressions,
            'test_beat_h_map': test_beat_h_map,
        },
        'segment_checks': segment_checks,
        'all_required_gates_passed': bool(all_passed),
    }


def _cluster_bootstrap_v2(
    per_buyer_paths: Mapping[str, Path],
    *,
    resamples: int = 2000,
    seed: int = 42,
) -> dict[str, Any] | None:
    if not all(f in per_buyer_paths and per_buyer_paths[f].is_file() for f in FOLDS_V1_V3):
        return None
    fold_dfs = {f: pl.read_parquet(per_buyer_paths[f]) for f in FOLDS_V1_V3}
    required_cols = {'v2_map_at_12', 'h_map_at_12', 'p_map_at_12'}
    if not all(required_cols <= set(df.columns) for df in fold_dfs.values()):
        return None

    buyers = sorted({cid for df in fold_dfs.values() for cid in df['customer_id'].to_list()})
    if not buyers:
        return None
    index = {c: i for i, c in enumerate(buyers)}
    n = len(buyers)
    # 6 columns: v2-H (map, ndcg, r200), v2-P (map, ndcg, r200)
    cols = 6
    matrix = np.zeros((n, 3 * (1 + cols)), dtype=np.float64)
    means: list[np.ndarray] = []

    for f_idx, f_name in enumerate(FOLDS_V1_V3):
        df = fold_dfs[f_name]
        cids = df['customer_id'].to_list()
        diffs = np.column_stack([
            df['v2_map_at_12'].to_numpy() - df['h_map_at_12'].to_numpy(),
            df['v2_ndcg_at_12'].to_numpy() - df['h_ndcg_at_12'].to_numpy(),
            df['v2_recall_at_200'].to_numpy() - df['h_recall_at_200'].to_numpy(),
            df['v2_map_at_12'].to_numpy() - df['p_map_at_12'].to_numpy(),
            df['v2_ndcg_at_12'].to_numpy() - df['p_ndcg_at_12'].to_numpy(),
            df['v2_recall_at_200'].to_numpy() - df['p_recall_at_200'].to_numpy(),
        ]).astype(np.float64, copy=False)
        pos = [index[c] for c in cids]
        start = f_idx * (1 + cols)
        matrix[pos, start] = 1.0
        matrix[pos, start + 1:start + 1 + cols] = diffs
        means.append(diffs.mean(axis=0))

    rng = np.random.default_rng(seed)
    accepted: list[np.ndarray] = []
    discarded = 0
    attempted = 0
    while len(accepted) < resamples:
        batch = min(16, resamples - len(accepted))
        counts = np.empty((batch, n), dtype=np.float64)
        for row in range(batch):
            counts[row] = np.bincount(rng.integers(0, n, size=n), minlength=n)
        totals = (counts @ matrix).reshape(batch, 3, 1 + cols)
        for row in totals:
            attempted += 1
            if (row[:, 0] == 0).any():
                discarded += 1
            else:
                accepted.append((row[:, 1:] / row[:, :1]).mean(axis=0))
        if attempted > resamples * 100:
            raise ValueError('Too many empty-fold bootstrap draws')

    draws = np.asarray(accepted)
    point = np.mean(means, axis=0)
    contrasts: dict[str, dict[str, Any]] = {}
    for b_idx, b_name in enumerate(('v2_minus_H', 'v2_minus_P')):
        contrasts[b_name] = {}
        for m_idx, metric in enumerate(CORE_METRICS):
            p_idx = b_idx * 3 + m_idx
            entry: dict[str, Any] = {
                'delta': float(point[p_idx]),
                'ci_95': np.quantile(draws[:, p_idx], [0.025, 0.975]).tolist(),
            }
            if metric == 'map_at_12':
                entry['family_ci'] = np.quantile(draws[:, p_idx], [0.05 / 4.0, 1.0 - 0.05 / 4.0]).tolist()
            contrasts[b_name][metric] = entry

    return {
        'resamples': resamples,
        'seed': seed,
        'cluster': 'customer',
        'fold_weighting': 'equal',
        'unique_buyers': n,
        'discarded_empty_fold_draws': discarded,
        'contrasts': contrasts,
    }


def _aggregate_v1_v3_summary(
    fold_reports: Mapping[str, Mapping[str, Any]],
    config: QualityV2Config,
    bootstrap: dict[str, Any] | None,
) -> dict[str, Any]:
    available = [f for f in FOLDS_V1_V3 if f in fold_reports]
    if not available:
        return {}

    def _mean_metric(path: tuple[str, ...]) -> float | None:
        vals: list[float] = []
        for f in available:
            cur: Any = fold_reports[f]
            for k in path:
                if not isinstance(cur, Mapping) or k not in cur or cur[k] is None:
                    return None
                cur = cur[k]
            vals.append(float(cur))
        return float(np.mean(vals))

    v2_summary: dict[str, float | None] = {
        'map_at_12': _mean_metric(('v2', 'metrics', 'map_at_12')),
        'ndcg_at_12': _mean_metric(('v2', 'metrics', 'ndcg_at_12')),
        'recall_at_200': _mean_metric(('v2', 'metrics', 'recall_at_200')),
        'recall_at_300': _mean_metric(('v2', 'metrics', 'recall_at_300')),
        'discovery_map_at_12': _mean_metric(('v2', 'targets', 'discovery', 'metrics', 'map_at_12')),
        'repeat_map_at_12': _mean_metric(('v2', 'targets', 'repeat', 'metrics', 'map_at_12')),
        'stage1_union_recall': _mean_metric(('stage1_diagnostics', 'stage1_union_recall')),
        'mean_candidates_per_user': _mean_metric(('stage1_diagnostics', 'mean_candidates_per_user')),
    }
    q1_avg = config.baselines_v1.get('quality_v1_avg', {})
    h_ref = q1_avg.get('H', {})
    p_ref = q1_avg.get('P', {})
    rg_ref = q1_avg.get('RG', {})

    deltas: dict[str, Any] = {}
    if v2_summary['map_at_12'] is not None:
        v2_m = float(v2_summary['map_at_12'])
        for b_name, ref in (('H', h_ref), ('P', p_ref), ('RG', rg_ref)):
            if 'map_at_12' in ref:
                b_m = float(ref['map_at_12'])
                deltas[f'v2_vs_{b_name}_map_at_12'] = {
                    'abs_diff': v2_m - b_m,
                    'rel_pct': ((v2_m - b_m) / b_m * 100.0) if b_m > 0.0 else 0.0,
                }
    if v2_summary['discovery_map_at_12'] is not None:
        v2_d = float(v2_summary['discovery_map_at_12'])
        for b_name, ref in (('H', h_ref), ('P', p_ref), ('RG', rg_ref)):
            if 'discovery_map_at_12' in ref:
                b_d = float(ref['discovery_map_at_12'])
                deltas[f'v2_vs_{b_name}_discovery_map_at_12'] = {
                    'abs_diff': v2_d - b_d,
                    'rel_pct': ((v2_d - b_d) / b_d * 100.0) if b_d > 0.0 else 0.0,
                }

    return {
        'folds_included': available,
        'v2': v2_summary,
        'baselines_v1_reference': q1_avg,
        'deltas': deltas,
        'bootstrap': bootstrap,
    }


def run_quality_v2(
    dataset_or_path: (
        DatasetManifest
        | Path
        | str
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame]
        | tuple[pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame, pl.LazyFrame | pl.DataFrame | None]
    ),
    config_path: Path | str,
    output_dir: Path | str,
    *,
    folds: Sequence[str] | None = None,
    include_test: bool = True,
    pilot_users: int | None = None,
    subprocess_per_fold: bool = True,
    compute_baselines: bool = True,
    resume: bool = False,
) -> dict[str, Any]:
    cfg_path = Path(config_path)
    config = load_quality_v2_config(cfg_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    r_cfg = config.to_retrieval_config()
    rk_cfg = config.to_ranker_config()
    eval_batch_users = int(config.budget.get('eval_batch_users', 2048))
    bootstrap_resamples = int(config.budget.get('bootstrap_resamples', 2000))

    fold_cutoffs: dict[str, date] = {}
    requested_folds = list(folds) if folds is not None else list(config.folds.keys())
    for fid in requested_folds:
        if fid == config.descriptive_test_window.fold_id:
            fold_cutoffs[fid] = config.descriptive_test_window.cutoff
        elif fid in config.folds:
            fold_cutoffs[fid] = config.folds[fid]
        else:
            raise ValueError(f'Unknown fold_id {fid!r}')
    if include_test:
        test_fid = config.descriptive_test_window.fold_id
        fold_cutoffs[test_fid] = config.descriptive_test_window.cutoff

    ds_root: Path | None = None
    ds_id = 'in-memory'
    ds_manifest_sha = ''
    if isinstance(dataset_or_path, (str, Path)):
        ds_root = Path(dataset_or_path)
        ds = load_dataset(ds_root)
        ds_id = ds.dataset_id
        ds_manifest_sha = sha256(ds_root / 'manifest.json')
    elif isinstance(dataset_or_path, DatasetManifest):
        ds_id = dataset_or_path.dataset_id
        if dataset_or_path._root is not None:
            ds_root = dataset_or_path._root
            ds_manifest_sha = sha256(ds_root / 'manifest.json')

    use_subprocess = bool(subprocess_per_fold and ds_root is not None)
    tmp_dir = out_dir / '.fold_tmp'
    tmp_dir.mkdir(parents=True, exist_ok=True)

    fold_reports: dict[str, dict[str, Any]] = {}
    per_buyer_paths: dict[str, Path] = {}
    try:
        for fid, cutoff in fold_cutoffs.items():
            pb_path = tmp_dir / f'{fid}_per_buyer.parquet'
            per_buyer_paths[fid] = pb_path
            if use_subprocess:
                assert ds_root is not None
                fold_json = tmp_dir / f'{fid}_report.json'
                if not (resume and fold_json.is_file() and (not compute_baselines or pb_path.is_file())):
                    cmd = [
                        sys.executable,
                        '-m',
                        'hm_recommender.v2.evaluate',
                        '--dataset',
                        str(ds_root),
                        '--config',
                        str(cfg_path),
                        '--output',
                        str(out_dir),
                        '--single-fold',
                        fid,
                        '--single-fold-out',
                        str(fold_json),
                        '--per-buyer-out',
                        str(pb_path),
                    ]
                    if pilot_users is not None:
                        cmd.extend(['--pilot-users', str(pilot_users)])
                    if not compute_baselines:
                        cmd.append('--no-baselines')
                    subprocess.run(cmd, check=True)
                fold_reports[fid] = json.loads(fold_json.read_text())
                rep_f = fold_reports[fid]
                print(
                    f'[quality-v2] {fid} done: map@12={rep_f["v2"]["metrics"]["map_at_12"]:.6f} '
                    f'rss_mib={rep_f["peak_rss_bytes"] / (1024 * 1024):.1f} '
                    f'secs={rep_f["duration_seconds"]:.1f}',
                    flush=True,
                )
            else:
                fold_reports[fid] = evaluate_fold(
                    dataset_or_path,
                    fid,
                    cutoff,
                    label_days=config.label_days,
                    retrieval_config=r_cfg,
                    ranker_config=rk_cfg,
                    pilot_users=pilot_users,
                    seed=config.seed,
                    eval_batch_users=eval_batch_users,
                    ks=config.recall_ks,
                    compute_baselines=compute_baselines,
                    per_buyer_out=pb_path if compute_baselines else None,
                )

        bootstrap = (
            _cluster_bootstrap_v2(per_buyer_paths, resamples=bootstrap_resamples, seed=config.seed)
            if compute_baselines
            else None
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    gates = evaluate_quality_gates(fold_reports, config)
    summary_v1_v3 = _aggregate_v1_v3_summary(fold_reports, config, bootstrap)

    test_fid = config.descriptive_test_window.fold_id
    descriptive_test = fold_reports.get(test_fid)

    report = {
        'protocol': config.protocol,
        'dataset_id': ds_id,
        'dataset_manifest_sha256': ds_manifest_sha,
        'config_sha256': digest(config.model_dump(mode='json')),
        'generated_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'pilot_users': pilot_users,
        'config': {
            'retrieval': config.retrieval,
            'ranker': config.ranker,
            'gates': config.gates,
        },
        'summary_v1_v3': summary_v1_v3,
        'descriptive_test_2020_09_16': descriptive_test,
        'folds': fold_reports,
        'gates': gates,
    }
    atomic_json(out_dir / 'quality-v2-report.json', report)
    if descriptive_test is not None:
        atomic_json(out_dir / 'test-2020-09-16-report.json', {
            'protocol': config.protocol,
            'dataset_id': ds_id,
            'dataset_manifest_sha256': ds_manifest_sha,
            'config_sha256': report['config_sha256'],
            'generated_at_utc': report['generated_at_utc'],
            'descriptive_test_window': descriptive_test,
            'test_beat_h_map': gates['checks']['test_beat_h_map'],
            'segment_checks': [c for c in gates['segment_checks'] if c['fold_id'] == test_fid],
        })
    return report


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description='Run quality-v2 offline recommender benchmark.')
    parser.add_argument('--dataset', default='data/processed/hm-v1')
    parser.add_argument('--config', default='configs/quality/quality-v2.json')
    parser.add_argument('--output', default='artifacts/quality-v2')
    parser.add_argument('--folds', nargs='*', default=None)
    parser.add_argument('--include-test', action='store_true')
    parser.add_argument('--single-fold', default=None)
    parser.add_argument('--single-fold-out', default=None)
    parser.add_argument('--per-buyer-out', default=None)
    parser.add_argument('--pilot-users', type=int, default=None)
    parser.add_argument('--no-baselines', action='store_true')
    parser.add_argument('--no-subprocess-per-fold', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.single_fold is not None:
        config = load_quality_v2_config(args.config)
        fid = args.single_fold
        if fid == config.descriptive_test_window.fold_id:
            cutoff = config.descriptive_test_window.cutoff
        elif fid in config.folds:
            cutoff = config.folds[fid]
        else:
            raise ValueError(f'Unknown fold {fid!r}')
        rep = evaluate_fold(
            args.dataset,
            fid,
            cutoff,
            label_days=config.label_days,
            retrieval_config=config.to_retrieval_config(),
            ranker_config=config.to_ranker_config(),
            pilot_users=args.pilot_users,
            seed=config.seed,
            eval_batch_users=int(config.budget.get('eval_batch_users', 2048)),
            ks=config.recall_ks,
            compute_baselines=not args.no_baselines,
            per_buyer_out=Path(args.per_buyer_out) if args.per_buyer_out else None,
        )
        out_path = Path(args.single_fold_out) if args.single_fold_out else (Path(args.output) / f'{fid}-report.json')
        atomic_json(out_path, rep)
        return 0

    report = run_quality_v2(
        args.dataset,
        args.config,
        args.output,
        folds=args.folds,
        include_test=args.include_test,
        pilot_users=args.pilot_users,
        subprocess_per_fold=not args.no_subprocess_per_fold,
        compute_baselines=not args.no_baselines,
        resume=args.resume,
    )
    print(json.dumps(report['gates'], indent=2))
    return 0 if report['gates']['all_required_gates_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())


__all__ = [
    'CORE_METRICS',
    'DescriptiveWindowConfig',
    'FOLDS_V1_V3',
    'QualityV2Config',
    'SEGMENTS',
    'compute_buyer_metrics',
    'compute_user_segments',
    'evaluate_fold',
    'evaluate_hp_baselines_for_fold',
    'evaluate_quality_gates',
    'load_quality_v2_config',
    'main',
    'read_fold_labels',
    'run_quality_v2',
]
