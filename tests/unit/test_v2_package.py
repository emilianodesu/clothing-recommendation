from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import polars as pl
from pydantic import ValidationError
import pytest
from scipy import sparse

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.v2.features import V2_FEATURE_NAMES, V2FeatureContext
from hm_recommender.v2.latent import LatentContentIndex
from hm_recommender.v2.package import (
    EXPECTED_FILENAMES,
    V2ReleaseManifest,
    build_v2_release,
    publish_v2_release,
    v2_release_identity,
    verify_v2_release,
)
from hm_recommender.v2.ranker import RankerConfig, load_booster, score_candidates
from hm_recommender.v2.retrieval import MultiCandidateRetriever, RetrievalConfig


def _build_synthetic_dataset(
    root: Path,
    *,
    tx_rows: list[tuple[str, str, str, float, int]] | None = None,
    min_date: date = date(2020, 6, 1),
    max_date: date = date(2020, 9, 22),
) -> DatasetManifest:
    root.mkdir(parents=True, exist_ok=True)
    items = [f'a{i:03d}' for i in range(1, 13)]
    articles_df = pl.DataFrame({
        'article_id': items,
        'product_code': [f'pc{(i - 1) // 2:02d}' for i in range(1, 13)],
        'prod_name': [f'Item {i}' for i in range(1, 13)],
        'product_type_name': ['Trousers' if i % 2 else 'Top' for i in range(1, 13)],
        'product_group_name': ['Garment Lower body' if i % 2 else 'Garment Upper body' for i in range(1, 13)],
        'colour_group_name': ['Black' if i % 3 == 0 else 'White' for i in range(1, 13)],
        'graphical_appearance_name': ['Solid'] * 12,
        'index_group_name': ['Ladieswear' if i <= 6 else 'Menswear' for i in range(1, 13)],
        'department_no': ['100'] * 12,
        'garment_group_name': ['Jersey'] * 12,
        'detail_desc': [f'Soft fabric style {i}' for i in range(1, 13)],
    })
    customers_df = pl.DataFrame({
        'customer_id': ['u1', 'u2', 'u3', 'u4', 'u_cold'],
        'age': ['25', '34', '46', '57', '22'],
        'club_member_status': ['ACTIVE'] * 5,
        'fashion_news_frequency': ['Regularly', 'Monthly', 'NONE', 'Regularly', 'NONE'],
        'FN': ['1.0', None, None, '1.0', None],
        'Active': ['1.0', '1.0', None, '1.0', None],
    })
    if tx_rows is None:
        rows: list[tuple[str, str, str, float, int]] = []
        base = date(2020, 8, 1)
        for w in range(8):
            d1 = (base + timedelta(days=7 * w)).isoformat()
            d2 = (base + timedelta(days=7 * w + 2)).isoformat()
            rows.extend([
                (d1, 'u1', 'a001', 0.02, 2),
                (d2, 'u1', 'a002', 0.02, 2),
                (d1, 'u2', 'a003', 0.03, 1),
                (d2, 'u2', 'a004', 0.03, 2),
                (d1, 'u3', 'a007', 0.04, 2),
                (d2, 'u3', 'a008', 0.04, 2),
                (d1, 'u4', 'a009', 0.05, 1),
                (d2, 'u4', 'a010', 0.05, 1),
            ])
        rows.append(('2020-09-22', 'u1', 'a012', 0.06, 2))
        tx_rows = rows

    tx_df = (
        pl.DataFrame(
            tx_rows,
            schema=['t_dat', 'customer_id', 'article_id', 'price', 'sales_channel_id'],
            orient='row',
        )
        .with_columns(
            pl.col('t_dat').str.to_date(),
            pl.col('price').cast(pl.Float64),
            pl.col('sales_channel_id').cast(pl.Int8),
        )
    )
    partitions = [
        write_partition(tx_df.to_arrow(), root, 'transactions/part.parquet'),
        write_partition(articles_df.to_arrow(), root, 'articles/part.parquet'),
        write_partition(customers_df.to_arrow(), root, 'customers/part.parquet'),
    ]
    ds = DatasetManifest(
        dataset_id='pkg-fixture',
        source_sha256='d' * 64,
        min_date=min_date,
        max_date=max_date,
        counts={'transactions': tx_df.height, 'articles': articles_df.height, 'customers': customers_df.height},
        partitions=partitions,
    )
    ds._root = root
    atomic_json(root / 'manifest.json', ds.model_dump(mode='json'))
    return ds


