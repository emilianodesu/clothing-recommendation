"""Read-only validation diagnostics. Aggregate outputs never contain customer IDs."""
from datetime import timedelta
import json
import math

import numpy as np
import polars as pl
import torch
from pydantic import BaseModel, ConfigDict, Field

from hm_recommender.data.ingest import scan
from hm_recommender.features.build import VALIDATION
from hm_recommender.model.evaluate import evaluate_model, ranking_metrics, topk
from hm_recommender.model.inputs import ids, lookup, numeric, select_users
from hm_recommender.model.train import load_checkpoint
from hm_recommender.model.two_tower import TwoTower


class DiagnosticConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seed: int = Field(default=42, ge=0)
    sample_users: int = Field(default=5000, ge=2, le=10000)


def assert_replay(expected, actual):
    """Compare every original evaluation field, including segment denominators."""
    def equal(a, b):
        if isinstance(a, dict):
            return isinstance(b, dict) and a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
        if isinstance(a, float):
            return isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=0, abs_tol=1e-10)
        return type(a) is type(b) and a == b
    if not equal(expected, actual) or actual.get('test_evaluated') is not False:
        raise ValueError('Validation replay differs from the closed source report')


def verified_model(directory, inputs, source):
    pointer = json.loads((directory / 'latest.json').read_text())
    if pointer != source['checkpoint']:
        raise ValueError('Source checkpoint pointer changed')
    state = load_checkpoint(directory)
    if state['identity'] != inputs.identity or state['identity'] != source['input_identity']:
        raise ValueError('Source input identity mismatch')
    if state['cursor'] != [inputs.config.epochs, 0, 0] or state['rows'] != source['rows_consumed']:
        raise ValueError('Source checkpoint is incomplete')
    model = TwoTower(**state['settings'])
    model.load_state_dict(state['model'], strict=True)
    return model.eval()


def exposure(rankings, catalog_size, k=12):
    selected = rankings[:, :k]
    counts = np.bincount(selected.reshape(-1), minlength=catalog_size)
    positive = counts[counts > 0]
    probabilities = positive / positive.sum()
    descending = np.sort(positive)[::-1]
    return {'k': selected.shape[1], 'distinct_items': len(positive), 'coverage': len(positive) / catalog_size,
            'unique_ordered_lists': len({tuple(row) for row in selected}),
            'unique_sets': len({tuple(sorted(row)) for row in selected}),
            'top_exposure_share': {str(n): float(descending[:n].sum() / positive.sum()) for n in (1, 5, 10, 50)},
            'effective_items': float(np.exp(-np.sum(probabilities * np.log(probabilities))))}


def paired_rankings(left, right, k=12):
    overlaps, jaccards = [], []
    for a, b in zip(left[:, :k], right[:, :k]):
        a, b = set(a), set(b)
        overlaps.append(len(a & b) / len(a))
        jaccards.append(len(a & b) / len(a | b))
    return {'mean_overlap': float(np.mean(overlaps)), 'mean_jaccard': float(np.mean(jaccards)),
            'identical_order_fraction': float(np.mean(np.all(left[:, :k] == right[:, :k], axis=1)))}


def recent_popular(transactions, catalog, days):
    counts = transactions.filter((pl.col('t_dat') >= VALIDATION - timedelta(days=days)) &
                                 (pl.col('t_dat') < VALIDATION)).group_by('article_id').len().collect(engine='streaming')
    ordered = catalog.select('article_id').with_row_index('index').join(counts, on='article_id', how='left').with_columns(pl.col('len').fill_null(0))
    return ordered.sort(['len', 'article_id'], descending=[True, False])['index'].to_numpy()[:200].astype(np.int64)


def metric_average(rankings, truths, article_ids):
    records = [ranking_metrics(article_ids[ranked].tolist(), truth) for ranked, truth in zip(rankings, truths)]
    return {key: float(np.mean([row[key] for row in records])) for key in records[0]}


