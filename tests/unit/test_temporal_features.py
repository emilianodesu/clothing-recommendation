from datetime import date

import polars as pl

from hm_recommender.data.ingest import ingest
from hm_recommender.data.synthetic import make_zip
from hm_recommender.features.build import build_features, build_examples


def read(output, manifest, name):
    return pl.read_parquet(output / manifest.files[name].path)


def test_future_purchase_does_not_change_past_features(tmp_path):
    results = []
    for suffix, future in [('base', False), ('future', True)]:
        dataset = ingest(make_zip(tmp_path / (suffix + '.zip'), future=future), tmp_path / suffix)
        output = tmp_path / (suffix + '-features')
        manifest = build_features(dataset, date(2020, 9, 9), output)
        results.append((output, manifest))
    for key in ('profiles', 'catalog', 'vocabulary'):
        a, b = [read(o, m, key) for o, m in results]
        assert a.equals(b), key
    profiles = read(*results[0], 'profiles')
    a = profiles.filter(pl.col('customer_id') == 'user-a').row(0, named=True)
    assert a['purchase_count'] == 3
    assert a['recency_days'] == 1
    assert abs(a['mean_price'] - 0.02) < 1e-12
    assert set(read(*results[0], 'catalog')['article_id']) == {'00a', '00b'}


def test_weekly_labels_are_binary_but_purchases_are_preserved(tmp_path):
    dataset = ingest(make_zip(tmp_path / 'source.zip'), tmp_path / 'data')
    result = build_examples(dataset, tmp_path / 'examples')
    frames = [pl.read_parquet(tmp_path / 'examples' / p['path']) for p in result['files']]
    rows = pl.concat(frames)
    assert rows['frequency'].sum() == 7
    repeated = rows.filter((pl.col('week_start') == pl.date(2020, 9, 2)) & (pl.col('article_id') == '00a'))
    assert repeated['frequency'].to_list() == [2]
    assert repeated['purchase_count'].to_list() == [1]  # excludes that week's purchases
    assert rows.filter(pl.col('split') == 'validation')['frequency'].sum() == 2
    assert rows.filter(pl.col('split') == 'test')['frequency'].sum() == 1
    unseen = rows.filter((pl.col('week_start') == pl.date(2020, 9, 9)) & (pl.col('article_id') == '00c'))
    assert unseen['in_catalog'].to_list() == [False]
    assert result['counts']['transactions'] == 7
    assert result['counts']['out_of_catalog_labels'] > 0


def test_frozen_training_vocabulary_keeps_new_test_items_oov(tmp_path):
    dataset = ingest(make_zip(tmp_path / 'source.zip'), tmp_path / 'data')
    train = build_features(dataset, date(2020, 9, 9), tmp_path / 'train')
    test = build_features(dataset, date(2020, 9, 16), tmp_path / 'test', vocabulary=tmp_path / 'train' / train.files['vocabulary'].path)
    catalog = read(tmp_path / 'test', test, 'catalog')
    assert catalog.filter(pl.col('article_id') == '00c')['item_index'].to_list() == [0]
