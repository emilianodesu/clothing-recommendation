from datetime import date, datetime, timedelta
import math
import subprocess
import sys
import warnings

import numpy as np
import polars as pl
import pyarrow.parquet as pq
import pytest

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json
from hm_recommender.v2 import (
    AGE_BINS,
    CANDIDATE_SCHEMA,
    MISSING_RANK_SENTINEL,
    RANK_COLUMNS,
    SCORE_COLUMNS,
    LatentContentIndex,
    MultiCandidateRetriever,
    RetrievalConfig,
    age_bin_expr,
    cast_age_expr,
)
from hm_recommender.v2.retrieval import _bin_age_scalar


def _write_synthetic_dataset(
    root,
    tx_rows: list[tuple[str, str, str]],
    articles_df: pl.DataFrame,
    customers_df: pl.DataFrame | None = None,
) -> DatasetManifest:
    root.mkdir(parents=True, exist_ok=True)
    tx_df = (
        pl.DataFrame(tx_rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row')
        .with_columns(pl.col('t_dat').str.to_date())
    )
    partitions = [
        write_partition(tx_df.to_arrow(), root, 'transactions/part.parquet'),
        write_partition(articles_df.to_arrow(), root, 'articles/part.parquet'),
    ]
    counts = {'transactions': tx_df.height, 'articles': articles_df.height}
    if customers_df is not None:
        partitions.append(write_partition(customers_df.to_arrow(), root, 'customers/part.parquet'))
        counts['customers'] = customers_df.height

    ds = DatasetManifest(
        dataset_id='v2-fixture',
        source_sha256='a' * 64,
        min_date=date(2020, 1, 1),
        max_date=date(2020, 9, 22),
        counts=counts,
        partitions=partitions,
    )
    ds._root = root
    atomic_json(root / 'manifest.json', ds.model_dump(mode='json'))
    return ds


# ---------------------------------------------------------------------------
# 1. Import isolation (zero sklearn / torch / lightgbm / ranking.history)
# ---------------------------------------------------------------------------

def test_v2_imports_without_sklearn_or_torch():
    code = (
        'import sys; '
        'import hm_recommender.v2; '
        'from hm_recommender.v2 import LatentContentIndex, MultiCandidateRetriever; '
        'bad = {"sklearn", "torch", "lightgbm", "hm_recommender.ranking.history"} & set(sys.modules); '
        'assert not bad, f"Forbidden modules imported: {bad}"; '
        'print("ISOLATED_OK")'
    )
    out = subprocess.check_output([sys.executable, '-c', code], text=True)
    assert out.strip() == 'ISOLATED_OK'


# ---------------------------------------------------------------------------
# 2. Strict < cutoff temporal isolation & cutoff type validation
# ---------------------------------------------------------------------------

def test_future_transactions_and_boundary_cutoff_excluded(tmp_path):
    cutoff = date(2020, 9, 9)
    past_rows = [
        ('2020-08-20', 'u1', '001'),
        ('2020-09-01', 'u1', '002'),
        ('2020-09-05', 'u2', '001'),
        ('2020-09-07', 'u2', '003'),
        ('2020-09-08', 'u3', '002'),
        ('2020-09-08', 'u3', '004'),
    ]
    future_rows = [
        ('2020-09-09', 'u1', '9999999999'),  # exact cutoff date -> excluded
        ('2020-09-10', 'u1', '003'),
        ('2020-09-15', 'u2', '9999999999'),
        ('2020-09-15', 'u4', '9999999999'),
    ]
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003', '004', '9999999999'],
        'product_code': ['p1', 'p1', 'p2', 'p3', 'p99'],
        'product_type_name': ['Dress', 'Dress', 'Trousers', 'Shirt', 'Coat'],
        'product_group_name': ['Garment', 'Garment', 'Garment', 'Garment', 'Garment'],
        'colour_group_name': ['Black', 'Blue', 'Black', 'White', 'Red'],
        'index_group_name': ['Ladieswear', 'Ladieswear', 'Ladieswear', 'Menswear', 'Ladieswear'],
        'detail_desc': ['Summer dress', 'Blue summer dress', 'Tailored pants', 'Cotton shirt', 'Future coat'],
    })
    ds_left = _write_synthetic_dataset(tmp_path / 'left', past_rows, articles)
    ds_right = _write_synthetic_dataset(tmp_path / 'right', past_rows + future_rows, articles)

    r_left = MultiCandidateRetriever.fit(ds_left, cutoff=cutoff)
    r_right = MultiCandidateRetriever.fit(ds_right, cutoff=cutoff)

    assert '9999999999' not in r_left.article_ids
    assert '9999999999' not in r_right.article_ids
    assert r_left.article_ids == r_right.article_ids

    out_left = r_left.retrieve(['u1', 'u2', 'u3', 'u4'])
    out_right = r_right.retrieve(['u1', 'u2', 'u3', 'u4'])
    assert out_left.equals(out_right)
    assert '9999999999' not in out_right['article_id'].to_list()