def diagnose(model, inputs, dataset, expected, config, progress=lambda _: None):
    if dataset.dataset_id != inputs.manifest['dataset_id'] or dataset.source_sha256 != inputs.manifest['source_sha256']:
        raise ValueError('Diagnostic dataset identity mismatch')
    progress('replay')
    replay = evaluate_model(model, inputs, population_size=dataset.counts['customers'])
    assert_replay(expected, replay)
    progress('baselines')
    catalog_size = len(inputs.article_ids)
    baseline_indices = {'historical': inputs.catalog.with_row_index('index').sort(
        ['purchase_count', 'article_id'], descending=[True, False])['index'].to_numpy()[:200].astype(np.int64)}
    for days in (7, 28):
        baseline_indices[f'recent_{days}d'] = recent_popular(scan(dataset), inputs.catalog, days)
    labels = pl.concat([pl.read_parquet(path) for path in inputs.validation_files])
    population = ids(sorted(labels['customer_id'].unique()))
    selected = select_users(population, inputs.config.eval_users, inputs.config.seed)
    groups = labels.filter(pl.col('customer_id').is_in([v.decode() for v in selected])).group_by('customer_id').agg(pl.col('article_id')).sort('customer_id')
    if len(groups) < 2:
        raise ValueError('Diagnostics need at least two validation buyers')
    profiles = groups.select('customer_id').join(inputs.profiles, on='customer_id', how='left').with_columns(
        pl.col('purchase_count').fill_null(0), pl.col('mean_price').fill_null(0),
        pl.col('online_share').fill_null(0), pl.col('recency_days').fill_null(-1))
    user_ids = lookup(inputs.users, ids(profiles['customer_id']))
    histories = numeric(profiles)
    truths = [set(row) for row in groups['article_id'].to_list()]
    cohort = select_users(ids(groups['customer_id']), config.sample_users, config.seed)
    sample = np.flatnonzero(np.isin(ids(groups['customer_id']), cohort))
    order = np.random.default_rng(config.seed).permutation(len(sample))
    permutation = np.empty_like(order)
    permutation[order] = np.roll(order, 1)  # no fixed rows, even for a tiny cohort
    with torch.inference_mode():
        vectors = torch.cat([model.encode_item(torch.from_numpy(inputs.attributes[i:i + 4096])) for i in range(0, catalog_size, 4096)])
        def retrieve(users, history):
            output = []
            for start in range(0, len(users), 128):
                encoded = model.encode_user(torch.from_numpy(users[start:start + 128]), torch.from_numpy(history[start:start + 128]))
                output.append(topk((encoded @ vectors.T).numpy()))
            return np.concatenate(output)
        progress('normal')
        normal = retrieve(user_ids, histories)
        normal_metrics = metric_average(normal, truths, inputs.article_ids)
        for key, value in normal_metrics.items():
            if not math.isclose(value, replay[key], rel_tol=0, abs_tol=1e-10):
                raise ValueError('Diagnostic metric replay mismatch')
        sampled_normal = normal[sample]
        sampled_truths = [truths[i] for i in sample]
        sample_metrics = metric_average(sampled_normal, sampled_truths, inputs.article_ids)
        baselines = {}
        for name, ranking in baseline_indices.items():
            repeated = np.broadcast_to(ranking, (len(groups), len(ranking)))
            baselines[name] = {'metrics': metric_average(repeated, truths, inputs.article_ids),
                               'overlap_with_model_at_12': paired_rankings(normal, repeated)}
        controls = {}
        sampled_ids, sampled_history = user_ids[sample], histories[sample]
        for name, altered_ids, altered_history in (
            ('zero_user_id', np.zeros_like(sampled_ids), sampled_history),
            ('permuted_user_id', sampled_ids[permutation], sampled_history),
            ('permuted_history', sampled_ids, sampled_history[permutation])):
            progress(name)
            rankings = retrieve(altered_ids, altered_history)
            metrics = metric_average(rankings, sampled_truths, inputs.article_ids)
            controls[name] = {'metrics': metrics, 'delta_from_normal_sample': {key: metrics[key] - sample_metrics[key] for key in metrics},
                'comparison_with_normal_at_12': paired_rankings(sampled_normal, rankings),
                'unchanged_input_rows': int(np.count_nonzero((altered_ids == sampled_ids) & np.all(altered_history == sampled_history, axis=1))),
                'exposure_at_12': exposure(rankings, catalog_size)}
    return {'replay_verified': True, 'replay': replay, 'quality_accepted': False, 'test_evaluated': False,
        'population': {'users': len(groups), 'catalog_size': catalog_size, 'known_users': int(np.count_nonzero(user_ids)),
                       'new_users': int(np.count_nonzero(user_ids == 0))},
        'normal': {'metrics': normal_metrics, 'exposure_at_12': exposure(normal, catalog_size),
                   'exposure_at_200': exposure(normal, catalog_size, k=200)},
        'baselines': baselines,
        'sample': {'users': len(sample), 'seed': config.seed, 'known_users': int(np.count_nonzero(sampled_ids)),
                   'new_users': int(np.count_nonzero(sampled_ids == 0)), 'normal_metrics': sample_metrics,
                   'inter_user_comparison_at_12': paired_rankings(sampled_normal, sampled_normal[permutation])},
        'controls': controls}
