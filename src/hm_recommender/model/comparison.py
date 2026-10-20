"""Paired validation of two fixed checkpoints; no fitted parameters or test labels."""
import numpy as np
import polars as pl
import torch
from pydantic import Field

from hm_recommender.data.ingest import scan
from hm_recommender.model.diagnostics import DiagnosticConfig, assert_replay, recent_popular, paired_rankings
from hm_recommender.model.evaluate import evaluate_model, ranking_metrics, topk
from hm_recommender.model.inputs import ids, lookup, numeric, select_users
from hm_recommender.model.comparison_contract import validate_comparison_sources

METRICS = ('map_at_12', 'ndcg_at_12', 'recall_at_200')


class ComparisonConfig(DiagnosticConfig):
    sample_users: int = Field(default=5000, ge=2, le=5000)
    bootstrap_resamples: int = Field(default=2000, ge=200, le=10000)
    permutation_repeats: int = Field(default=10, ge=2, le=30)


def paired_summary(left, right, *, seed, resamples):
    left, right = np.asarray(left, dtype=np.float64), np.asarray(right, dtype=np.float64)
    if left.shape != right.shape or left.ndim != 2 or left.shape[1] != 3 or not len(left):
        raise ValueError('Paired metrics require matching nonempty user-by-three arrays')
    if not np.isfinite(left).all() or not np.isfinite(right).all() or resamples < 1:
        raise ValueError('Invalid paired statistics input')
    delta = left - right
    rng = np.random.default_rng(seed)
    draws = []
    # At most 16 populations in RAM, rather than a resamples x population matrix.
    for start in range(0, resamples, 16):
        indices = rng.integers(0, len(delta), size=(min(16, resamples-start), len(delta)))
        draws.append(delta[indices].mean(axis=1))
    intervals = np.quantile(np.concatenate(draws), [.025, .975], axis=0)
    return {'users': len(delta), 'bootstrap_resamples': resamples, 'seed': seed,
        'method': 'paired user bootstrap; percentile 95%; conditional on fixed trained models',
        'metrics': {key: {'left_mean': float(left[:, i].mean()), 'right_mean': float(right[:, i].mean()),
                         'delta': float(delta[:, i].mean()), 'ci95': intervals[:, i].tolist()}
                    for i, key in enumerate(METRICS)}}


def derangement(size, seed):
    if size < 2:
        raise ValueError('Permutation needs two distinct known users')
    order = np.random.default_rng(seed).permutation(size)
    result = np.empty_like(order)
    result[order] = np.roll(order, 1)
    return result


def metric_rows(rankings, truths, article_ids):
    return np.asarray([[metrics[key] for key in METRICS] for metrics in
        (ranking_metrics(article_ids[row].tolist(), truth) for row, truth in zip(rankings, truths))])