def _fast_configs() -> tuple[RetrievalConfig, RankerConfig]:
    r_cfg = RetrievalConfig(
        history_limit=6, variant_limit=4, cooc_limit=4, latent_dim=4,
        latent_limit=4, content_limit=4, segment_pop_limit=6, global_pop_limit=6,
    )
    rk_cfg = RankerConfig(
        weeks=4, val_weeks=1, negatives_per_group=4, hard_negatives_per_group=2,
        num_boost_round=4, early_stopping_rounds=2, num_leaves=7, min_data_in_leaf=1, num_threads=1,
    )
    return r_cfg, rk_cfg


def _resign_manifest(root: Path, payload: dict) -> None:
    payload['release_id'] = v2_release_identity(payload)
    atomic_json(root / 'manifest.json', payload)


def _update_file_entry(root: Path, payload: dict, key: str, filename: str, rows: int | None) -> None:
    fpath = root / filename
    payload['files'][key] = {
        'path': filename,
        'sha256': sha256(fpath),
        'size_bytes': fpath.stat().st_size,
        'rows': rows,
    }


# 1. Import isolation & sklearn=None (hmrec-app parity)
def test_package_import_isolation_and_no_sklearn_verify(tmp_path, monkeypatch):
    code_import = (
        'import sys; '
        'import hm_recommender.v2; '
        'import hm_recommender.v2.package; '
        'bad = {"lightgbm", "sklearn", "torch", "hm_recommender.ranking.history", "hm_recommender.quality.baselines"} & set(sys.modules); '
        'assert not bad, f"Forbidden modules imported: {bad}"; '
        'print("PKG_ISOLATED_OK")'
    )
    assert subprocess.check_output([sys.executable, '-c', code_import], text=True).strip() == 'PKG_ISOLATED_OK'

    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    ds = _build_synthetic_dataset(tmp_path / 'ds')
    r_cfg, rk_cfg = _fast_configs()
    pkg_dir = tmp_path / 'pkg'
    build_v2_release(ds, date(2020, 9, 23), pkg_dir, retrieval_config=r_cfg, ranker_config=rk_cfg)

    code_no_sklearn = (
        'import sys; '
        'sys.modules["sklearn"] = None; '
        'import polars as pl; '
        'from hm_recommender.v2.package import verify_v2_release; '
        'from hm_recommender.v2.features import V2FeatureContext; '
        'from hm_recommender.v2.latent import LatentContentIndex; '
        'from hm_recommender.v2.ranker import load_booster; '
        f'm = verify_v2_release({str(pkg_dir)!r}); '
        f'cat = pl.read_parquet({str(pkg_dir / "catalog.parquet")!r}); '
        'ctx = V2FeatureContext.from_tables('
        'catalog=cat, '
        f'item_stats=pl.read_parquet({str(pkg_dir / "item_stats.parquet")!r}), '
        f'user_stats=pl.read_parquet({str(pkg_dir / "user_stats.parquet")!r}), '
        f'user_affinities=pl.read_parquet({str(pkg_dir / "user_affinities.parquet")!r}), '
        f'histories=pl.read_parquet({str(pkg_dir / "histories.parquet")!r}), '
        'retain_dataframes=False); '
        f'lat = LatentContentIndex.load({str(pkg_dir)!r}, article_ids=cat["article_id"].to_list()); '
        f'b = load_booster({str(pkg_dir / "model.txt")!r}); '
        'assert "sklearn" in sys.modules and sys.modules["sklearn"] is None; '
        'assert b.num_trees() >= 1 and lat.n_articles == cat.height and ctx._n_items == cat.height; '
        'print("NO_SKLEARN_VERIFY_OK")'
    )
    assert subprocess.check_output([sys.executable, '-c', code_no_sklearn], text=True).strip() == 'NO_SKLEARN_VERIFY_OK'