def test_non_date_cutoff_rejected(tmp_path):
    articles = pl.DataFrame({'article_id': ['001']})
    tx = pl.DataFrame({'t_dat': [date(2020, 9, 1)], 'customer_id': ['u1'], 'article_id': ['001']})
    for bad in (datetime(2020, 9, 9, 12, 0), '2020-09-09', 20200909):
        with pytest.raises(ValueError):
            MultiCandidateRetriever.fit_frames(tx, articles, cutoff=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            LatentContentIndex.fit(tx, articles, cutoff=bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. Defensive schemas & missing partitions
# ---------------------------------------------------------------------------

def test_customers_age_string_casting_and_bins():
    df = pl.DataFrame({
        'age': ['18', '25', '34.0', '45', '60', None, '', 'invalid', '-3', '250', '10', '110', '9.9', '110.1']
    })
    out = df.select(cast_age_expr('age').alias('age_f'), age_bin_expr('age'))
    assert out['age_f'].dtype == pl.Float32
    assert out['age_bin'].to_list() == [
        '<25',
        '25-34',
        '25-34',
        '45-54',
        '55+',
        'unknown',
        'unknown',
        'unknown',
        'unknown',
        'unknown',
        '<25',
        '55+',
        'unknown',
        'unknown',
    ]
    assert set(out['age_bin'].to_list()).issubset(set(AGE_BINS))


def test_missing_customers_partition_and_optional_article_columns(tmp_path):
    cutoff = date(2020, 9, 9)
    tx_rows = [
        ('2020-09-01', 'u1', '001'),
        ('2020-09-02', 'u1', '002'),
        ('2020-09-03', 'u2', '002'),
        ('2020-09-04', 'u2', '003'),
    ]
    minimal_articles = pl.DataFrame({
        'article_id': ['001', '002', '003'],
        'product_type_name': ['Shirt', 'Shirt', 'Pants'],
    })
    ds = _write_synthetic_dataset(tmp_path / 'minimal', tx_rows, minimal_articles, customers_df=None)

    retriever = MultiCandidateRetriever.fit(ds, cutoff=cutoff)
    assert retriever.variant_matrix.nnz == 0
    assert retriever.index_groups == ('', '', '')

    res = retriever.retrieve(['u1', 'cold_user'])
    assert res.schema == CANDIDATE_SCHEMA
    assert res.filter(pl.col('customer_id') == 'cold_user').height == 3


# ---------------------------------------------------------------------------
# 4. Individual verification of all 5 generators + global pop7d
# ---------------------------------------------------------------------------

def test_history_decay_exact_weights_and_ties():
    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 8), date(2020, 9, 8), date(2020, 8, 26)],
        'customer_id': ['u1', 'u1', 'u1'],
        'article_id': ['002', '001', '003'],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003', '004'],
        'product_type_name': ['Top', 'Top', 'Skirt', 'Coat'],
    })
    cfg = RetrievalConfig(history_limit=2)
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff, config=cfg)
    out = retriever.retrieve(
        ['u1'],
        top_k_per_generator={
            'history_decay': 2,
            'product_code_variants': 1,
            'cooccurrence': 1,
            'latent': 1,
            'content': 1,
            'segment_popularity': 1,
            'pop7d': 1,
        },
    )
    hist_hits = (
        out.filter(pl.col('rank_history_decay') < MISSING_RANK_SENTINEL)
        .sort('rank_history_decay')
    )
    assert hist_hits['article_id'].to_list() == ['001', '002']  # tied score -> ascending article_id
    expected_1d = 0.6 * math.exp(-1.0 / 14.0) + 0.4 * math.exp(-1.0 / 60.0)
    assert hist_hits['score_history_decay'].to_list()[0] == pytest.approx(expected_1d, rel=1e-5)
    assert hist_hits['score_history_decay'].to_list()[1] == pytest.approx(expected_1d, rel=1e-5)


def test_product_code_variants_excludes_purchased_and_ranks_by_pop14d():
    cutoff = date(2020, 9, 9)
    # u1 bought 001 (product_code P100). Variants 002 and 003 also have P100.
    # 003 has higher pop14d than 002. 004 has different product_code P200.
    tx_rows = [
        ('2020-09-08', 'u1', '001'),
        ('2020-09-07', 'u2', '003'),
        ('2020-09-06', 'u3', '003'),
        ('2020-09-05', 'u4', '003'),
        ('2020-09-04', 'u2', '002'),
        ('2020-09-04', 'u2', '004'),
    ]
    tx = pl.DataFrame(tx_rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row').with_columns(
        pl.col('t_dat').str.to_date()
    )
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003', '004'],
        'product_code': ['P100', 'P100', 'P100', 'P200'],
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff)
    out = retriever.retrieve(['u1'])
    var_hits = out.filter(pl.col('rank_variant') < MISSING_RANK_SENTINEL).sort('rank_variant')
    assert var_hits['article_id'].to_list() == ['003', '002']
    assert '001' not in var_hits['article_id'].to_list()
    assert '004' not in var_hits['article_id'].to_list()


def test_cooccurrence_weekly_basket_bm25_no_self_loops_and_blockwise():
    cutoff = date(2020, 9, 9)
    # One oversized basket of 52 items (000..051) + two normal baskets linking 000<->052 and 000<->053.
    rows = [('2020-09-08', 'big', f'{i:03}') for i in range(52)]
    rows += [
        ('2020-09-08', 'b1', '000'),
        ('2020-09-08', 'b1', '052'),
        ('2020-09-01', 'b2', '000'),
        ('2020-09-01', 'b2', '053'),
    ]
    tx = pl.DataFrame(rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row').with_columns(
        pl.col('t_dat').str.to_date()
    )
    articles = pl.DataFrame({'article_id': [f'{i:03}' for i in range(54)]})

    r_b2 = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff, config=RetrievalConfig(cooc_block_nodes=2))
    r_b256 = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff, config=RetrievalConfig(cooc_block_nodes=256))

    m1, m2 = r_b2.cooc_matrix, r_b256.cooc_matrix
    assert m1.dtype == np.float32
    assert m1.indices.dtype == np.int32
    assert m1.diagonal().sum() == 0.0
    assert np.array_equal(m1.indptr, m2.indptr)
    assert np.array_equal(m1.indices, m2.indices)
    assert np.allclose(m1.data, m2.data)
    # 050 and 051 were truncated by head(50) in the oversized basket
    assert m1[50].nnz == 0 and m1[51].nnz == 0
    # 000 connects to 052 and 053 with positive BM25 weights, 052 more recent than 053
    assert m1[0, 52] > m1[0, 53] > 0.0