def compare_models(candidate, reference, inputs, dataset, candidate_source, reference_source, config, progress=lambda _: None):
    validate_comparison_sources(candidate_source, reference_source)
    if dataset.dataset_id != inputs.manifest['dataset_id'] or dataset.source_sha256 != inputs.manifest['source_sha256']:
        raise ValueError('Comparison dataset identity mismatch')
    progress('comparison_replay')
    # Epoch count does not affect evaluation. All other config/input fields were checked above.
    for model, source in ((candidate, candidate_source), (reference, reference_source)):
        assert_replay(source['evaluation'], evaluate_model(model, inputs, population_size=dataset.counts['customers']))
    labels = pl.concat([pl.read_parquet(path) for path in inputs.validation_files])
    population = ids(sorted(labels['customer_id'].unique()))
    selected = select_users(population, inputs.config.eval_users, inputs.config.seed)
    groups = labels.filter(pl.col('customer_id').is_in([v.decode() for v in selected])).group_by('customer_id').agg(pl.col('article_id')).sort('customer_id')
    profiles = groups.select('customer_id').join(inputs.profiles, on='customer_id', how='left').with_columns(
        pl.col('purchase_count').fill_null(0), pl.col('mean_price').fill_null(0),
        pl.col('online_share').fill_null(0), pl.col('recency_days').fill_null(-1))
    user_ids = lookup(inputs.users, ids(profiles['customer_id']))
    histories = numeric(profiles)
    truths = [set(row) for row in groups['article_id'].to_list()]
    sample_ids = select_users(ids(groups['customer_id']), config.sample_users, config.seed)
    sample = np.flatnonzero(np.isin(ids(groups['customer_id']), sample_ids))
    known = sample[user_ids[sample] > 0]
    if len(known) < 2:
        raise ValueError('Comparison requires two known users in the fixed sample')
    known_truths = [truths[i] for i in known]
    recent = recent_popular(scan(dataset), inputs.catalog, 7)
    baseline_rows = metric_rows(np.broadcast_to(recent, (len(groups), len(recent))), truths, inputs.article_ids)
    normal_rows, controls = {}, {}
    for name, model in (('candidate', candidate), ('reference', reference)):
        progress('comparison_' + name)
        model.eval()
        with torch.inference_mode():
            vectors = torch.cat([model.encode_item(torch.from_numpy(inputs.attributes[i:i+4096])) for i in range(0, len(inputs.attributes), 4096)])
            def retrieve(users, history):
                output = []
                for start in range(0, len(users), 128):
                    encoded = model.encode_user(torch.from_numpy(users[start:start+128]), torch.from_numpy(history[start:start+128]))
                    output.append(topk((encoded @ vectors.T).numpy()))
                return np.concatenate(output)
            ranking = retrieve(user_ids, histories)
            normal_rows[name] = metric_rows(ranking, truths, inputs.article_ids)
            expected = candidate_source if name == 'candidate' else reference_source
            for i, key in enumerate(METRICS):
                if not np.isclose(normal_rows[name][:, i].mean(), expected['evaluation'][key], rtol=0, atol=1e-10):
                    raise ValueError('Comparison metric replay mismatch')
            known_ranking = ranking[known]
            normal_known = normal_rows[name][known]
            del ranking
            shuffled_mean = np.zeros_like(normal_known)
            records = []
            for repeat in range(config.permutation_repeats):
                seed = config.seed + repeat
                permutation = derangement(len(known), seed)
                shuffled_ids = user_ids[known][permutation]
                ranked = retrieve(shuffled_ids, histories[known])
                metrics = metric_rows(ranked, known_truths, inputs.article_ids)
                shuffled_mean += metrics / config.permutation_repeats
                records.append({'seed': seed, 'unchanged_id_rows': int(np.count_nonzero(shuffled_ids == user_ids[known])),
                    'normal_minus_permuted': {key: float((normal_known[:, i] - metrics[:, i]).mean()) for i, key in enumerate(METRICS)},
                    'comparison_with_normal_at_12': paired_rankings(known_ranking, ranked)})
                progress(f'comparison_{name}_permutation_{repeat+1}')
            controls[name] = {'permutations': records,
                'normal_vs_mean_permuted': paired_summary(normal_known, shuffled_mean, seed=config.seed, resamples=config.bootstrap_resamples)}
    progress('comparison_bootstrap')
    def summary(left, right):
        return paired_summary(left, right, seed=config.seed, resamples=config.bootstrap_resamples)
    return {'completed': True, 'reference_replay_verified': True, 'candidate_replay_verified': True,
        'candidate_run_id': candidate_source['run_id'], 'reference_run_id': reference_source['run_id'],
        'candidate_epochs': candidate_source['config']['epochs'], 'reference_epochs': reference_source['config']['epochs'],
        'population': {'users': len(groups), 'catalog_size': len(inputs.article_ids)},
        'candidate_vs_reference': summary(normal_rows['candidate'], normal_rows['reference']),
        'candidate_vs_recent_7d': summary(normal_rows['candidate'], baseline_rows),
        'reference_vs_recent_7d': summary(normal_rows['reference'], baseline_rows),
        'known_user_controls': {'users': len(known), 'sample_users': len(sample), 'excluded_unknown_users': len(sample)-len(known),
                               'selection_seed': config.seed, **controls},
        'quality_accepted': False, 'test_evaluated': False}