# 2. Synthetic roundtrip + publish_v2_release + online/offline parity
def test_roundtrip_publish_and_online_parity(tmp_path, monkeypatch):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    # Break singular-value symmetry across u1..u4 before refit comparison
    ds_seed = _build_synthetic_dataset(tmp_path / 'ds_seed')
    seed_tx = pl.read_parquet(tmp_path / 'ds_seed/transactions/part.parquet')
    asym = pl.DataFrame(
        [
            ('2020-09-09', 'u1', 'a001', 0.02, 2),
            ('2020-09-10', 'u1', 'a002', 0.02, 2),
            ('2020-09-11', 'u2', 'a004', 0.03, 1),
            ('2020-09-12', 'u3', 'a007', 0.04, 2),
            ('2020-09-13', 'u3', 'a008', 0.04, 2),
            ('2020-09-14', 'u3', 'a007', 0.04, 2),
        ],
        schema=['t_dat', 'customer_id', 'article_id', 'price', 'sales_channel_id'],
        orient='row',
    ).with_columns(pl.col('t_dat').str.to_date(), pl.col('price').cast(pl.Float64), pl.col('sales_channel_id').cast(pl.Int8))
    ds = _build_synthetic_dataset(
        tmp_path / 'ds',
        tx_rows=[tuple(r) for r in pl.concat([seed_tx, asym]).with_columns(pl.col('t_dat').cast(pl.String)).iter_rows()],
    )
    r_cfg, rk_cfg = _fast_configs()
    manifest = publish_v2_release(ds, date(2020, 9, 23), tmp_path / 'releases', retrieval_config=r_cfg, ranker_config=rk_cfg)

    assert re.fullmatch(r'v2-[a-f0-9]{64}', manifest.release_id)
    pkg_dir = tmp_path / 'releases' / manifest.release_id
    assert pkg_dir.is_dir()
    assert {p.name for p in pkg_dir.iterdir()} == set(EXPECTED_FILENAMES.values()) | {'manifest.json'}
    assert verify_v2_release(pkg_dir) == manifest

    # Online reconstruction parity vs offline
    cutoff = date(2020, 9, 23)
    retriever = MultiCandidateRetriever.fit(ds, cutoff=cutoff, config=r_cfg)
    feat_off = V2FeatureContext.from_dataset(ds, cutoff=cutoff, retain_dataframes=True)
    booster = load_booster(pkg_dir / 'model.txt')

    cat = pl.read_parquet(pkg_dir / 'catalog.parquet')
    feat_on = V2FeatureContext.from_tables(
        catalog=cat,
        item_stats=pl.read_parquet(pkg_dir / 'item_stats.parquet'),
        user_stats=pl.read_parquet(pkg_dir / 'user_stats.parquet'),
        user_affinities=pl.read_parquet(pkg_dir / 'user_affinities.parquet'),
        histories=pl.read_parquet(pkg_dir / 'histories.parquet'),
        cutoff=cutoff,
        retain_dataframes=False,
    )
    lat_on = LatentContentIndex.load(pkg_dir, article_ids=cat['article_id'].to_list())
    assert lat_on.k == retriever.latent_index.k
    np.testing.assert_allclose(lat_on.svd_sigma, retriever.latent_index.svd_sigma, rtol=1e-5, atol=1e-5)
    np.testing.assert_allclose(np.abs(lat_on.svd_v), np.abs(retriever.latent_index.svd_v), rtol=1e-5, atol=1e-5)
    np.testing.assert_allclose(
        lat_on.svd_v @ lat_on.svd_v.T,
        retriever.latent_index.svd_v @ retriever.latent_index.svd_v.T,
        rtol=1e-5,
        atol=1e-5,
    )

    cands = retriever.retrieve(['u1', 'u_cold'])
    f_off = feat_off.transform(cands)
    f_on = feat_on.transform(cands)
    assert f_off.equals(f_on)
    s_off = score_candidates(cands, f_off, booster=booster, num_threads=1)
    s_on = score_candidates(cands, f_on, booster=booster, num_threads=1)
    assert s_off.equals(s_on)


