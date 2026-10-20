import json
from pathlib import Path

import numpy as np
import pytest

from hm_recommender.model.demo import prepare_demo_dataset
from hm_recommender.quality.contracts import FoldSpec, QualityConfig
from hm_recommender.quality.temporal import build_snapshot, read_labels, frame
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.evaluate import evaluate_ranking
from hm_recommender.graph.build import build_graph
from hm_recommender.graph.retrieve import rank_graph


@pytest.mark.parametrize('pool', ['H', 'G'])
def test_ranker_four_weeks_refit_save_load_and_fixed_pool(tmp_path, pool):
    from hm_recommender.reranking.examples import build_ranking_examples
    from hm_recommender.reranking.train import fit_ranker, load_model
    from hm_recommender.reranking.predict import rerank
    dataset = prepare_demo_dataset(tmp_path / 'demo')
    config, fold = QualityConfig(), FoldSpec.evaluation('V3')
    table = build_ranking_examples(dataset, fold, pool, config, tmp_path / 'table')
    assert [w['cutoff'] for w in table.weeks] == ['2020-08-12', '2020-08-19', '2020-08-26', '2020-09-02']
    assert all(w['label_end'] <= str(fold.cutoff) for w in table.weeks)
    assert table.statistics['groups_total'] == 128
    assert table.statistics['groups_trainable'] == 128
    assert table.statistics['sampled_rows'] == 128 * 22
    for week in table.weeks:
        groups = frame(table, week['groups_file'])
        assert groups['sampled_rows'].sum() == table.files[week['sample_file']].rows
    model = fit_ranker(table, config, tmp_path / 'model')
    assert model.statistics['early_stopping_cutoff'] == '2020-09-02'
    assert model.statistics['inner_training_weeks'] == 3
    assert model.statistics['refit_training_weeks'] == 4
    assert model.statistics['refit_groups'] == 128
    assert 1 <= model.best_iteration <= 300
    assert model.statistics['refit_requested_iterations'] == model.best_iteration
    snapshot = build_snapshot(dataset, fold, tmp_path / 'snapshot', config)
    graph = build_graph(dataset, snapshot, config, tmp_path / 'graph')
    labels = read_labels(dataset, fold)
    buyers = labels.select('customer_id').unique().sort('customer_id').collect()['customer_id'].to_list()
    base = (rank_baseline(snapshot, buyers, 'H', tmp_path / 'base') if pool == 'H' else
            rank_graph(snapshot, graph, buyers, tmp_path / 'base'))
    ranked = rerank(model, base, snapshot, graph, dataset, tmp_path / 'ranked')
    base_rows, new_rows = frame(base, 'ranking'), frame(ranked, 'ranking')
    keys = ['customer_id', 'article_id']
    assert base_rows.select(keys).sort(keys).equals(new_rows.select(keys).sort(keys))
    ties = 0
    for group in new_rows.partition_by('customer_id', maintain_order=True):
        items, scores = group['article_id'].to_list(), group['score'].to_list()
        for i in range(1, len(items)):
            if scores[i] == scores[i-1]:
                ties += 1
                assert items[i-1] < items[i]
    assert ties > 0
    before = evaluate_ranking(base, labels, snapshot, tmp_path / 'before')
    after = evaluate_ranking(ranked, labels, snapshot, tmp_path / 'after')
    assert after.method == 'R' + pool and before.metrics['recall_at_200'] == after.metrics['recall_at_200']
    loaded = load_model(tmp_path / 'model')
    repeated = rerank(loaded, base, snapshot, graph, dataset, tmp_path / 'repeat')
    assert frame(repeated, 'ranking').equals(new_rows)
    assert not model.statistics['outer_labels_used']
    manifest = json.loads((tmp_path / 'model/manifest.json').read_text())
    manifest['feature_names'] = manifest['feature_names'][::-1]
    (tmp_path / 'model/manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='schema'):
        load_model(tmp_path / 'model')


def test_zero_positive_groups_counted_and_kept_in_inner_evaluation(tmp_path):
    from hm_recommender.reranking.examples import write_week_examples
    from hm_recommender.reranking.features import FeatureContext
    import polars as pl
    dataset = prepare_demo_dataset(tmp_path / 'demo')
    snapshot = build_snapshot(dataset, FoldSpec.training('V3', 1), tmp_path / 'snapshot', QualityConfig())
    graph = build_graph(dataset, snapshot, QualityConfig(), tmp_path / 'graph')
    buyers = ['demo-000', 'never-seen']
    ranking = rank_baseline(snapshot, buyers, 'H', tmp_path / 'ranking')
    labels = pl.DataFrame({'customer_id': ['demo-000', 'demo-000', 'never-seen'],
                          'article_id': ['0000000000', 'outside', 'outside']})
    week = write_week_examples(FeatureContext(dataset, snapshot, graph, buyers), ranking, labels,
                              tmp_path / 'week', keep_full=True)
    assert week['counts']['groups_total'] == 2 and week['counts']['groups_without_positives'] == 1
    assert week['counts']['positives_not_recovered'] == 2
    assert week['counts']['sampled_rows'] == 21
    full = pl.read_parquet(tmp_path / 'week/full.parquet')
    assert full.height == 48 and full['customer_id'].n_unique() == 2
    groups = pl.read_parquet(tmp_path / 'week/groups.parquet')
    assert groups['relevant_items'].to_list() == [2, 1]
    assert not full.filter(pl.col('article_id') == 'outside').height


def test_outer_fold_not_used_for_early_stopping_or_refit(tmp_path):
    from datetime import date
    import polars as pl
    from hm_recommender.data.ingest import scan, write_partition
    from hm_recommender.data.io import atomic_json
    from hm_recommender.reranking.examples import build_ranking_examples
    from hm_recommender.reranking.train import fit_ranker
    outputs = []
    for altered in (False, True):
        root = tmp_path / str(altered)
        dataset = prepare_demo_dataset(root / 'demo')
        if altered:
            tx = scan(dataset).collect().with_columns(
                pl.when(pl.col('t_dat') >= date(2020, 9, 9)).then(pl.lit('0000000019'))
                .otherwise(pl.col('article_id')).alias('article_id'))
            replacement = write_partition(tx.to_arrow(), dataset._root, 'transactions/changed.parquet')
            dataset.partitions = [p for p in dataset.partitions if not p.path.startswith('transactions/')] + [replacement]
            atomic_json(dataset._root / 'manifest.json', dataset.model_dump(mode='json'))
        table = build_ranking_examples(dataset, FoldSpec.evaluation('V3'), 'H', QualityConfig(), root / 'table')
        model = fit_ranker(table, QualityConfig(), root / 'model')
        outputs.append((table, model))
    a, b = outputs
    assert {k: f.sha256 for k, f in a[0].files.items()} == {k: f.sha256 for k, f in b[0].files.items()}
    assert a[1].best_iteration == b[1].best_iteration
    assert a[1].files['model'].sha256 == b[1].files['model'].sha256
