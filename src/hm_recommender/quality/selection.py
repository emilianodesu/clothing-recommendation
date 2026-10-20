"""Pure-Python frozen selection rule, shared by science and report validation."""
import math

FOLDS = ('V1', 'V2', 'V3')
CANDIDATES = ('G', 'RH', 'RG')
METRICS = ('map_at_12', 'ndcg_at_12', 'recall_at_200')


def mean_metrics(method):
    return {metric: sum(method['folds'][f]['metrics'][metric] for f in FOLDS)/3 for metric in METRICS}


def at_least(value, boundary):
    return value >= boundary or math.isclose(value, boundary, rel_tol=0, abs_tol=1e-12)


def select_candidate(methods, intervals):
    if 'H' not in methods or set(methods['H']['folds']) != set(FOLDS):
        raise ValueError('Complete H baseline is required')
    base = mean_metrics(methods['H']); candidates = {}
    for name in CANDIDATES:
        item = methods.get(name); reasons = []
        complete = item is not None and set(item['folds']) == set(FOLDS)
        if not complete:
            candidates[name] = {'complete': False, 'eligible': False, 'reasons': ['incomplete_candidate'], 'mean_metrics': None}
            continue
        means = mean_metrics(item)
        if not (at_least(means['map_at_12'], base['map_at_12']*1.02) if base['map_at_12'] else means['map_at_12'] > 0):
            reasons.append('insufficient_mean_map_gain')
        positive = sum(item['folds'][f]['metrics']['map_at_12'] > methods['H']['folds'][f]['metrics']['map_at_12'] for f in FOLDS)
        if positive < 2: reasons.append('too_few_positive_folds')
        if any(not at_least(item['folds'][f]['metrics']['map_at_12'], methods['H']['folds'][f]['metrics']['map_at_12']*.98) for f in FOLDS):
            reasons.append('fold_map_regression')
        interval = intervals.get(name, {}).get('map_at_12', {}).get('family_interval')
        if interval is None or interval[0] <= 0: reasons.append('map_interval_not_positive')
        for metric in ('ndcg_at_12', 'recall_at_200'):
            if not at_least(means[metric], base[metric]*.99): reasons.append(metric + '_regression')
        for f in FOLDS:
            for segment, original in methods['H']['folds'][f]['segments'].items():
                if original['users'] >= 1000:
                    candidate = item['folds'][f]['segments'][segment]
                    if not at_least(candidate['metrics']['map_at_12'], original['metrics']['map_at_12']*.95):
                        if 'segment_regression' not in reasons: reasons.append('segment_regression')
        candidates[name] = {'complete': True, 'eligible': not reasons, 'reasons': reasons,
            'mean_metrics': means, 'positive_folds': positive}
    eligible = [name for name in CANDIDATES if candidates[name]['eligible']]
    winner = 'H'; tied = []
    if eligible:
        best = max(candidates[name]['mean_metrics']['map_at_12'] for name in eligible)
        tied = [name for name in eligible if at_least(candidates[name]['mean_metrics']['map_at_12'], best-.0001)]
        winner = min(tied, key=lambda name: (methods[name]['cost_seconds'], methods[name]['peak_memory_bytes'], CANDIDATES.index(name)))
    return {'selected_method': winner, 'baseline': 'H', 'candidates': candidates,
            'tie_candidates': tied, 'reason': 'eligible_candidate' if eligible else 'no_eligible_candidate'}