def test_latent_and_content_generators():
    cutoff = date(2020, 9, 9)
    # Two strong latent clusters: {000, 001, 002} and {003, 004, 005}.
    # u_target bought 000 -> latent should surface 001, 002 above {003..005}.
    # Content: 006 shares identical metadata/desc with 000 despite zero co-purchases.
    rows = []
    for u in ('a1', 'a2', 'a3', 'a4'):
        rows.extend([('2020-09-05', u, '000'), ('2020-09-05', u, '001'), ('2020-09-05', u, '002')])
    for u in ('b1', 'b2', 'b3', 'b4'):
        rows.extend([('2020-09-05', u, '003'), ('2020-09-05', u, '004'), ('2020-09-05', u, '005')])
    rows.append(('2020-09-08', 'u_target', '000'))
    rows.append(('2020-05-01', 'old_buyer', '006'))  # in catalog, outside 180d latent? Wait: May 1 is inside 180d; use Mar 1
    rows = [r if r[1] != 'old_buyer' else ('2020-03-01', 'old_buyer', '006') for r in rows]

    tx = pl.DataFrame(rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row').with_columns(
        pl.col('t_dat').str.to_date()
    )
    articles = pl.DataFrame({
        'article_id': ['000', '001', '002', '003', '004', '005', '006'],
        'product_type_name': ['Sneakers', 'Boots', 'Loafers', 'Dress', 'Skirt', 'Blouse', 'Sneakers'],
        'colour_group_name': ['White', 'Brown', 'Tan', 'Red', 'Pink', 'Red', 'White'],
        'detail_desc': [
            'white leather running sneakers',
            'leather walking boots',
            'casual suede loafers',
            'floral evening dress',
            'pleated midi skirt',
            'silk evening blouse',
            'white leather running sneakers cushioned sole',
        ],
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff, config=RetrievalConfig(latent_dim=4))
    out = retriever.retrieve(['u_target'])
    lat_top = out.filter(pl.col('rank_latent') < MISSING_RANK_SENTINEL).sort('rank_latent')['article_id'].to_list()
    assert set(lat_top[:2]) == {'001', '002'}
    con_top = out.filter(pl.col('rank_content') < MISSING_RANK_SENTINEL).sort('rank_content')['article_id'].to_list()
    assert con_top[0] == '006'


def test_segment_popularity_and_cold_start_fallback():
    cutoff = date(2020, 9, 9)
    # Young Divided buyers (<25, Divided) love item '001'; older Menswear buyers (55+, Menswear) love item '002'.
    tx_rows = [
        ('2020-09-07', 'y1', '001'),
        ('2020-09-07', 'y2', '001'),
        ('2020-09-07', 'y3', '001'),
        ('2020-09-07', 'o1', '002'),
        ('2020-09-07', 'o2', '002'),
        ('2020-08-01', 'target_young', '003'),  # past Divided buyer -> pref_ig='Divided'
        ('2020-08-01', 'target_old', '004'),    # past Menswear buyer -> pref_ig='Menswear'
    ]
    tx = pl.DataFrame(tx_rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row').with_columns(
        pl.col('t_dat').str.to_date()
    )
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003', '004'],
        'index_group_name': ['Divided', 'Menswear', 'Divided', 'Menswear'],
    })
    customers = pl.DataFrame({
        'customer_id': ['y1', 'y2', 'y3', 'o1', 'o2', 'target_young', 'target_old', 'cold_old'],
        'age': ['20', '21', '22', '60', '65', '21', '62', '63'],
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, customers, cutoff=cutoff)
    out = retriever.retrieve(['target_young', 'target_old', 'cold_old', 'unseen_cold'])

    y_top = out.filter((pl.col('customer_id') == 'target_young') & (pl.col('rank_seg_pop') == 1))['article_id'].to_list()
    o_top = out.filter((pl.col('customer_id') == 'target_old') & (pl.col('rank_seg_pop') == 1))['article_id'].to_list()
    c_old_top = out.filter((pl.col('customer_id') == 'cold_old') & (pl.col('rank_seg_pop') == 1))['article_id'].to_list()
    unseen_top = out.filter((pl.col('customer_id') == 'unseen_cold') & (pl.col('rank_seg_pop') == 1))['article_id'].to_list()

    assert y_top == ['001']
    assert o_top == ['002']
    assert c_old_top == ['002']       # age_bin='55+' fallback
    assert unseen_top == ['001']      # global fallback ('001' has count 3 > '002' count 2)


# ---------------------------------------------------------------------------
# 5. Combined schema, dtypes, sentinel & > 200 union candidates (no unique_prefix cap)
# ---------------------------------------------------------------------------

def test_combined_schema_dtypes_sentinel_and_over_200_candidates():
    cutoff = date(2020, 9, 9)
    n_items = 320
    # Target user buys 65 items (000..064), peers co-buy 065..144, variants cover 145..204,
    # content covers 205..264, pop7d covers 265..319 -> union > 250 items for u_target!
    rows: list[tuple[str, str, str]] = []
    for i in range(65):
        rows.append(('2020-09-05', 'u_target', f'{i:03}'))
    for j in range(65, 145):
        rows.append(('2020-09-06', 'peer1', '000'))
        rows.append(('2020-09-06', 'peer1', f'{j:03}'))
        rows.append(('2020-09-07', 'peer2', '001'))
        rows.append(('2020-09-07', 'peer2', f'{j:03}'))
    for j in range(265, 320):
        rows.append(('2020-09-08', f'pop_{j}', f'{j:03}'))
        rows.append(('2020-09-08', f'pop2_{j}', f'{j:03}'))
    for j in range(145, 265):
        rows.append(('2020-03-01', 'catalog_seed', f'{j:03}'))

    tx = pl.DataFrame(rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row').with_columns(
        pl.col('t_dat').str.to_date()
    )
    prod_codes = [f'pc_{i}' for i in range(n_items)]
    # Link 145..204 as variants of 000..059
    for k in range(60):
        prod_codes[145 + k] = prod_codes[k]
    articles = pl.DataFrame({
        'article_id': [f'{i:03}' for i in range(n_items)],
        'product_code': prod_codes,
        'product_type_name': ['Jacket' if (i < 65 or 205 <= i < 265) else 'Socks' for i in range(n_items)],
        'index_group_name': ['Ladieswear'] * n_items,
        'detail_desc': ['warm wool jacket' if (i < 65 or 205 <= i < 265) else 'cotton socks' for i in range(n_items)],
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff, config=RetrievalConfig(user_batch_size=2))

    # Empty customer input returns exact CANDIDATE_SCHEMA
    empty_df = retriever.retrieve([])
    assert empty_df.is_empty()
    assert empty_df.schema == CANDIDATE_SCHEMA

    out = retriever.retrieve(['u_target', 'cold_a', 'u_target'])
    assert out.schema == CANDIDATE_SCHEMA
    for sc in SCORE_COLUMNS:
        assert out[sc].dtype == pl.Float32
    for rk in RANK_COLUMNS:
        assert out[rk].dtype == pl.Int16
        assert out[rk].max() <= MISSING_RANK_SENTINEL

    # Sorted by (customer_id, article_id) & deduplicated
    assert out.unique(['customer_id', 'article_id']).height == out.height
    assert out.sort(['customer_id', 'article_id']).equals(out)

    u_rows = out.filter(pl.col('customer_id') == 'u_target')
    assert u_rows.height > 200  # proves no 200-item unique_prefix cap


# ---------------------------------------------------------------------------
# 6. LatentContentIndex unit tests
# ---------------------------------------------------------------------------

def test_latent_fold_in_query_and_exclusions():
    cutoff = date(2020, 9, 9)
    rows = []
    for u in ('u1', 'u2', 'u3'):
        rows.extend([('2020-09-05', u, '000'), ('2020-09-05', u, '001')])
    for u in ('v1', 'v2', 'v3'):
        rows.extend([('2020-09-05', u, '002'), ('2020-09-05', u, '003')])
    tx = pl.DataFrame(rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row').with_columns(
        pl.col('t_dat').str.to_date()
    )
    catalog = pl.DataFrame({
        'article_id': ['000', '001', '002', '003'],
        'product_type_name': ['Hat', 'Hat', 'Shoe', 'Shoe'],
        'detail_desc': ['knit wool beanie', 'wool beanie cap', 'running shoe', 'trail shoe'],
    })
    idx = LatentContentIndex.fit(tx, catalog, cutoff, k=4)
    assert np.all(np.diff(idx.svd_sigma) <= 1e-6)  # descending singular values

    # Unit norm & scale invariance
    u_a = idx.fold_in_user([0], [1.0])
    u_b = idx.fold_in_user([0], [5.0])
    assert float(np.linalg.norm(u_a)) == pytest.approx(1.0, rel=1e-5)
    assert np.allclose(u_a, u_b, atol=1e-5)

    # Degenerate weights -> zero vector
    assert np.all(idx.fold_in_user([], []) == 0.0)
    assert np.all(idx.fold_in_user([0, 999], [0.0, 1.0]) == 0.0)

    # Exclusions & tie-breaking
    lat_idx, lat_sc = idx.query_latent(u_a, top_k=3, exclude_indices=[0])
    assert 0 not in lat_idx.tolist()
    assert lat_idx[0] == 1
    assert lat_sc[0] > 0.0

    con_idx, con_sc = idx.query_content([0], [1.0], top_k=3, exclude_indices=[0])
    assert 0 not in con_idx.tolist()
    assert con_idx[0] == 1
    assert con_sc[0] > 0.0


def test_latent_batch_parity_with_single_queries():
    from scipy import sparse

    cutoff = date(2020, 9, 9)
    rows = [
        ('2020-09-01', f'u{u}', f'{i:03}')
        for u in range(10)
        for i in ((u % 3), (u % 3) + 3, (u % 3) + 6)
    ]
    tx = pl.DataFrame(rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row').with_columns(
        pl.col('t_dat').str.to_date()
    )
    catalog = pl.DataFrame({
        'article_id': [f'{i:03}' for i in range(12)],
        'product_type_name': ['A', 'B', 'C'] * 4,
        'detail_desc': [f'item {i % 3} fabric' for i in range(12)],
    })
    idx = LatentContentIndex.fit(tx, catalog, cutoff, k=6)

    q_csr = sparse.csr_matrix(
        (
            np.array([1.0, 0.5, 0.8, 0.0, 1.2, 0.3], dtype=np.float32),
            (np.array([0, 0, 1, 2, 3, 3]), np.array([0, 3, 2, 1, 4, 7])),
        ),
        shape=(4, 12),
        dtype=np.float32,
    )
    b_lat_i, b_lat_s = idx.batch_query_latent(q_csr, top_k=5, exclude_purchased=True, batch_size=2)
    b_con_i, b_con_s = idx.batch_query_content(q_csr, top_k=5, exclude_purchased=True, batch_size=2)

    for r in range(4):
        sub = q_csr[r]
        cols = sub.indices
        vals = sub.data
        u_vec = idx.fold_in_user(cols, vals)
        s_li, s_ls = idx.query_latent(u_vec, top_k=5, exclude_indices=cols)
        valid_l = b_lat_i[r] >= 0
        assert np.array_equal(b_lat_i[r][valid_l], s_li)
        assert np.allclose(b_lat_s[r][valid_l], s_ls, atol=1e-5)

        s_ci, s_cs = idx.query_content(cols, vals, top_k=5, exclude_indices=cols)
        valid_c = b_con_i[r] >= 0
        assert np.array_equal(b_con_i[r][valid_c], s_ci)
        assert np.allclose(b_con_s[r][valid_c], s_cs, atol=1e-5)


def test_latent_save_load_roundtrip_and_missing_sigma_fallback(tmp_path):
    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 1), date(2020, 9, 2), date(2020, 9, 3), date(2020, 9, 4)],
        'customer_id': ['u1', 'u1', 'u2', 'u2'],
        'article_id': ['001', '002', '002', '003'],
    })
    catalog = pl.DataFrame({
        'article_id': ['001', '002', '003'],
        'product_type_name': ['Tee', 'Tee', 'Jeans'],
        'detail_desc': ['soft cotton tee', 'blue cotton tee', 'denim jeans'],
    })
    idx = LatentContentIndex.fit(tx, catalog, cutoff, k=64)
    assert idx.k == 1  # clamped: min(2, 3) - 1 == 1

    saved = idx.save(tmp_path)
    loaded = LatentContentIndex.load(tmp_path)
    assert np.allclose(loaded.svd_v, idx.svd_v)
    assert np.allclose(loaded.svd_sigma, idx.svd_sigma)
    assert np.allclose(loaded.content_tfidf.toarray(), idx.content_tfidf.toarray())
    u1 = idx.fold_in_user([0], [1.0])
    u2 = loaded.fold_in_user([0], [1.0])
    assert np.allclose(u1, u2)

    # Absent svd_sigma.npy falls back to ones cleanly
    saved['svd_sigma'].unlink()
    loaded_no_sigma = LatentContentIndex.load(tmp_path, article_ids=['001', '002', '003'])
    assert np.allclose(loaded_no_sigma.svd_sigma, np.ones(idx.k, dtype=np.float32))


