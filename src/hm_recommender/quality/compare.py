"""Customer-cluster resampling of three temporal folds with equal fold weights."""
import numpy as np

from hm_recommender.quality.selection import FOLDS, CANDIDATES, METRICS, select_candidate


def cluster_bootstrap(folds, candidates, *, resamples=2000, seed=42):
    if (len(folds) != 3 or {f[0] for f in folds} != set(FOLDS) or not candidates
            or len(set(candidates)) != len(candidates) or not set(candidates) <= set(CANDIDATES)
            or resamples != 2000 or seed != 42):
        raise ValueError('Fixed bootstrap protocol and three folds required')
    folds = sorted(folds, key=lambda f: FOLDS.index(f[0]))
    columns = len(candidates)*3
    buyers = sorted({customer for _, ids, _ in folds for customer in ids})
    index = {customer: i for i, customer in enumerate(buyers)}
    if not buyers: raise ValueError('Empty cohort')
    n = len(buyers); matrix = np.zeros((n, 3*(1+columns)), dtype=np.float64)
    means = []
    for f, (_, ids, values) in enumerate(folds):
        values = np.asarray(values, dtype=np.float64)
        if not ids or len(set(ids)) != len(ids) or values.shape != (len(ids), columns) or not np.isfinite(values).all():
            raise ValueError('Invalid paired differences or duplicate buyers')
        positions = [index[c] for c in ids]; start = f*(1+columns)
        matrix[positions, start] = 1
        matrix[positions, start+1:start+1+columns] = values
        means.append(values.mean(axis=0))
    rng = np.random.default_rng(seed); accepted = []; discarded = 0; attempted = 0
    while len(accepted) < resamples:
        batch = min(16, resamples-len(accepted))
        counts = np.empty((batch, n), dtype=np.float64)
        for row in range(batch):
            counts[row] = np.bincount(rng.integers(0, n, size=n), minlength=n)
        totals = (counts @ matrix).reshape(batch, 3, 1+columns)
        for row in totals:
            attempted += 1
            if (row[:, 0] == 0).any(): discarded += 1
            else: accepted.append((row[:, 1:]/row[:, :1]).mean(axis=0))
        if attempted > resamples*100:
            raise ValueError('Too many empty-fold bootstrap draws')
    draws = np.asarray(accepted); point = np.mean(means, axis=0); contrasts = {}
    for c, candidate in enumerate(candidates):
        contrasts[candidate] = {}
        for m, metric in enumerate(METRICS):
            position = c*3+m
            item = {'delta': float(point[position]), 'exploratory_interval': np.quantile(draws[:,position], [.025,.975]).tolist()}
            if metric == 'map_at_12':
                item['family_interval'] = np.quantile(draws[:,position], [.05/6, 1-.05/6]).tolist()
            contrasts[candidate][metric] = item
    return {'resamples':resamples, 'seed':seed, 'cluster':'customer', 'fold_weighting':'equal',
        'family_contrasts':3, 'family_confidence':1-.05/3, 'exploratory_confidence':.95,
        'unique_buyers':n, 'observations':sum(len(f[1]) for f in folds),
        'fold_buyers':{f[0]:len(f[1]) for f in folds}, 'discarded_empty_fold_draws':discarded,
        'contrasts':contrasts}, draws


def compare_campaign(evaluations, config, output_dir, *, size='full'):
    from pathlib import Path
    import time
    import polars as pl
    from hm_recommender.data.io import atomic_json, sha256
    from hm_recommender.quality.campaign import verified_campaign
    from hm_recommender.quality.contracts import QualityConfig, digest
    if config.model_dump()!=QualityConfig().model_dump(): raise ValueError('Fixed protocol required')
    started=time.monotonic()
    tables,methods,identities,sources,source=verified_campaign(evaluations,size,config)
    verification_seconds=time.monotonic()-started
    complete=[m for m in CANDIDATES if all(m in tables[f] for f in FOLDS)]
    started=time.monotonic();bootstrap=None;bootstrap_artifact=None
    root=Path(output_dir);root.mkdir(parents=True,exist_ok=True)
    if complete:
        paired=[]
        for fold in FOLDS:
            baseline=tables[fold]['H'].select(*METRICS).to_numpy()
            differences=np.concatenate([tables[fold][m].select(*METRICS).to_numpy()-baseline for m in complete],axis=1)
            paired.append((fold,tables[fold]['H']['customer_id'].to_list(),differences))
        bootstrap,draws=cluster_bootstrap(paired,complete)
        path=root/'bootstrap.parquet'
        pl.DataFrame(draws,schema=[m+'_'+metric for m in complete for metric in METRICS]).write_parquet(path)
        bootstrap_artifact={'sha256':sha256(path),'rows':len(draws),'bytes':path.stat().st_size}
    decision=select_candidate(methods,bootstrap['contrasts'] if bootstrap else {})
    selected=decision['selected_method'];comparison_complete=len(complete)==3
    result={'inputs':{'protocol_sha256':digest(config.model_dump()),'source_sha256':source[0],
            'dataset_manifest_sha256':source[1],'source_reports_sha256':digest(sources)},
        'scope':size,'sources':sources,'evaluations':identities,'methods':methods,
        'bootstrap':bootstrap,'bootstrap_artifact':bootstrap_artifact,'decision':decision,
        'comparison_complete':comparison_complete,'selection_finalized':size=='full' and comparison_complete,
        'test_evaluated':False,'quality_accepted':False,
        'procedure':{'method':selected,'protocol':'quality-v1','config_sha256':digest(config.model_dump()),
            'cutoff_parameterized':True,'training_weeks':4 if selected in ('RH','RG') else 0,
            'refit_rule':'three_weeks_then_fourth_early_stopping_then_four_week_refit' if selected in ('RH','RG') else 'past_only_state',
            'test_policy':'T8_once_selected_and_P_H_no_retuning'},
        'costs':{'verification_seconds':verification_seconds,'bootstrap_and_selection_seconds':time.monotonic()-started},
        'cost_scope':{'P_H':'not_separately_measured_not_used_in_tie_break','G_RH_RG':'preparation_fit_ranking_excludes_controls_and_evaluation',
            'memory':'source_job_peak_upper_bound_including_controls'}}
    atomic_json(root/'decision.json',result)
    return result
