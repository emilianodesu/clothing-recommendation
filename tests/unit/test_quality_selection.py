from copy import deepcopy
import importlib

import numpy as np
import pytest


def implementation():
    # Missing Q4 is an explicit assertion failure while defining this contract.
    assert importlib.util.find_spec('hm_recommender.quality.compare'), 'Q4 comparison is not implemented'
    return importlib.import_module('hm_recommender.quality.compare')


def test_cluster_bootstrap_keeps_buyers_together_and_equal_fold_weights():
    module = implementation()
    # A appears in all folds; B/C do not. Resampling rows or averaging all rows
    # changes this answer. Independent reference expands sampled buyer records.
    folds = [('V1', ['A', 'B'], np.array([[.2, .4, 0], [-.1, .1, 0]])),
             ('V2', ['A', 'C', 'B'], np.array([[.1, .2, 0], [.3, .3, 0], [0, .1, 0]])),
             ('V3', ['A', 'C'], np.array([[.3, .5, 0], [.2, .2, 0]]))]
    result, draws = module.cluster_bootstrap(folds, ['RH'], resamples=2000, seed=42)
    rng = np.random.default_rng(42)
    reference = []
    rejected = 0
    while len(reference) < 2000:
        buyers = rng.choice(['A', 'B', 'C'], size=3, replace=True)
        week_values = []
        for _, ids, values in folds:
            sample = [values[ids.index(c)] for c in buyers if c in ids]
            if not sample:
                break
            week_values.append(np.mean(sample, axis=0))
        if len(week_values) == 3:
            reference.append(np.mean(week_values, axis=0))
        else:
            rejected += 1
    np.testing.assert_allclose(draws, reference, rtol=0, atol=1e-15)
    contrast = result['contrasts']['RH']['map_at_12']
    assert contrast['delta'] == pytest.approx(13/90)
    np.testing.assert_allclose(contrast['family_interval'], np.quantile(np.array(reference)[:, 0], [.05/6, 1-.05/6]))
    assert result['unique_buyers'] == 3 and result['observations'] == 7
    assert result['discarded_empty_fold_draws'] == rejected
    assert result['family_confidence'] == pytest.approx(1-.05/3)
    assert draws.shape == (2000, 3)


def selection_fixture():
    folds = {f: {'metrics': {'map_at_12': .1, 'ndcg_at_12': .2, 'recall_at_200': .4},
                 'segments': {'no_history': {'users': 1000, 'metrics': {'map_at_12': .1}},
                              'recent28d': {'users': 999, 'metrics': {'map_at_12': .1}}}}
             for f in ('V1', 'V2', 'V3')}
    methods = {'H': {'folds': folds, 'cost_seconds': 0, 'peak_memory_bytes': 100}}
    intervals = {}
    for name in ('G', 'RH', 'RG'):
        methods[name] = {'folds': deepcopy(folds), 'cost_seconds': 10, 'peak_memory_bytes': 100}
        for fold in methods[name]['folds'].values():
            fold['metrics']['map_at_12'] = .102
        intervals[name] = {'map_at_12': {'family_interval': [.001, .003]}}
    return methods, intervals


def test_threshold_boundaries_segments_zero_reference_and_incomplete_candidates():
    module = implementation(); methods, intervals = selection_fixture()
    # Exactly +2%, -1% auxiliary and -5% segment losses are allowed.
    for f in methods['RH']['folds'].values():
        f['metrics'].update(ndcg_at_12=.198, recall_at_200=.396)
        f['segments']['no_history']['metrics']['map_at_12'] = .095
        f['segments']['recent28d']['metrics']['map_at_12'] = 0
    assert module.select_candidate(methods, intervals)['candidates']['RH']['eligible']
    methods['RH']['folds']['V1']['segments']['no_history']['metrics']['map_at_12'] = .094999
    assert 'segment_regression' in module.select_candidate(methods, intervals)['candidates']['RH']['reasons']
    del methods['G']['folds']['V3']
    assert not module.select_candidate(methods, intervals)['candidates']['G']['eligible']
    intervals['RG']['map_at_12']['family_interval'][0] = 0
    result = module.select_candidate(methods, intervals)
    assert result['selected_method'] == 'H'
    assert 'map_interval_not_positive' in result['candidates']['RG']['reasons']
    # Zero baseline has no relative division; nonnegative segment delta passes.
    for f in methods['H']['folds'].values():
        f['segments']['no_history']['metrics']['map_at_12'] = 0
    intervals['RG']['map_at_12']['family_interval'][0] = .001
    assert module.select_candidate(methods, intervals)['candidates']['RG']['eligible']


def test_fold_regression_and_positive_fold_count_block_promotion():
    module = implementation(); methods, intervals = selection_fixture()
    for name in ('G', 'RH', 'RG'):
        for fold, value in zip(methods[name]['folds'].values(), [.15, .1, .098]):
            fold['metrics']['map_at_12'] = value
    out = module.select_candidate(methods, intervals)
    assert out['selected_method'] == 'H'
    assert 'too_few_positive_folds' in out['candidates']['RH']['reasons']
    methods['RH']['folds']['V2']['metrics']['map_at_12'] = .101
    assert module.select_candidate(methods, intervals)['candidates']['RH']['eligible']
    methods['RH']['folds']['V3']['metrics']['map_at_12'] = .097999
    assert 'fold_map_regression' in module.select_candidate(methods, intervals)['candidates']['RH']['reasons']


def test_tie_break_uses_total_cost_then_memory_then_stable_order():
    module = implementation(); methods, intervals = selection_fixture()
    for f in methods['RG']['folds'].values(): f['metrics']['map_at_12'] = .1021
    methods['G']['cost_seconds'] = 9
    assert module.select_candidate(methods, intervals)['selected_method'] == 'G'
    methods['RH']['cost_seconds'] = 9; methods['RH']['peak_memory_bytes'] = 90
    assert module.select_candidate(methods, intervals)['selected_method'] == 'RH'
    methods['G']['peak_memory_bytes'] = 90
    assert module.select_candidate(methods, intervals)['selected_method'] == 'G'
    for f in methods['RG']['folds'].values(): f['metrics']['map_at_12'] = .102101
    assert module.select_candidate(methods, intervals)['selected_method'] == 'RG'


def test_unknown_fold_or_nonfinite_bootstrap_input_is_rejected():
    module = implementation()
    with pytest.raises(ValueError):
        module.cluster_bootstrap([('test', ['A'], np.array([[.1, .2, .3]]))], ['G'])
    with pytest.raises(ValueError):
        module.cluster_bootstrap([(f, ['A'], np.array([[np.nan, .2, .3]])) for f in ('V1','V2','V3')], ['G'])