def test_latent_degenerate_small_matrices():
    cutoff = date(2020, 9, 9)
    tx_1 = pl.DataFrame({
        't_dat': [date(2020, 9, 1)],
        'customer_id': ['u1'],
        'article_id': ['001'],
    })
    cat_1 = pl.DataFrame({'article_id': ['001'], 'detail_desc': [None]})
    idx_1 = LatentContentIndex.fit(tx_1, cat_1, cutoff, k=64)
    assert idx_1.k == 0
    assert idx_1.vocab_size == 0
    assert idx_1.fold_in_user([0], [1.0]).shape == (0,)
    li, ls = idx_1.query_latent(np.zeros((0,), dtype=np.float32), top_k=5)
    ci, cs = idx_1.query_content([0], [1.0], top_k=5)
    assert li.size == 0 and ls.size == 0 and ci.size == 0 and cs.size == 0


# ---------------------------------------------------------------------------
# 7. MultiCandidateRetriever export/load & retrieve_online (WS-3 hooks)
# ---------------------------------------------------------------------------

def test_export_and_load_artifacts_roundtrip(tmp_path):
    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 5), date(2020, 9, 5), date(2020, 9, 6), date(2020, 9, 7)],
        'customer_id': ['u1', 'u1', 'u2', 'u2'],
        'article_id': ['001', '002', '001', '003'],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003', '004'],
        'product_code': ['P1', 'P2', 'P2', 'P1'],
        'index_group_name': ['Ladieswear', 'Ladieswear', 'Ladieswear', 'Ladieswear'],
        'detail_desc': ['silk blouse', 'cotton skirt', 'linen skirt', 'silk shirt'],
    })
    customers = pl.DataFrame({'customer_id': ['u1', 'u2'], 'age': ['28', '40']})
    # Ensure 004 is in catalog before cutoff
    tx = pl.concat([tx, pl.DataFrame({'t_dat': [date(2020, 8, 1)], 'customer_id': ['u3'], 'article_id': ['004']})])

    retriever = MultiCandidateRetriever.fit_frames(tx, articles, customers, cutoff=cutoff)
    paths = retriever.export_artifacts(tmp_path)
    for key in ('cooc', 'segment_popularity', 'svd_v', 'svd_sigma', 'content_tfidf'):
        assert paths[key].is_file()

    loaded = MultiCandidateRetriever.load_artifacts(tmp_path)
    assert loaded.retrieve(['u1', 'cold']).equals(retriever.retrieve(['u1', 'cold']))
    assert loaded.retrieve_online('u1', top_k=4).equals(retriever.retrieve_online('u1', top_k=4))