# 3. Cutoff bounds & empty 180d history edge case
def test_cutoff_validation_and_empty_180d_history_edge_case(tmp_path, monkeypatch):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    ds = _build_synthetic_dataset(tmp_path / 'ds')
    r_cfg, rk_cfg = _fast_configs()

    with pytest.raises(ValueError):
        build_v2_release(ds, date(2020, 6, 1), tmp_path / 'bad1', retrieval_config=r_cfg, ranker_config=rk_cfg)
    with pytest.raises(ValueError):
        build_v2_release(ds, date(2020, 9, 24), tmp_path / 'bad2', retrieval_config=r_cfg, ranker_config=rk_cfg)
    with pytest.raises(ValueError):
        build_v2_release(ds, datetime(2020, 9, 23, 12, 0), tmp_path / 'bad3', retrieval_config=r_cfg, ranker_config=rk_cfg)  # type: ignore[arg-type]

    # Empty 180d histories/affinities at data_as_of=2020-09-23 (tx only on 2020-01-10), using pre-trained booster
    valid_pkg = build_v2_release(ds, date(2020, 9, 23), tmp_path / 'valid_for_model', retrieval_config=r_cfg, ranker_config=rk_cfg)
    old_ds = _build_synthetic_dataset(
        tmp_path / 'old_ds',
        tx_rows=[('2020-01-10', 'u1', 'a001', 0.02, 2), ('2020-01-11', 'u2', 'a002', 0.03, 1)],
        min_date=date(2020, 1, 1),
        max_date=date(2020, 9, 22),
    )
    old_pkg = build_v2_release(
        old_ds,
        date(2020, 9, 23),
        tmp_path / 'old_pkg',
        retrieval_config=r_cfg,
        ranker_config=rk_cfg,
        model_path=tmp_path / 'valid_for_model/model.txt',
        validation_reference={'best_iteration': load_booster(tmp_path / 'valid_for_model/model.txt').current_iteration()},
    )
    assert old_pkg.files['histories'].rows == 0
    assert old_pkg.files['user_affinities'].rows == 0
    assert verify_v2_release(tmp_path / 'old_pkg') == old_pkg


# 4 & 5 & 6 & 7. Symlinks, missing/extra/traversal, SHA/size/rows, schema/rules/val_ref tampering
@pytest.mark.parametrize('mutation', [
    'symlink_dir',
    'symlink_catalog',
    'symlink_model',
    'missing_svd_sigma',
    'extra_file',
    'traversal_path',
    'corrupt_svd_v_bytes',
    'size_mismatch',
    'rows_mismatch',
    'schema_version',
    'bad_release_id',
    'rules_mismatch',
    'val_ref_date_mismatch',
])
def test_tampering_filesystem_integrity_and_provenance(tmp_path, monkeypatch, mutation):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    ds = _build_synthetic_dataset(tmp_path / 'ds')
    r_cfg, rk_cfg = _fast_configs()
    root = tmp_path / 'pkg'
    build_v2_release(ds, date(2020, 9, 23), root, retrieval_config=r_cfg, ranker_config=rk_cfg)

    payload = json.loads((root / 'manifest.json').read_text())

    if mutation == 'symlink_dir':
        link = tmp_path / 'pkg_link'
        link.symlink_to(root)
        with pytest.raises(ValueError):
            verify_v2_release(link)
        return
    if mutation == 'symlink_catalog':
        outside = tmp_path / 'outside.parquet'
        (root / 'catalog.parquet').rename(outside)
        (root / 'catalog.parquet').symlink_to(outside)
    elif mutation == 'symlink_model':
        outside = tmp_path / 'outside.txt'
        (root / 'model.txt').rename(outside)
        (root / 'model.txt').symlink_to(outside)
    elif mutation == 'missing_svd_sigma':
        (root / 'svd_sigma.npy').unlink()
    elif mutation == 'extra_file':
        (root / 'untracked.txt').write_text('oops')
    elif mutation == 'traversal_path':
        payload['files']['catalog']['path'] = '../x.parquet'
        _resign_manifest(root, payload)
        with pytest.raises((ValueError, ValidationError)):
            verify_v2_release(root)
        return
    elif mutation == 'corrupt_svd_v_bytes':
        data = bytearray((root / 'svd_v.npy').read_bytes())
        data[-1] ^= 0xFF
        (root / 'svd_v.npy').write_bytes(bytes(data))
    elif mutation == 'size_mismatch':
        payload['files']['model']['size_bytes'] += 1
        _resign_manifest(root, payload)
    elif mutation == 'rows_mismatch':
        payload['files']['catalog']['rows'] += 1
        _resign_manifest(root, payload)
    elif mutation == 'schema_version':
        payload['schema_version'] = 2
        atomic_json(root / 'manifest.json', payload)
        with pytest.raises((ValueError, ValidationError)):
            verify_v2_release(root)
        return
    elif mutation == 'bad_release_id':
        payload['release_id'] = 'v2-' + ('f' * 64)
        atomic_json(root / 'manifest.json', payload)
    elif mutation == 'rules_mismatch':
        rules_p = json.loads((root / 'rules.json').read_text())
        rules_p['history_days'] = 90
        atomic_json(root / 'rules.json', rules_p)
        _update_file_entry(root, payload, 'rules', 'rules.json', None)
        _resign_manifest(root, payload)
    elif mutation == 'val_ref_date_mismatch':
        vr = json.loads((root / 'validation-reference.json').read_text())
        vr['data_as_of'] = '2020-09-22'
        atomic_json(root / 'validation-reference.json', vr)
        _update_file_entry(root, payload, 'validation-reference', 'validation-reference.json', None)
        _resign_manifest(root, payload)

    with pytest.raises(ValueError):
        verify_v2_release(root)


