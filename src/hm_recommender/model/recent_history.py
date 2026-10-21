"""Fixed recent-purchase retrieval protocol; no fitting or test-label access."""
from datetime import timedelta
from typing import Literal

import numpy as np
import polars as pl
import torch

from hm_recommender.data.ingest import scan
from hm_recommender.features.build import VALIDATION
from hm_recommender.model.comparison import ComparisonConfig, METRICS, derangement, metric_rows, paired_summary
from hm_recommender.model.diagnostics import assert_replay, exposure, paired_rankings, recent_popular
from hm_recommender.model.evaluate import evaluate_model, topk
from hm_recommender.model.inputs import ids, lookup, numeric, select_users
from hm_recommender.ranking.history import unique_prefix


class HistoryConfig(ComparisonConfig):
    history_days: Literal[28] = 28
    popularity_days: Literal[7] = 7


def recent_histories(transactions, catalog, customers, days=28):
    """Return aligned catalog indices, using only events strictly before validation."""
    from hm_recommender.quality.temporal import history_table
    selected = transactions.join(pl.DataFrame({'customer_id':customers}).lazy(), on='customer_id', how='semi')
    selected = selected.join(catalog.select('article_id').lazy(), on='article_id', how='semi')
    grouped = history_table(selected, VALIDATION, days).group_by('customer_id', maintain_order=True).agg(pl.col('article_id').head(200))
    indices = {item:i for i,item in enumerate(catalog['article_id'])}
    mapped = {customer:np.asarray([indices[item] for item in items],dtype=np.int64) for customer,items in grouped.iter_rows()}
    return [mapped.get(customer,np.empty(0,dtype=np.int64)) for customer in customers]


def compose_recent(history, popular, *, model=None, k=200):
    # Both variants have an explicit popularity fallback when no recent history exists.
    streams = (history, model, popular) if len(history) and model is not None else (history, popular)
    selected=unique_prefix((map(int,stream) for stream in streams),limit=k)
    if len(selected)==k:return np.asarray(selected,dtype=np.int64)
    raise ValueError('Insufficient unique candidates for recent-history ranking')


