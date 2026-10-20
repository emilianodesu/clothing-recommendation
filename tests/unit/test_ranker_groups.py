import numpy as np
import polars as pl
import pytest


def test_missing_positive_not_injected_and_sampling_deterministic():
    from hm_recommender.reranking.examples import sample_group
    articles = [f'{i:03}' for i in range(40)]
    labels = np.array([int(a in {'003', '020'}) for a in articles])
    selected = sample_group('2020-08-12', 'buyer', articles, labels)
    assert len(selected) == 22 and labels[selected].sum() == 2
    assert set(articles[i] for i in selected) <= set(articles)
    assert all(a != 'outside' for a in (articles[i] for i in selected))
    assert np.array_equal(selected, sample_group('2020-08-12', 'buyer', articles, labels))
    reverse = sample_group('2020-08-12', 'buyer', articles[::-1], labels[::-1])
    assert {articles[i] for i in selected} == {articles[::-1][i] for i in reverse}
    assert len(sample_group('2020-08-12', 'u', articles, np.zeros(40))) == 0
    assert len(sample_group('2020-08-12', 'u', articles, np.ones(40))) == 0


def test_inner_ndcg_keeps_missing_relevant_denominator_and_zero_groups():
    from hm_recommender.reranking.train import project_ndcg_evaluator
    # First buyer retrieved one of FOUR relevant items; second retrieved none.
    labels = np.array([1, 0, 0, 0, 0, 0], dtype=float)
    evaluate = project_ndcg_evaluator(labels, np.array([3, 3]), np.array([4, 1]))
    name, score, higher = evaluate(np.array([1., 1., 0., 3., 2., 1.]), None)
    assert name == 'project_ndcg_at_12' and higher
    assert score == pytest.approx((1 / (1 + 1 / np.log2(3) + 1 / 2 + 1 / np.log2(5))) / 2)
    # Ties follow the already lexicographically sorted article IDs, never labels.
    assert evaluate(np.zeros(6), None)[1] == pytest.approx(score)


def test_ndcg_rejects_incomplete_groups_and_nonfinite_scores():
    from hm_recommender.reranking.train import project_ndcg_evaluator
    with pytest.raises(ValueError):
        project_ndcg_evaluator(np.zeros(6), np.array([2, 3]), np.array([1, 2]))
    evaluate = project_ndcg_evaluator(np.array([1, 0]), np.array([2]), np.array([1]))
    with pytest.raises(ValueError): evaluate(np.array([np.nan, 1]), None)