# 8. Temporal leakage isolation & verification
def test_temporal_leakage_isolation_and_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    r_cfg, rk_cfg = _fast_configs()
    ds_seed = _build_synthetic_dataset(tmp_path / 'ds_seed')
    seed_tx = pl.read_parquet(tmp_path / 'ds_seed/transactions/part.parquet')
    # Break singular-value symmetry across u1..u4 so SVD eigenvectors are unique and bitwise deterministic
    asym = pl.DataFrame(
        [
            ('2020-09-09', 'u1', 'a001', 0.02, 2),
            ('2020-09-10', 'u1', 'a002', 0.02, 2),
            ('2020-09-11', 'u1', 'a003', 0.03, 2),
            ('2020-09-12', 'u2', 'a004', 0.03, 1),
            ('2020-09-13', 'u3', 'a007', 0.04, 2),
        ],
        schema=['t_dat', 'customer_id', 'article_id', 'price', 'sales_channel_id'],
        orient='row',
    ).with_columns(pl.col('t_dat').str.to_date(), pl.col('price').cast(pl.Float64), pl.col('sales_channel_id').cast(pl.Int8))
    base_tx = pl.concat([seed_tx, asym])
    ds_a = _build_synthetic_dataset(
        tmp_path / 'ds_a',
        tx_rows=[tuple(r) for r in base_tx.with_columns(pl.col('t_dat').cast(pl.String)).iter_rows()],
    )
    m_a = build_v2_release(ds_a, date(2020, 9, 16), tmp_path / 'pkg_a', retrieval_config=r_cfg, ranker_config=rk_cfg)

    # Inject extra transactions >= 2020-09-16: artifacts & data tables must match
    extra = pl.DataFrame(
        [('2020-09-16', 'u_cold', 'a012', 0.09, 2), ('2020-09-22', 'u1', 'a011', 0.09, 2)],
        schema=['t_dat', 'customer_id', 'article_id', 'price', 'sales_channel_id'],
        orient='row',
    ).with_columns(pl.col('t_dat').str.to_date(), pl.col('price').cast(pl.Float64), pl.col('sales_channel_id').cast(pl.Int8))
    ds_b = _build_synthetic_dataset(
        tmp_path / 'ds_b',
        tx_rows=[tuple(r) for r in pl.concat([base_tx, extra]).with_columns(pl.col('t_dat').cast(pl.String)).iter_rows()],
    )
    m_b = build_v2_release(ds_b, date(2020, 9, 16), tmp_path / 'pkg_b', retrieval_config=r_cfg, ranker_config=rk_cfg)
    for fname in ('catalog', 'item_stats', 'user_stats', 'user_affinities', 'histories', 'segment_popularity', 'cooc', 'content_tfidf'):
        assert m_a.files[fname].sha256 == m_b.files[fname].sha256, f'{fname} differed'

    svd_v_a = np.load(tmp_path / 'pkg_a/svd_v.npy', allow_pickle=False)
    svd_v_b = np.load(tmp_path / 'pkg_b/svd_v.npy', allow_pickle=False)
    svd_s_a = np.load(tmp_path / 'pkg_a/svd_sigma.npy', allow_pickle=False)
    svd_s_b = np.load(tmp_path / 'pkg_b/svd_sigma.npy', allow_pickle=False)
    assert svd_v_a.shape == svd_v_b.shape and svd_v_a.dtype == svd_v_b.dtype == np.float32
    assert svd_s_a.shape == svd_s_b.shape and svd_s_a.dtype == svd_s_b.dtype == np.float32
    np.testing.assert_allclose(svd_s_a, svd_s_b, rtol=1e-5, atol=1e-5)
    np.testing.assert_allclose(np.abs(svd_v_a), np.abs(svd_v_b), rtol=1e-5, atol=1e-5)
    np.testing.assert_allclose(svd_v_a @ svd_v_a.T, svd_v_b @ svd_v_b.T, rtol=1e-5, atol=1e-5)

    # Mutate catalog.first_seen >= data_as_of & histories.last_purchase >= data_as_of -> rejected
    for tbl, col in (('catalog', 'first_seen'), ('histories', 'last_purchase')):
        bad_dir = tmp_path / f'leak_{tbl}'
        build_v2_release(ds_a, date(2020, 9, 16), bad_dir, retrieval_config=r_cfg, ranker_config=rk_cfg)
        df = pl.read_parquet(bad_dir / f'{tbl}.parquet').with_columns(pl.lit(date(2020, 9, 16)).alias(col))
        df.write_parquet(bad_dir / f'{tbl}.parquet')
        payload = json.loads((bad_dir / 'manifest.json').read_text())
        _update_file_entry(bad_dir, payload, tbl, f'{tbl}.parquet', df.height)
        _resign_manifest(bad_dir, payload)
        with pytest.raises(ValueError):
            verify_v2_release(bad_dir)