def test_retrieve_online_live_event_weights():
    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 5), date(2020, 9, 5), date(2020, 9, 6), date(2020, 9, 6)],
        'customer_id': ['b1', 'b1', 'b2', 'b2'],
        'article_id': ['001', '002', '003', '004'],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003', '004'],
        'product_code': ['P1', 'P1', 'P2', 'P2'],
        'index_group_name': ['Ladieswear'] * 4,
        'detail_desc': ['red dress', 'blue dress', 'black jeans', 'grey jeans'],
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff)

    res_view = retriever.retrieve_online('cold', live_events=[('001', 'view')])
    res_click = retriever.retrieve_online('cold', live_events=[('001', 'click')])
    res_purchase = retriever.retrieve_online('cold', live_events=[('001', 'purchase')])

    # View/click do NOT populate history_decay (purchases only), whereas live purchase does
    assert res_view.filter(pl.col('article_id') == '001')['score_history_decay'].item() == 0.0
    assert res_click.filter(pl.col('article_id') == '001')['score_history_decay'].item() == 0.0
    assert res_purchase.filter(pl.col('article_id') == '001')['score_history_decay'].item() == pytest.approx(1.0)

    # Variant score on 002 scales strictly with live weight purchase (1.0) > click (0.55) > view (0.25)
    v_view = res_view.filter(pl.col('article_id') == '002')['score_variant'].item()
    v_click = res_click.filter(pl.col('article_id') == '002')['score_variant'].item()
    v_purch = res_purchase.filter(pl.col('article_id') == '002')['score_variant'].item()
    assert v_purch > v_click > v_view > 0.0
    assert v_click / v_view == pytest.approx(0.55 / 0.25, rel=1e-5)

    with pytest.raises(ValueError):
        retriever.retrieve_online('cold', live_events=[('001', 'unknown_kind')])


