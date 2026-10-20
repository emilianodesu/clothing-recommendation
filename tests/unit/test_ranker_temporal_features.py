from datetime import date

import numpy as np
import polars as pl
import pytest

from test_graph_weights import graph_fixture
from hm_recommender.graph.build import build_graph
from hm_recommender.quality.contracts import QualityConfig, FoldSpec
from hm_recommender.quality.temporal import build_snapshot


def feature_fixture(root, future=False):
    from hm_recommender.reranking.features import FeatureContext
    root.mkdir()
    rows = [('2020-09-08', 'u', '000')] * 3 + [('2020-09-08', 'u', '001')]
    rows += [('2020-08-01', 'u', '002')]
    if future:
        rows += [('2020-09-09', 'u', '003')] * 50
    dataset = graph_fixture(root / 'ds', rows, n=210)
    snapshot = build_snapshot(dataset, FoldSpec.evaluation('V3'), root / 'snapshot', QualityConfig())
    graph = build_graph(dataset, snapshot, QualityConfig(), root / 'graph')
    return FeatureContext(dataset, snapshot, graph, ['new', 'u'])


def test_exact_feature_values_and_missing_sentinels(tmp_path):
    from hm_recommender.reranking.features import FEATURE_NAMES
    context = feature_fixture(tmp_path / 'base')
    pairs = pl.DataFrame({'customer_id': ['u', 'new'], 'article_id': ['000', '209']})
    result = context.transform(pairs)
    assert result.columns == ['customer_id', 'article_id', *FEATURE_NAMES]
    assert len(FEATURE_NAMES) == 20
    a = result.row(0, named=True)
    assert [a[k] for k in FEATURE_NAMES[:10]] == pytest.approx([3, 3, 1, 4, 5, 1, 3, 3, 3, 3 / 1.75])
    assert [a[k] for k in FEATURE_NAMES[10:13]] == [1, 1, 1]
    # Only seed 001 is adjacent to 000; 002 was purchased in a separate basket.
    weights = [np.log1p(3) * 2 ** (-1 / 14), np.log1p(1) * 2 ** (-1 / 14), np.log1p(1) * 2 ** (-39 / 14)]
    assert a['graph_score'] == pytest.approx(weights[1] / sum(weights))
    assert a['history_rank'] == 1 and a['popular_rank'] == 1
    assert a['in_history'] == a['in_neighbors'] == a['in_popular'] == 1
    b = result.row(1, named=True)
    assert b['pair_recency90'] == b['user_recency90'] == 91
    assert b['history_rank'] == b['neighbor_rank'] == b['popular_rank'] == 201
    assert all(b[k] == 0 for k in FEATURE_NAMES if k not in
               ('pair_recency90', 'user_recency90', 'history_rank', 'neighbor_rank', 'popular_rank'))


def test_future_label_never_enters_features(tmp_path):
    pairs = pl.DataFrame({'customer_id': ['u', 'u', 'new'], 'article_id': ['000', '003', '001']})
    a = feature_fixture(tmp_path / 'before').transform(pairs)
    b = feature_fixture(tmp_path / 'after', future=True).transform(pairs)
    assert a.equals(b)


def test_weekly_cutoff_and_graph_identity_are_enforced(tmp_path):
    from hm_recommender.reranking.features import FeatureContext
    dataset = graph_fixture(tmp_path / 'ds', [('2020-08-20', 'u', '000')])
    config = QualityConfig()
    past = build_snapshot(dataset, FoldSpec.training('V3', 4), tmp_path / 'past', config)
    outer = build_snapshot(dataset, FoldSpec.evaluation('V3'), tmp_path / 'outer', config)
    graph = build_graph(dataset, outer, config, tmp_path / 'graph')
    with pytest.raises(ValueError, match='snapshot'):
        FeatureContext(dataset, past, graph, ['u'])


def test_missing_content_uses_consistent_category(tmp_path):
    from hm_recommender.reranking.features import FeatureContext
    from hm_recommender.data.ingest import write_partition
    from hm_recommender.data.io import atomic_json
    dataset = graph_fixture(tmp_path / 'ds', [('2020-09-08', 'u', '000')], n=2)
    articles = pl.DataFrame({'article_id':['000','001'], 'product_type_name':[None,None],
        'product_group_name':['g',None], 'colour_group_name':['x','y']},
        schema_overrides={'product_type_name':pl.String})
    dataset.partitions[-1] = write_partition(articles.to_arrow(), dataset._root, 'articles/missing.parquet')
    atomic_json(dataset._root / 'manifest.json', dataset.model_dump(mode='json'))
    snap = build_snapshot(dataset, FoldSpec.evaluation('V3'), tmp_path / 'snap', QualityConfig())
    graph = build_graph(dataset, snap, QualityConfig(), tmp_path / 'graph')
    row = FeatureContext(dataset, snap, graph, ['u']).transform(
        pl.DataFrame({'customer_id':['u'], 'article_id':['001']})).row(0, named=True)
    assert row['affinity_type90'] == 1 and row['affinity_group90'] == row['affinity_color90'] == 0