# 9 & 10. Keys, sorting, referential integrity, catalog/item_stats & node alignment
@pytest.mark.parametrize('mutation', [
    'catalog_unsorted',
    'catalog_duplicate',
    'user_stats_empty_key',
    'histories_unsorted',
    'histories_unbound_user',
    'histories_unbound_article',
    'item_stats_swapped',
    'seg_pop_bad_node',
])
def test_semantic_table_and_alignment_rejections(tmp_path, monkeypatch, mutation):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    ds = _build_synthetic_dataset(tmp_path / 'ds')
    r_cfg, rk_cfg = _fast_configs()
    root = tmp_path / 'pkg'
    build_v2_release(ds, date(2020, 9, 23), root, retrieval_config=r_cfg, ranker_config=rk_cfg)
    payload = json.loads((root / 'manifest.json').read_text())

    if mutation == 'catalog_unsorted':
        df = pl.read_parquet(root / 'catalog.parquet').reverse()
        df.write_parquet(root / 'catalog.parquet')
        _update_file_entry(root, payload, 'catalog', 'catalog.parquet', df.height)
    elif mutation == 'catalog_duplicate':
        cat = pl.read_parquet(root / 'catalog.parquet')
        cat = pl.concat([cat, cat.tail(1)])
        cat.write_parquet(root / 'catalog.parquet')
        _update_file_entry(root, payload, 'catalog', 'catalog.parquet', cat.height)
    elif mutation == 'user_stats_empty_key':
        us = pl.read_parquet(root / 'user_stats.parquet')
        us = us.with_columns(pl.when(pl.arange(0, us.height) == 0).then(pl.lit('')).otherwise(pl.col('customer_id')).alias('customer_id'))
        us.write_parquet(root / 'user_stats.parquet')
        _update_file_entry(root, payload, 'user_stats', 'user_stats.parquet', us.height)
    elif mutation == 'histories_unsorted':
        df = pl.read_parquet(root / 'histories.parquet').reverse()
        df.write_parquet(root / 'histories.parquet')
        _update_file_entry(root, payload, 'histories', 'histories.parquet', df.height)
    elif mutation == 'histories_unbound_user':
        df = pl.read_parquet(root / 'histories.parquet')
        row = df.head(1).with_columns(pl.lit('ghost_user').alias('customer_id'))
        df = pl.concat([row, df]).sort(['customer_id', 'last_purchase', 'count_180', 'article_id'], descending=[False, True, True, False])
        df.write_parquet(root / 'histories.parquet')
        _update_file_entry(root, payload, 'histories', 'histories.parquet', df.height)
    elif mutation == 'histories_unbound_article':
        df = pl.read_parquet(root / 'histories.parquet')
        row = df.head(1).with_columns(pl.lit('ghost_article').alias('article_id'))
        df = pl.concat([row, df]).sort(['customer_id', 'last_purchase', 'count_180', 'article_id'], descending=[False, True, True, False])
        df.write_parquet(root / 'histories.parquet')
        _update_file_entry(root, payload, 'histories', 'histories.parquet', df.height)
    elif mutation == 'item_stats_swapped':
        df = pl.read_parquet(root / 'item_stats.parquet').reverse()
        df.write_parquet(root / 'item_stats.parquet')
        _update_file_entry(root, payload, 'item_stats', 'item_stats.parquet', df.height)
    elif mutation == 'seg_pop_bad_node':
        df = pl.read_parquet(root / 'segment_popularity.parquet')
        df = df.with_columns(pl.when(pl.arange(0, df.height) == 0).then((pl.col('node') + 1) % 12).otherwise(pl.col('node')).cast(pl.Int32).alias('node'))
        df.write_parquet(root / 'segment_popularity.parquet')
        _update_file_entry(root, payload, 'segment_popularity', 'segment_popularity.parquet', df.height)

    _resign_manifest(root, payload)
    with pytest.raises(ValueError):
        verify_v2_release(root)