def test_retrieve_online_cold_start_preferences():
    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 7)] * 4,
        'customer_id': ['y1', 'y2', 'o1', 'o2'],
        'article_id': ['001', '001', '002', '002'],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002'],
        'index_group_name': ['Divided', 'Menswear'],
    })
    customers = pl.DataFrame({
        'customer_id': ['y1', 'y2', 'o1', 'o2'],
        'age': ['20', '22', '60', '65'],
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, customers, cutoff=cutoff)
    out = retriever.retrieve_online(
        'brand_new',
        preferences={'age_bin': '55+', 'index_group_name': 'Menswear'},
        top_k=1,
    )
    assert out['article_id'].to_list() == ['002']
    assert out['primary_reason'].to_list() == ['popular_for_segment']
    assert out['score_seg_pop'].item() > 0.0


def test_retrieve_online_balanced_vs_discovery_and_eligible_filter():
    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 7), date(2020, 9, 7), date(2020, 9, 7), date(2020, 9, 7)],
        'customer_id': ['u1', 'u2', 'u2', 'u2'],
        'article_id': ['001', '002', '003', '004'],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003', '004'],
        'product_code': ['P1', 'P1', 'P1', 'P2'],
        'index_group_name': ['Ladieswear'] * 4,
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff)

    bal = retriever.retrieve_online('u1', mode='balanced')
    disc = retriever.retrieve_online('u1', mode='discovery')

    # Balanced keeps purchased '001' and multiple P1 variants ('002', '003')
    assert '001' in bal['article_id'].to_list()
    assert {'002', '003'}.issubset(set(bal['article_id'].to_list()))

    # Discovery excludes purchased '001' and keeps at most 1 candidate per product_code P1
    disc_ids = disc['article_id'].to_list()
    assert '001' not in disc_ids
    assert len(set(disc_ids) & {'002', '003'}) == 1
    assert '004' in disc_ids

    # eligible_articles filters before top_k
    filtered = retriever.retrieve_online('u1', mode='balanced', eligible_articles={'003', '004'}, top_k=1)
    assert filtered.height == 1
    assert filtered['article_id'].item() in {'003', '004'}


# ---------------------------------------------------------------------------
# 8. Regression tests for Iteration 2 determinism, null & validation fixes
# ---------------------------------------------------------------------------

def test_pref_ig_deterministic_under_tied_groups():
    cutoff = date(2020, 9, 9)
    # 16 items per index_group_name (32 total) on identical date -> exact tie between Ladieswear and Menswear
    rows = [('2020-09-05', 'u0', f'{i:03}') for i in range(32)]
    tx = (
        pl.DataFrame(rows, schema=['t_dat', 'customer_id', 'article_id'], orient='row')
        .with_columns(pl.col('t_dat').str.to_date())
    )
    articles = pl.DataFrame({
        'article_id': [f'{i:03}' for i in range(32)],
        'index_group_name': ['Ladieswear' if i % 2 == 0 else 'Menswear' for i in range(32)],
    })
    observed = set()
    for _ in range(15):
        r = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff)
        ig_code = (r._user_packed['u0'] >> 4) & 0xFFFF
        observed.add(r._ig_list[ig_code])
    assert observed == {'Ladieswear'}


def test_whitespace_age_and_numeric_dtypes_consistency():
    raw_strings = [None, '', '   ', 'NaN', 'nan', 'inf', '-inf', ' 25 ', '9.9', '10.0', '110.0', '110.1', 'abc']
    df = pl.DataFrame({'age': raw_strings})
    bins = df.select(age_bin_expr('age'))['age_bin'].to_list()
    scalar_bins = [_bin_age_scalar(v) for v in raw_strings]
    assert bins == scalar_bins
    assert bins[raw_strings.index(' 25 ')] == '25-34'

    # Null, Int64, Float64 column dtypes all bin cleanly
    assert pl.DataFrame({'age': pl.Series([None], dtype=pl.Null)}).select(age_bin_expr('age'))['age_bin'].item() == 'unknown'
    assert pl.DataFrame({'age': pl.Series([25], dtype=pl.Int64)}).select(age_bin_expr('age'))['age_bin'].item() == '25-34'
    assert pl.DataFrame({'age': pl.Series([25.0, float('nan'), float('inf'), float('-inf')], dtype=pl.Float64)}).select(
        age_bin_expr('age')
    )['age_bin'].to_list() == ['25-34', 'unknown', 'unknown', 'unknown']