def validate_recent_history(model, inputs, dataset, source, config, progress=lambda _: None):
    if dataset.dataset_id != inputs.manifest['dataset_id'] or dataset.source_sha256 != inputs.manifest['source_sha256']:
        raise ValueError('Recent-history dataset identity mismatch')
    progress('history_replay')
    replay = evaluate_model(model, inputs, population_size=dataset.counts['customers'])
    assert_replay(source['evaluation'], replay)
    labels = pl.concat([pl.read_parquet(path) for path in inputs.validation_files])
    population = ids(sorted(labels['customer_id'].unique()))
    selected = select_users(population, inputs.config.eval_users, inputs.config.seed)
    groups = labels.filter(pl.col('customer_id').is_in([v.decode() for v in selected])).group_by('customer_id').agg(pl.col('article_id')).sort('customer_id')
    customers = groups['customer_id'].to_list()
    profiles = groups.select('customer_id').join(inputs.profiles, on='customer_id', how='left').with_columns(
        pl.col('purchase_count').fill_null(0), pl.col('mean_price').fill_null(0),
        pl.col('online_share').fill_null(0), pl.col('recency_days').fill_null(-1))
    user_ids, features = lookup(inputs.users, ids(customers)), numeric(profiles)
    truths = [set(row) for row in groups['article_id'].to_list()]
    progress('history_profiles')
    histories = recent_histories(scan(dataset), inputs.catalog, customers, config.history_days)
    popular = recent_popular(scan(dataset), inputs.catalog, config.popularity_days)
    catalog_size, k = len(inputs.article_ids), min(200, len(inputs.article_ids))
    sizes = np.asarray([len(row) for row in histories])
    with_history = sizes > 0
    sample_ids = select_users(ids(customers), config.sample_users, config.seed)
    sample = np.flatnonzero(np.isin(ids(customers), sample_ids))
    controlled = sample[with_history[sample]]
    if len(controlled) < 2:
        raise ValueError('History controls require two buyers with recent history')
    progress('history_model_ranking')
    model.eval()
    with torch.inference_mode():
        vectors = torch.cat([model.encode_item(torch.from_numpy(inputs.attributes[i:i+4096])) for i in range(0,catalog_size,4096)])
        batches = []
        for start in range(0,len(groups),128):
            encoded = model.encode_user(torch.from_numpy(user_ids[start:start+128]), torch.from_numpy(features[start:start+128]))
            batches.append(topk((encoded @ vectors.T).numpy()))
        model_ranking = np.concatenate(batches)
        del batches, vectors
    methods, rows, sample_rankings = {}, {}, {}
    masks = {'with_recent_history':with_history, 'without_recent_history':~with_history, 'unknown_model_user':user_ids==0}
    def averages(values):
        return {key:float(values[:,i].mean()) for i,key in enumerate(METRICS)} if len(values) else None
    def record(name, ranking):
        metrics = metric_rows(ranking, truths, inputs.article_ids)
        rows[name] = metrics
        sample_rankings[name] = ranking[controlled].copy()
        methods[name] = {'metrics':averages(metrics), 'exposure_at_12':exposure(ranking,catalog_size),
            'exposure_at_200':exposure(ranking,catalog_size,k=200),
            'segments':{segment:{'users':int(mask.sum()),'metrics':averages(metrics[mask])} for segment,mask in masks.items()}}
    record('model', model_ranking)
    for key,value in methods['model']['metrics'].items():
        if not np.isclose(value,replay[key],rtol=0,atol=1e-10):
            raise ValueError('Recent-history metric replay mismatch')
    record('popular_7d', np.broadcast_to(popular,(len(groups),k)))
    for name,use_model in (('recent_then_popular',False),('recent_then_model',True)):
        progress('history_'+name)
        ranking = np.asarray([compose_recent(history,popular,model=model_ranking[i] if use_model else None,k=k)
                              for i,history in enumerate(histories)])
        record(name,ranking)
        del ranking
    progress('history_bootstrap')
    contrasts = {'recent_popular_vs_popular':('recent_then_popular','popular_7d'),
                 'recent_popular_vs_model':('recent_then_popular','model'),
                 'recent_model_vs_recent_popular':('recent_then_model','recent_then_popular'),
                 'recent_model_vs_popular':('recent_then_model','popular_7d'),
                 'recent_model_vs_model':('recent_then_model','model')}
    def paired(left,right):
        return paired_summary(left,right,seed=config.seed,resamples=config.bootstrap_resamples)
    comparisons = {key:paired(rows[left],rows[right]) for key,(left,right) in contrasts.items()}
    controls = {'users':len(controlled),'sample_users':len(sample),'excluded_without_history':len(sample)-len(controlled),
                'selection_seed':config.seed}
    controlled_truths = [truths[i] for i in controlled]
    for name,use_model in (('recent_then_popular',False),('recent_then_model',True)):
        normal = rows[name][controlled]
        shuffled_mean = np.zeros_like(normal)
        records = []
        for repeat in range(config.permutation_repeats):
            seed = config.seed+repeat
            order = derangement(len(controlled),seed)
            owners = controlled[order]
            ranked = np.asarray([compose_recent(histories[owner],popular,model=model_ranking[user] if use_model else None,k=k)
                                 for user,owner in zip(controlled,owners)])
            metrics = metric_rows(ranked,controlled_truths,inputs.article_ids)
            shuffled_mean += metrics/config.permutation_repeats
            records.append({'seed':seed,'unchanged_owner_rows':int(np.count_nonzero(controlled==owners)),
                'unchanged_history_rows':sum(np.array_equal(histories[a],histories[b]) for a,b in zip(controlled,owners)),
                'normal_minus_permuted':averages(normal-metrics),
                'comparison_with_normal_at_12':paired_rankings(sample_rankings[name],ranked)})
            progress(f'history_{name}_permutation_{repeat+1}')
        controls[name] = {'permutations':records,'normal_vs_mean_permuted':paired(normal,shuffled_mean)}
    return {'completed':True,'model_replay_verified':True,'replay':replay,'source_run_id':source['run_id'],
        'protocol':'recent-history-v1','config':config.model_dump(),
        'population':{'users':len(groups),'catalog_size':catalog_size,'with_recent_history':int(with_history.sum()),
                      'without_recent_history':int((~with_history).sum())},
        'history_summary':{'cutoff_exclusive':str(VALIDATION),'window_start_inclusive':str(VALIDATION-timedelta(days=config.history_days)),
            'mean_prefix_items':float(sizes.mean()),'users_with_prefix_filling_top12':int(np.count_nonzero(sizes>=min(12,k)))},
        'methods':methods,'paired':comparisons,'history_controls':controls,'quality_accepted':False,'test_evaluated':False}