# 11. Matrix shapes, dtypes, finiteness, diagonal, duplicate indices, sigma monotonicity
@pytest.mark.parametrize('mutation', [
    'svd_v_bad_rows',
    'svd_v_float64',
    'svd_v_nan',
    'svd_sigma_bad_len',
    'svd_sigma_negative',
    'svd_sigma_non_descending',
    'cooc_bad_shape',
    'cooc_nonzero_diag',
    'cooc_dup_indices',
    'tfidf_bad_rows',
])
def test_matrix_validation_rejections(tmp_path, monkeypatch, mutation):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    ds = _build_synthetic_dataset(tmp_path / 'ds')
    r_cfg, rk_cfg = _fast_configs()
    root = tmp_path / 'pkg'
    build_v2_release(ds, date(2020, 9, 23), root, retrieval_config=r_cfg, ranker_config=rk_cfg)
    payload = json.loads((root / 'manifest.json').read_text())

    svd_v = np.load(root / 'svd_v.npy', allow_pickle=False)
    svd_sigma = np.load(root / 'svd_sigma.npy', allow_pickle=False)
    n_items, k = svd_v.shape

    if mutation == 'svd_v_bad_rows':
        np.save(root / 'svd_v.npy', svd_v[:-1])
        _update_file_entry(root, payload, 'svd_v', 'svd_v.npy', None)
    elif mutation == 'svd_v_float64':
        np.save(root / 'svd_v.npy', svd_v.astype(np.float64))
        _update_file_entry(root, payload, 'svd_v', 'svd_v.npy', None)
    elif mutation == 'svd_v_nan':
        svd_v[0, 0] = np.nan
        np.save(root / 'svd_v.npy', svd_v)
        _update_file_entry(root, payload, 'svd_v', 'svd_v.npy', None)
    elif mutation == 'svd_sigma_bad_len':
        np.save(root / 'svd_sigma.npy', np.ones(k + 1, dtype=np.float32))
        _update_file_entry(root, payload, 'svd_sigma', 'svd_sigma.npy', None)
    elif mutation == 'svd_sigma_negative':
        bad = svd_sigma.copy()
        bad[-1] = -0.1
        np.save(root / 'svd_sigma.npy', bad)
        _update_file_entry(root, payload, 'svd_sigma', 'svd_sigma.npy', None)
    elif mutation == 'svd_sigma_non_descending':
        bad = np.arange(1, k + 1, dtype=np.float32)
        np.save(root / 'svd_sigma.npy', bad)
        _update_file_entry(root, payload, 'svd_sigma', 'svd_sigma.npy', None)
    elif mutation == 'cooc_bad_shape':
        sparse.save_npz(root / 'cooc.npz', sparse.csr_matrix((n_items - 1, n_items - 1), dtype=np.float32))
        _update_file_entry(root, payload, 'cooc', 'cooc.npz', None)
    elif mutation == 'cooc_nonzero_diag':
        cooc = sparse.load_npz(root / 'cooc.npz').tolil()
        cooc[0, 0] = 1.0
        sparse.save_npz(root / 'cooc.npz', cooc.tocsr().astype(np.float32))
        _update_file_entry(root, payload, 'cooc', 'cooc.npz', None)
    elif mutation == 'cooc_dup_indices':
        indptr = np.zeros(n_items + 1, dtype=np.int32)
        indptr[1:] = 2
        dup = sparse.csr_matrix((np.array([1.0, 1.0], dtype=np.float32), np.array([1, 1], dtype=np.int32), indptr), shape=(n_items, n_items))
        sparse.save_npz(root / 'cooc.npz', dup)
        _update_file_entry(root, payload, 'cooc', 'cooc.npz', None)
    elif mutation == 'tfidf_bad_rows':
        tfidf = sparse.load_npz(root / 'content_tfidf.npz')
        sparse.save_npz(root / 'content_tfidf.npz', tfidf[:-1])
        _update_file_entry(root, payload, 'content_tfidf', 'content_tfidf.npz', None)

    _resign_manifest(root, payload)
    with pytest.raises(ValueError):
        verify_v2_release(root)


