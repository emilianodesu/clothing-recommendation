from datetime import date
import numpy as np
import polars as pl
import pytest
from hm_recommender.model.diagnostics import exposure, paired_rankings, recent_popular, assert_replay


def test_exposure_distinguishes_identical_lists_from_personalized_lists():
    result = exposure(np.array([[0, 1], [0, 1], [1, 0]]), 4, k=2)
    assert result['distinct_items'] == 2 and result['coverage'] == .5
    assert result['unique_ordered_lists'] == 2 and result['unique_sets'] == 1
    assert result['top_exposure_share']['1'] == .5
    assert result['effective_items'] == pytest.approx(2)
    pairs = paired_rankings(np.array([[0, 1], [0, 1]]), np.array([[0, 1], [2, 3]]), k=2)
    assert pairs == {'mean_overlap': .5, 'mean_jaccard': .5, 'identical_order_fraction': .5}


def test_recent_popularity_excludes_cutoff_and_uses_inclusive_window_start():
    transactions = pl.DataFrame({'article_id': ['a', 'a', 'b', 'b', 'c', 'c', 'd'],
        't_dat': [date(2020, 9, 1), date(2020, 9, 2), date(2020, 9, 2), date(2020, 9, 8),
                  date(2020, 9, 9), date(2020, 9, 10), date(2020, 9, 8)]}).lazy()
    catalog = pl.DataFrame({'article_id': ['a', 'b', 'c']})
    assert recent_popular(transactions, catalog, 7).tolist() == [1, 0, 2]


def test_replay_rejects_changed_scores_counts_and_test_access():
    expected = {'map_at_12': .01, 'evaluated_users': 33, 'test_evaluated': False, 'segments': {'new_user': {'users': 1}}}
    assert_replay(expected, dict(expected))
    for changed in ({'map_at_12': .02}, {'evaluated_users': 34}, {'test_evaluated': True}, {'segments': {}}):
        with pytest.raises(ValueError, match='replay'):
            assert_replay(expected, dict(expected, **changed))
