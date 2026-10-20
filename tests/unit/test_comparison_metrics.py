import numpy as np
import pytest
from hm_recommender.model.comparison import paired_summary, derangement, ComparisonConfig


def test_paired_bootstrap_preserves_pairing_and_known_constant_difference():
    baseline = np.array([[0., .2, .4], [.5, .3, .1], [.25, .6, .5]])
    result = paired_summary(baseline + .125, baseline, seed=42, resamples=200)
    for metric in result['metrics'].values():
        assert metric['delta'] == pytest.approx(.125)
        assert metric['ci95'] == pytest.approx([.125, .125])
    zero = paired_summary(baseline, baseline, seed=42, resamples=200)
    assert all(metric['ci95'] == [0., 0.] for metric in zero['metrics'].values())
    with pytest.raises(ValueError): paired_summary(baseline[:2], baseline, seed=42, resamples=200)


def test_user_resampling_is_deterministic_and_includes_uncertainty():
    baseline = np.zeros((4, 3))
    candidate = np.tile(np.array([-1., 0., 0., 1.])[:, None], (1, 3))
    result = paired_summary(candidate, baseline, seed=42, resamples=200)
    assert result == paired_summary(candidate, baseline, seed=42, resamples=200)
    assert result['metrics']['map_at_12']['ci95'][0] < 0 < result['metrics']['map_at_12']['ci95'][1]
    order = derangement(17, 42)
    assert sorted(order) == list(range(17)) and np.all(order != np.arange(17))
    assert np.array_equal(order, derangement(17, 42))
    with pytest.raises(ValueError): derangement(1, 42)
    with pytest.raises(ValueError): ComparisonConfig(sample_users=5001)