# 12. model.txt feature name & iteration mismatch
def test_model_feature_and_iteration_mismatch(tmp_path, monkeypatch):
    import lightgbm as lgb

    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    ds = _build_synthetic_dataset(tmp_path / 'ds')
    r_cfg, rk_cfg = _fast_configs()
    root = tmp_path / 'pkg'
    build_v2_release(ds, date(2020, 9, 23), root, retrieval_config=r_cfg, ranker_config=rk_cfg)

    # Wrong feature names in model.txt
    wrong_ds = lgb.Dataset(np.zeros((4, 3), dtype=np.float32), label=[1, 0, 1, 0], group=[2, 2], feature_name=['f0', 'f1', 'f2'])
    wrong_b = lgb.train({'objective': 'lambdarank', 'verbosity': -1}, wrong_ds, num_boost_round=1)
    wrong_b.save_model(str(root / 'model.txt'))
    payload = json.loads((root / 'manifest.json').read_text())
    _update_file_entry(root, payload, 'model', 'model.txt', None)
    _resign_manifest(root, payload)
    with pytest.raises(ValueError):
        verify_v2_release(root)


# 13. Atomic publication & destination reuse guard
def test_atomic_publication_and_destination_guard(tmp_path, monkeypatch):
    import hm_recommender.v2.package as pkg_mod

    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a' * 40)
    ds = _build_synthetic_dataset(tmp_path / 'ds')
    r_cfg, rk_cfg = _fast_configs()
    root = tmp_path / 'release_atomic'

    # Idempotent rebuild succeeds & mismatched data_as_of fails without overwriting
    m1 = build_v2_release(ds, date(2020, 9, 23), root, retrieval_config=r_cfg, ranker_config=rk_cfg)
    m2 = build_v2_release(ds, date(2020, 9, 23), root, retrieval_config=r_cfg, ranker_config=rk_cfg)
    assert m1 == m2
    orig_manifest_bytes = (root / 'manifest.json').read_bytes()
    with pytest.raises(ValueError):
        build_v2_release(ds, date(2020, 9, 22), root, retrieval_config=r_cfg, ranker_config=rk_cfg)
    assert (root / 'manifest.json').read_bytes() == orig_manifest_bytes

    # Failure inside verify_v2_release keeps target unpublished and leaves no .partial-* dir
    failed_dest = tmp_path / 'failed_release'
    def boom(*_args, **_kwargs):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(pkg_mod, 'verify_v2_release', boom)
    with pytest.raises(OSError):
        build_v2_release(ds, date(2020, 9, 23), failed_dest, retrieval_config=r_cfg, ranker_config=rk_cfg)
    assert not failed_dest.exists()
    assert not list(tmp_path.glob('.failed_release.partial-*'))
