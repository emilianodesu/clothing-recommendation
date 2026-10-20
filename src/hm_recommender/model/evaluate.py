"""Exact dot-product retrieval, deterministic article-ID tie breaking."""
import numpy as np
import polars as pl
import torch

from hm_recommender.contracts import EvaluationReport
from hm_recommender.features.build import VALIDATION, TEST
from hm_recommender.model.inputs import ids, lookup, numeric, select_users


def topk(scores, k=200):
    k = min(k, scores.shape[1])
    output = []
    for row in scores:
        candidates = np.argpartition(-row, k - 1)[:k]
        threshold = row[candidates].min()
        better = np.flatnonzero(row > threshold)
        tied = np.flatnonzero(row == threshold)[:k - len(better)]
        candidates = np.concatenate([better, tied])
        output.append(candidates[np.lexsort((candidates, -row[candidates]))])
    return np.asarray(output, dtype=np.int64)


def ranking_metrics(ranking, relevant):
    if not relevant:
        raise ValueError('Users without purchases must be counted as excluded')
    hits = np.asarray([value in relevant for value in ranking], dtype=np.float64)
    cut = hits[:12]
    ap = np.sum(cut * np.cumsum(cut) / np.arange(1, len(cut) + 1)) / min(12, len(relevant))
    ideal = np.sum(1 / np.log2(np.arange(2, min(12, len(relevant)) + 2)))
    dcg = np.sum(cut / np.log2(np.arange(2, len(cut) + 2)))
    return {'map_at_12': float(ap), 'ndcg_at_12': float(dcg / ideal), 'recall_at_200': float(hits[:200].sum() / len(relevant))}


def evaluate_model(model, inputs, population_size=None):
    labels = pl.concat([pl.read_parquet(path) for path in inputs.validation_files])
    population = ids(sorted(labels['customer_id'].unique()))
    selected = select_users(population, inputs.config.eval_users, inputs.config.seed)
    labels = labels.filter(pl.col('customer_id').is_in([v.decode() for v in selected]))
    groups = labels.group_by('customer_id').agg(pl.col('article_id')).sort('customer_id')
    profiles = groups.select('customer_id').join(inputs.profiles, on='customer_id', how='left').with_columns(
        pl.col('purchase_count').fill_null(0), pl.col('mean_price').fill_null(0),
        pl.col('online_share').fill_null(0), pl.col('recency_days').fill_null(-1))
    user_indices = lookup(inputs.users, ids(profiles['customer_id']))
    history = numeric(profiles)
    popular = inputs.catalog.sort(['purchase_count', 'article_id'], descending=[True, False])['article_id'].to_list()[:200]
    known_items = set(inputs.article_ids)
    buckets = {key: [] for key in ['all', 'new_user', 'known_user', 'users_with_new_items']}
    baseline, covered = [], set()
    outside = 0
    model.eval()
    with torch.inference_mode():
        vectors = torch.cat([model.encode_item(torch.from_numpy(inputs.attributes[i:i + 4096])) for i in range(0, len(inputs.attributes), 4096)])
        for start in range(0, len(groups), 128):
            user_vectors = model.encode_user(torch.from_numpy(user_indices[start:start + 128]), torch.from_numpy(history[start:start + 128]))
            indices = topk((user_vectors @ vectors.T).numpy())
            for offset, ranked in enumerate(indices):
                row = start + offset
                relevant = set(groups['article_id'][row].to_list())
                ranking = inputs.article_ids[ranked].tolist()
                metrics = ranking_metrics(ranking, relevant)
                buckets['all'].append(metrics)
                buckets['new_user' if user_indices[row] == 0 else 'known_user'].append(metrics)
                missing = relevant - known_items
                if missing:
                    buckets['users_with_new_items'].append(metrics)
                outside += len(missing)
                baseline.append(ranking_metrics(popular, relevant))
                covered.update(ranking[:12])
    def average(records):
        return {key: float(np.mean([row[key] for row in records])) if records else 0.0
                for key in ('map_at_12', 'ndcg_at_12', 'recall_at_200')}
    all_users = set(inputs.profiles['customer_id'].to_list()) | {v.decode() for v in population}
    report = EvaluationReport(period_start=VALIDATION, period_end=TEST, evaluated_users=len(groups),
        excluded_users=(population_size if population_size is not None else len(all_users)) - len(population), **average(buckets['all']),
        coverage=len(covered) / len(known_items), popularity=average(baseline),
        segments={key: {'users': len(rows), **average(rows)} for key, rows in buckets.items() if key != 'all'})
    payload = report.model_dump(mode='json')
    payload.update(validation_buyers=len(population), sampled_out_users=len(population) - len(groups),
                   out_of_catalog_relevant_items=outside, exact_catalog_size=len(known_items),
                   new_item_recall_at_200=0.0, excluded_population='registered customers' if population_size is not None else 'users observed before validation end',
                   test_evaluated=False)
    return payload
