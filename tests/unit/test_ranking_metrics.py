import numpy as np
import pytest
from hm_recommender.model.evaluate import ranking_metrics, topk


def test_known_ranking_and_unavailable_relevant_denominator():
    metrics = ranking_metrics(['a', 'x', 'b'], {'a', 'b', 'unavailable'})
    assert metrics['map_at_12'] == pytest.approx((1 + 2 / 3) / 3)
    assert metrics['recall_at_200'] == pytest.approx(2 / 3)
    assert metrics['ndcg_at_12'] == pytest.approx(1.5 / (1 + 1 / np.log2(3) + .5))


def test_topk_ties_have_catalog_order_and_small_catalog():
    assert topk(np.array([[1., 2., 2., 0., 2.]]), 2).tolist() == [[1, 2]]
    assert topk(np.ones((1, 3)), 200).tolist() == [[0, 1, 2]]