def test_bool_rejected_in_config_and_top_k_overrides():
    int_fields = (
        'missing_rank_sentinel',
        'history_limit',
        'variant_limit',
        'cooc_limit',
        'latent_limit',
        'content_limit',
        'segment_pop_limit',
        'global_pop_limit',
        'history_days',
        'variant_pop_days',
        'variant_per_code_cap',
        'cooc_days',
        'cooc_basket_cap',
        'cooc_out_degree',
        'cooc_block_nodes',
        'latent_dim',
        'latent_window_days',
        'content_max_vocab',
        'pop_days',
        'user_batch_size',
    )
    for field_name in int_fields:
        for bad in (True, False, 0, -1):
            with pytest.raises(ValueError):
                RetrievalConfig(**{field_name: bad})  # type: ignore[arg-type]

    for field_name in ('history_tau_short', 'history_tau_long', 'cooc_half_life_days', 'latent_half_life_days'):
        for bad_f in (True, False, 0.0, -1.0, float('nan'), float('inf')):
            with pytest.raises(ValueError):
                RetrievalConfig(**{field_name: bad_f})  # type: ignore[arg-type]

    for field_name in ('history_short_weight', 'history_long_weight', 'cooc_bm25_k1', 'cooc_bm25_b'):
        for bad_f in (True, False, -0.1, float('nan'), float('inf')):
            with pytest.raises(ValueError):
                RetrievalConfig(**{field_name: bad_f})  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        RetrievalConfig(cooc_bm25_b=1.1)

    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({'t_dat': [date(2020, 9, 5)], 'customer_id': ['u1'], 'article_id': ['001']})
    articles = pl.DataFrame({'article_id': ['001']})
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff)

    with pytest.raises(ValueError):
        retriever.retrieve(['u1'], top_k_per_generator={'history_decay': True})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        retriever.retrieve(['u1'], top_k_per_generator={'history_decay': False})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        retriever.retrieve_online('u1', top_k=True)  # type: ignore[arg-type]


def test_retrieve_online_live_event_explicit_weight_none():
    cutoff = date(2020, 9, 9)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 5), date(2020, 9, 5)],
        'customer_id': ['b1', 'b1'],
        'article_id': ['001', '002'],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002'],
        'product_code': ['P1', 'P1'],
        'index_group_name': ['Ladieswear', 'Ladieswear'],
        'detail_desc': ['red dress', 'blue dress'],
    })
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, cutoff=cutoff)
    res_none = retriever.retrieve_online('cold', live_events=[{'article_id': '001', 'kind': 'click', 'weight': None}])
    res_omit = retriever.retrieve_online('cold', live_events=[{'article_id': '001', 'kind': 'click'}])
    assert res_none.equals(res_omit)


def test_null_customer_id_filtered_cleanly_without_warnings():
    cutoff = date(2020, 9, 9)
    tx_clean = pl.DataFrame({
        't_dat': [date(2020, 9, 5), date(2020, 9, 5), date(2020, 9, 6), date(2020, 9, 6)],
        'customer_id': ['u1', 'u1', 'u2', 'u2'],
        'article_id': ['001', '002', '002', '003'],
    })
    tx_with_nulls = pl.concat([
        tx_clean,
        pl.DataFrame({
            't_dat': [date(2020, 9, 7), date(2020, 9, 7)],
            'customer_id': [None, None],
            'article_id': ['001', '003'],
        }, schema={'t_dat': pl.Date, 'customer_id': pl.String, 'article_id': pl.String}),
    ])
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003'],
        'product_code': ['P1', 'P1', 'P2'],
        'detail_desc': ['red top', 'blue top', 'black pants'],
    })

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        idx_clean = LatentContentIndex.fit(tx_clean, articles, cutoff, k=4)
        idx_nulls = LatentContentIndex.fit(tx_with_nulls, articles, cutoff, k=4)
        retriever = MultiCandidateRetriever.fit_frames(tx_with_nulls, articles, cutoff=cutoff)

    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert not runtime_warnings
    assert np.allclose(idx_clean.svd_v, idx_nulls.svd_v)
    assert np.allclose(idx_clean.svd_sigma, idx_nulls.svd_sigma)
    assert None not in retriever._user_packed
    out = retriever.retrieve(['u1', 'u2'])
    assert out.schema == CANDIDATE_SCHEMA


def _build_and_compare_shuffled_parquet_determinism(
    tmp_path,
    *,
    tag: str,
    n_users: int,
    n_articles: int,
    latent_dim: int,
) -> None:
    cutoff = date(2020, 9, 9)

    articles_df = pl.DataFrame({
        'article_id': [f'{i:010d}' for i in range(n_articles)],
        'product_code': [f'pc_{i // 3:04d}' for i in range(n_articles)],
        'product_type_name': [['Dress', 'Shirt', 'Trousers', 'Sweater'][i % 4] for i in range(n_articles)],
        'product_group_name': ['Garment'] * n_articles,
        'colour_group_name': [['Black', 'White', 'Blue', 'Red'][i % 4] for i in range(n_articles)],
        'index_group_name': [['Ladieswear', 'Menswear', 'Divided'][(i // 2) % 3] for i in range(n_articles)],
        'detail_desc': [f'Tailored garment {i % 7} style {i % 5}' for i in range(n_articles)],
    })
    customers_df = pl.DataFrame({
        'customer_id': [f'cust_{u:05d}' for u in range(n_users)],
        'age': [str(20 + (u % 45)) for u in range(n_users)],
    })

    tx_rows: list[tuple[date, str, str]] = []
    # Guarantee every article appears before cutoff and include d=48 single-tx rows
    # (which trigger Polars Float32.exp() SIMD vs scalar-tail divergence) plus multi-date rows.
    for i in range(n_articles):
        u_idx = i % n_users
        days_ago = 48 if i % 5 == 0 else (1 + (i % 150))
        tx_rows.append((cutoff - timedelta(days=days_ago), f'cust_{u_idx:05d}', f'{i:010d}'))

    for u in range(n_users):
        cid = f'cust_{u:05d}'
        # Single transaction at days=48 for select users
        a_48 = f'{(u * 7 + 3) % n_articles:010d}'
        tx_rows.append((cutoff - timedelta(days=48), cid, a_48))
        # Multi-transaction pair across >= 4 distinct dates + duplicate same-day row
        a_multi = f'{(u * 5 + 1) % n_articles:010d}'
        for d_off in (2, 17, 48, 93, 141, 17):
            tx_rows.append((cutoff - timedelta(days=d_off), cid, a_multi))
        # Co-purchase basket items within 90d
        for step in range(3):
            a_bask = f'{(u * 3 + step * 11) % n_articles:010d}'
            tx_rows.append((cutoff - timedelta(days=1 + (u + step) % 12), cid, a_bask))

    base_tx = pl.DataFrame(tx_rows, schema={'t_dat': pl.Date, 'customer_id': pl.String, 'article_id': pl.String}, orient='row')
    future_tx = pl.DataFrame(
        [
            (cutoff, 'cust_00000', '0000000000'),
            (cutoff + timedelta(days=3), f'cust_{n_users - 1:05d}', f'{n_articles - 1:010d}'),
        ],
        schema={'t_dat': pl.Date, 'customer_id': pl.String, 'article_id': pl.String},
        orient='row',
    )

    cfg = RetrievalConfig(
        latent_dim=latent_dim,
        history_limit=20,
        variant_limit=15,
        cooc_limit=20,
        latent_limit=15,
        content_limit=15,
        segment_pop_limit=20,
        global_pop_limit=20,
        user_batch_size=max(16, n_users // 4),
    )

    def _fit_variant(variant_name: str, tx_in: pl.DataFrame, *, shuffle_seed: int | None, rg_size: int) -> MultiCandidateRetriever:
        v_dir = tmp_path / f'{tag}_{variant_name}'
        v_dir.mkdir(parents=True, exist_ok=True)
        art_out = articles_df
        cust_out = customers_df
        tx_out = tx_in
        if shuffle_seed is not None:
            s_rng = np.random.default_rng(shuffle_seed)
            tx_out = tx_out[s_rng.permutation(tx_out.height).tolist()]
            art_out = art_out[s_rng.permutation(art_out.height).tolist()]
            cust_out = cust_out[s_rng.permutation(cust_out.height).tolist()]
        tx_p = v_dir / 'transactions.parquet'
        art_p = v_dir / 'articles.parquet'
        cust_p = v_dir / 'customers.parquet'
        pq.write_table(tx_out.to_arrow(), tx_p, compression='zstd', row_group_size=rg_size)
        pq.write_table(art_out.to_arrow(), art_p, compression='zstd', row_group_size=max(13, rg_size // 2))
        pq.write_table(cust_out.to_arrow(), cust_p, compression='zstd', row_group_size=max(13, rg_size // 2))
        assert pq.ParquetFile(tx_p).metadata.num_row_groups >= 2
        return MultiCandidateRetriever.fit_frames(
            pl.scan_parquet(tx_p),
            pl.scan_parquet(art_p),
            pl.scan_parquet(cust_p),
            cutoff=cutoff,
            config=cfg,
        )

    baseline = _fit_variant('clean', base_tx, shuffle_seed=None, rg_size=131)
    sample_users = [f'cust_{u:05d}' for u in range(0, n_users, max(1, n_users // 10))]
    base_cands = baseline.retrieve(sample_users)

    variants = [
        ('future', pl.concat([base_tx, future_tx]), 101, 97),
        ('shuf_11', base_tx, 11, 89),
        ('shuf_77', base_tx, 77, 73),
        ('shuf_999', base_tx, 999, 61),
    ]
    for name, tx_var, seed, rg_size in variants:
        cand_ret = _fit_variant(name, tx_var, shuffle_seed=seed, rg_size=rg_size)
        assert np.array_equal(cand_ret.user_history_csr.indptr, baseline.user_history_csr.indptr), name
        assert np.array_equal(cand_ret.user_history_csr.indices, baseline.user_history_csr.indices), name
        assert np.array_equal(cand_ret.user_history_csr.data, baseline.user_history_csr.data), name
        assert np.array_equal(cand_ret.cooc_matrix.indptr, baseline.cooc_matrix.indptr), name
        assert np.array_equal(cand_ret.cooc_matrix.indices, baseline.cooc_matrix.indices), name
        assert np.array_equal(cand_ret.cooc_matrix.data, baseline.cooc_matrix.data), name
        assert np.array_equal(cand_ret.variant_matrix.indptr, baseline.variant_matrix.indptr), name
        assert np.array_equal(cand_ret.variant_matrix.indices, baseline.variant_matrix.indices), name
        assert np.array_equal(cand_ret.variant_matrix.data, baseline.variant_matrix.data), name
        assert np.array_equal(cand_ret.latent_index.svd_v, baseline.latent_index.svd_v), name
        assert np.array_equal(cand_ret.latent_index.svd_sigma, baseline.latent_index.svd_sigma), name
        assert cand_ret.seg_pop_df.equals(baseline.seg_pop_df), name
        assert cand_ret.pop7d_df.equals(baseline.pop7d_df), name
        assert cand_ret.retrieve(sample_users).equals(base_cands), name


def test_multi_rowgroup_shuffled_parquet_bitwise_determinism(tmp_path):
    # Dense SVD path: 60 * 50 = 3,000 <= _DENSE_SVD_MAX_ELEMENTS (262,144)
    _build_and_compare_shuffled_parquet_determinism(
        tmp_path, tag='dense', n_users=60, n_articles=50, latent_dim=16,
    )
    # Sparse svds path: 600 * 500 = 300,000 > _DENSE_SVD_MAX_ELEMENTS (262,144)
    _build_and_compare_shuffled_parquet_determinism(
        tmp_path, tag='sparse_svds', n_users=600, n_articles=500, latent_dim=16,
    )
