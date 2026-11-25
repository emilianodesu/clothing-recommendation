from datetime import date, datetime
import math
import subprocess
import sys

import numpy as np
import polars as pl
import pytest

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json
from hm_recommender.v2.features import (
    AFFINITY_ATTR_FIELDS,
    CATALOG_COLUMNS,
    V2_FEATURE_NAMES,
    V2_FEATURE_VERSION,
    V2FeatureContext,
)
from hm_recommender.v2.retrieval import (
    CANDIDATE_SCHEMA,
    MISSING_RANK_SENTINEL,
    MultiCandidateRetriever,
    RANK_COLUMNS,
    SCORE_COLUMNS,
)


def _write_synthetic_dataset(
    root,
    tx_df: pl.DataFrame,
    articles_df: pl.DataFrame,
    customers_df: pl.DataFrame | None = None,
) -> DatasetManifest:
    root.mkdir(parents=True, exist_ok=True)
    partitions = [
        write_partition(tx_df.to_arrow(), root, 'transactions/part.parquet'),
        write_partition(articles_df.to_arrow(), root, 'articles/part.parquet'),
    ]
    counts = {'transactions': tx_df.height, 'articles': articles_df.height}
    if customers_df is not None:
        partitions.append(write_partition(customers_df.to_arrow(), root, 'customers/part.parquet'))
        counts['customers'] = customers_df.height

    ds = DatasetManifest(
        dataset_id='v2-feat-fixture',
        source_sha256='b' * 64,
        min_date=date(2020, 1, 1),
        max_date=date(2020, 9, 22),
        counts=counts,
        partitions=partitions,
    )
    ds._root = root
    atomic_json(root / 'manifest.json', ds.model_dump(mode='json'))
    return ds


def _make_candidates(
    rows: list[tuple[str, str]],
    *,
    scores: dict[tuple[str, str], dict[str, float]] | None = None,
    ranks: dict[tuple[str, str], dict[str, int]] | None = None,
    extra_cols: dict[str, list[object]] | None = None,
) -> pl.DataFrame:
    if not rows:
        schema = dict(CANDIDATE_SCHEMA)
        if extra_cols:
            for k in extra_cols:
                schema[k] = pl.String
        return pl.DataFrame(schema=schema)

    data: dict[str, pl.Series] = {
        'customer_id': pl.Series('customer_id', [r[0] for r in rows], dtype=pl.String),
        'article_id': pl.Series('article_id', [r[1] for r in rows], dtype=pl.String),
    }
    for s_col in SCORE_COLUMNS:
        vals = [
            float((scores or {}).get(r, {}).get(s_col, 0.0))
            for r in rows
        ]
        data[s_col] = pl.Series(s_col, vals, dtype=pl.Float32)
    for r_col in RANK_COLUMNS:
        vals = [
            int((ranks or {}).get(r, {}).get(r_col, MISSING_RANK_SENTINEL))
            for r in rows
        ]
        data[r_col] = pl.Series(r_col, vals, dtype=pl.Int16)
    if extra_cols:
        for k, v in extra_cols.items():
            data[k] = pl.Series(k, v)
    return pl.DataFrame(data)


# F1: Import isolation
def test_v2_features_import_isolation():
    code = (
        'import sys; '
        'import hm_recommender.v2; '
        'from hm_recommender.v2.features import V2_FEATURE_NAMES, V2FeatureContext; '
        'bad = {"sklearn", "torch", "lightgbm", "hm_recommender.ranking.history"} & set(sys.modules); '
        'assert not bad, f"Forbidden modules imported: {bad}"; '
        'assert len(V2_FEATURE_NAMES) == 61; '
        'print("FEATURES_ISOLATED_OK")'
    )
    out = subprocess.check_output([sys.executable, '-c', code], text=True)
    assert out.strip() == 'FEATURES_ISOLATED_OK'


# F2: Strict < cutoff & cutoff type validation
def test_cutoff_strict_lt_and_type_validation():
    cutoff = date(2020, 9, 9)
    past_tx = pl.DataFrame({
        't_dat': [date(2020, 9, 1), date(2020, 9, 8)],
        'customer_id': ['u1', 'u1'],
        'article_id': ['001', '002'],
        'price': [10.0, 20.0],
        'sales_channel_id': [1, 2],
    })
    future_tx = pl.DataFrame({
        't_dat': [date(2020, 9, 9), date(2020, 9, 10)],  # == cutoff and > cutoff
        'customer_id': ['u1', 'u2'],
        'article_id': ['999', '001'],
        'price': [99.0, 50.0],
        'sales_channel_id': [2, 2],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '999'],
        'product_code': ['p1', 'p1', 'p9'],
        'product_type_name': ['Dress', 'Dress', 'Coat'],
    })
    customers = pl.DataFrame({
        'customer_id': ['u1', 'u2'],
        'age': ['30', '40'],
        'club_member_status': ['ACTIVE', 'ACTIVE'],
        'fashion_news_frequency': ['Regularly', 'NONE'],
        'FN': ['1.0', None],
        'Active': ['1.0', None],
    })

    for bad in (datetime(2020, 9, 9, 0, 0), '2020-09-09', 20200909):
        with pytest.raises(ValueError):
            V2FeatureContext.from_frames(past_tx, articles, customers, cutoff=bad)  # type: ignore[arg-type]

    ctx_past = V2FeatureContext.from_frames(past_tx, articles, customers, cutoff=cutoff)
    ctx_with_future = V2FeatureContext.from_frames(pl.concat([past_tx, future_tx]), articles, customers, cutoff=cutoff)

    assert '999' not in ctx_with_future.catalog['article_id'].to_list()
    assert ctx_past.catalog.equals(ctx_with_future.catalog)
    assert ctx_past.item_stats.equals(ctx_with_future.item_stats)
    assert ctx_past.user_stats.equals(ctx_with_future.user_stats)
    assert ctx_past.user_affinities.equals(ctx_with_future.user_affinities)
    assert ctx_past.histories.equals(ctx_with_future.histories)

    cands = _make_candidates([('u1', '001'), ('u1', '002'), ('u2', '001')])
    assert ctx_past.transform(cands).equals(ctx_with_future.transform(cands))


# F3: Exact feature values, schema, dtypes & finiteness
def test_exact_feature_values_schema_dtypes_and_finiteness():
    assert V2_FEATURE_VERSION == 2
    assert len(V2_FEATURE_NAMES) == 61
    assert 'user_active_flag' in V2_FEATURE_NAMES

    cutoff = date(2020, 9, 10)
    # u1 buys 001 twice (Sept 8 @ 10.0 online, Aug 20 @ 20.0 offline) and 002 once (Sept 1 @ 30.0 online)
    # u2 buys 001 once (Sept 5 @ 12.0 online)
    tx = pl.DataFrame({
        't_dat': [
            date(2020, 9, 8),
            date(2020, 8, 20),
            date(2020, 9, 1),
            date(2020, 9, 5),
        ],
        'customer_id': ['u1', 'u1', 'u1', 'u2'],
        'article_id': ['001', '001', '002', '001'],
        'price': [10.0, 20.0, 30.0, 12.0],
        'sales_channel_id': [2, 1, 2, 2],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002'],
        'product_code': ['PC1', 'PC1'],
        'prod_name': ['Item 1', 'Item 2'],
        'product_type_name': ['Dress', 'Top'],
        'product_group_name': ['Garment', 'Garment'],
        'colour_group_name': ['Black', 'Blue'],
        'graphical_appearance_name': ['Solid', 'Stripe'],
        'index_group_name': ['Ladieswear', 'Ladieswear'],
        'department_no': ['100', '200'],
        'garment_group_name': ['Jersey', 'Knit'],
        'detail_desc': ['Desc 1', 'Desc 2'],
    })
    customers = pl.DataFrame({
        'customer_id': ['u1', 'u2'],
        'age': ['20', '40'],
        'club_member_status': ['ACTIVE', 'PRE-CREATE'],
        'fashion_news_frequency': ['Regularly', 'Monthly'],
        'FN': ['1.0', None],
        'Active': ['1.0', '0.0'],
    })

    ctx = V2FeatureContext.from_frames(tx, articles, customers, cutoff=cutoff)
    cands = _make_candidates(
        [('u1', '001'), ('u1', '002')],
        scores={
            ('u1', '001'): {'score_latent': 0.5, 'score_cooc': 0.4, 'score_pop7d': 1.1},
            ('u1', '002'): {'score_pop7d': 0.7},
        },
        ranks={
            ('u1', '001'): {'rank_latent': 1, 'rank_cooc': 2, 'rank_pop7d': 1},
            ('u1', '002'): {'rank_pop7d': 3},
        },
    )
    out = ctx.transform(cands)
    assert out.columns == ['customer_id', 'article_id', *V2_FEATURE_NAMES]
    for col in V2_FEATURE_NAMES:
        assert out.schema[col] == pl.Float32
    mat = out.select(V2_FEATURE_NAMES).to_numpy()
    assert np.isfinite(mat).all()

    row0 = out.row(0, named=True)
    # User profile & activity for u1
    assert row0['user_age'] == pytest.approx(20.0)
    assert row0['user_age_missing'] == pytest.approx(0.0)
    assert row0['user_club_active'] == pytest.approx(1.0)
    assert row0['user_news_freq_code'] == pytest.approx(2.0)
    assert row0['user_fn_active'] == pytest.approx(1.0)
    assert row0['user_active_flag'] == pytest.approx(1.0)
    assert row0['user_count7'] == pytest.approx(1.0)     # Sept 8 (>= Sept 3)
    assert row0['user_count28'] == pytest.approx(3.0)
    assert row0['user_count90'] == pytest.approx(3.0)
    assert row0['user_count180'] == pytest.approx(3.0)
    assert row0['user_recency'] == pytest.approx(2.0)    # 2020-09-10 - 2020-09-08
    assert row0['user_avg_price'] == pytest.approx(20.0)  # mean(10, 20, 30)
    assert row0['user_std_price'] == pytest.approx(math.sqrt(200.0 / 3.0), rel=1e-5)
    assert row0['user_online_ratio'] == pytest.approx(2.0 / 3.0, rel=1e-5)
    assert row0['user_n_unique_prodcodes'] == pytest.approx(1.0)

    # Item 001 stats: bought Sept 8 (price 10), Sept 5 (price 12), Aug 20 (price 20)
    # Latest date for 001 is Sept 8 -> item_price = 10.0
    assert row0['item_price'] == pytest.approx(10.0)
    assert row0['item_count7'] == pytest.approx(2.0)   # Sept 8, Sept 5
    assert row0['item_count14'] == pytest.approx(2.0)
    assert row0['item_count28'] == pytest.approx(3.0)
    assert row0['item_count90'] == pytest.approx(3.0)
    assert row0['item_trend_7_28'] == pytest.approx(2.0 / (3.0 / 4.0 + 1.0), rel=1e-5)
    assert row0['item_trend_14_90'] == pytest.approx(2.0 / (3.0 / (90.0 / 14.0) + 1.0), rel=1e-5)
    assert row0['item_online_ratio'] == pytest.approx(2.0 / 3.0, rel=1e-5)
    # Buyers of 001: u1 (20), u1 (20), u2 (40) -> mean = 80/3, std(ddof=0) = sqrt(((20-80/3)^2*2 + (40-80/3)^2)/3)
    exp_mean_age = 80.0 / 3.0
    exp_std_age = math.sqrt((2 * (20.0 - exp_mean_age) ** 2 + (40.0 - exp_mean_age) ** 2) / 3.0)
    assert row0['item_mean_age'] == pytest.approx(exp_mean_age, rel=1e-5)
    assert row0['item_std_age'] == pytest.approx(exp_std_age, rel=1e-5)
    # 2 buyers of 001 in 90d: u1 (2 purchases), u2 (1 purchase) -> repurchase_rate = 0.5
    assert row0['item_repurchase_rate'] == pytest.approx(0.5)
    # first_seen of 001 = 2020-08-20 -> 21 days before 2020-09-10
    assert row0['item_days_since_first_seen'] == pytest.approx(21.0)

    # Pair (u1, 001)
    assert row0['pair_count7'] == pytest.approx(1.0)
    assert row0['pair_count28'] == pytest.approx(2.0)
    assert row0['pair_count90'] == pytest.approx(2.0)
    assert row0['pair_count180'] == pytest.approx(2.0)
    assert row0['pair_recency'] == pytest.approx(2.0)
    exp_decay = (0.6 * math.exp(-2.0 / 14.0) + 0.4 * math.exp(-2.0 / 60.0)) + (
        0.6 * math.exp(-21.0 / 14.0) + 0.4 * math.exp(-21.0 / 60.0)
    )
    assert row0['pair_decay_score'] == pytest.approx(exp_decay, rel=1e-5)
    # PC1 bought 3 times by u1 in 90d (001 x2, 002 x1), latest Sept 8 -> recency = 2.0
    assert row0['prodcode_count90'] == pytest.approx(3.0)
    assert row0['prodcode_recency'] == pytest.approx(2.0)

    # Cross & affinities
    assert row0['abs_age_diff'] == pytest.approx(abs(20.0 - exp_mean_age), rel=1e-5)
    assert row0['price_ratio'] == pytest.approx(10.0 / 20.0, rel=1e-5)
    assert row0['channel_affinity'] == pytest.approx(1.0 - abs(2.0 / 3.0 - 2.0 / 3.0), rel=1e-5)
    assert row0['aff_product_type'] == pytest.approx(2.0 / 3.0, rel=1e-5)
    assert row0['aff_product_group'] == pytest.approx(1.0, rel=1e-5)
    assert row0['aff_colour_group'] == pytest.approx(2.0 / 3.0, rel=1e-5)
    assert row0['aff_department'] == pytest.approx(2.0 / 3.0, rel=1e-5)
    assert row0['aff_index_group'] == pytest.approx(1.0, rel=1e-5)
    assert row0['aff_graphical_appearance'] == pytest.approx(2.0 / 3.0, rel=1e-5)

    # Stage 1 signals
    assert row0['n_generators_hit'] == pytest.approx(3.0)
    assert row0['min_gen_rank'] == pytest.approx(1.0)
    assert row0['latent_x_cooc'] == pytest.approx(0.2, rel=1e-5)


# F4: Empty, single-row, unsorted/interleaved customer order, extra columns ignored
def test_empty_single_row_and_unsorted_duplicate_customer_order():
    cutoff = date(2020, 9, 10)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 8), date(2020, 9, 7)],
        'customer_id': ['u1', 'u2'],
        'article_id': ['001', '002'],
    })
    articles = pl.DataFrame({'article_id': ['001', '002']})
    ctx = V2FeatureContext.from_frames(tx, articles, None, cutoff=cutoff)

    empty_cands = _make_candidates([], extra_cols={'primary_reason': []})
    empty_out = ctx.transform(empty_cands)
    assert empty_out.is_empty()
    assert empty_out.columns == ['customer_id', 'article_id', *V2_FEATURE_NAMES]
    for col in V2_FEATURE_NAMES:
        assert empty_out.schema[col] == pl.Float32

    single_cands = _make_candidates([('u1', '001')], extra_cols={'primary_reason': ['recent_purchase']})
    single_out = ctx.transform(single_cands)
    assert single_out.height == 1
    assert single_out.columns == ['customer_id', 'article_id', *V2_FEATURE_NAMES]
    assert np.isfinite(single_out.select(V2_FEATURE_NAMES).to_numpy()).all()

    interleaved = _make_candidates(
        [('u2', '001'), ('u1', '002'), ('u2', '002'), ('cold', '001')],
        extra_cols={'primary_reason': ['popular_7d', 'recent_purchase', 'popular_7d', 'popular_7d']},
    )
    out = ctx.transform(interleaved)
    assert out['customer_id'].to_list() == ['u2', 'u1', 'u2', 'cold']
    assert out['article_id'].to_list() == ['001', '002', '002', '001']
    assert 'primary_reason' not in out.columns
    assert out.filter(pl.col('customer_id') == 'cold')['user_count180'].to_list() == [0.0]


# F5: Age parsing variants and bounds across String, Int64, Float64, Null
def test_age_parsing_variants_and_bounds():
    cutoff = date(2020, 9, 10)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 8)],
        'customer_id': ['u_tx'],
        'article_id': ['001'],
    })
    articles = pl.DataFrame({'article_id': ['001']})

    raw_ages = ['25', ' 25 ', 'unknown', '', 'NaN', 'inf', '-inf', None, '9.9', '10.0', '110.0', '110.1']
    cids = [f'c{i}' for i in range(len(raw_ages))]
    cust_str = pl.DataFrame({'customer_id': cids, 'age': pl.Series('age', raw_ages, dtype=pl.String)})
    ctx_str = V2FeatureContext.from_frames(tx, articles, cust_str, cutoff=cutoff)

    cands = _make_candidates([(c, '001') for c in cids])
    out = ctx_str.transform(cands)
    ages = out['user_age'].to_list()
    missing = out['user_age_missing'].to_list()
    assert ages[0] == pytest.approx(25.0) and missing[0] == 0.0
    assert ages[1] == pytest.approx(25.0) and missing[1] == 0.0
    for idx in (2, 3, 4, 5, 6, 7, 8, 11):
        assert ages[idx] == pytest.approx(-1.0)
        assert missing[idx] == 1.0
    assert ages[9] == pytest.approx(10.0) and missing[9] == 0.0
    assert ages[10] == pytest.approx(110.0) and missing[10] == 0.0

    # Float64 with nan/inf and Int64 with null
    cust_flt = pl.DataFrame({
        'customer_id': ['f1', 'f2', 'f3', 'f4'],
        'age': pl.Series('age', [25.0, float('nan'), float('inf'), 9.5], dtype=pl.Float64),
    })
    ctx_flt = V2FeatureContext.from_frames(tx, articles, cust_flt, cutoff=cutoff)
    out_flt = ctx_flt.transform(_make_candidates([(c, '001') for c in ['f1', 'f2', 'f3', 'f4']]))
    assert out_flt['user_age'].to_list() == [25.0, -1.0, -1.0, -1.0]
    assert out_flt['user_age_missing'].to_list() == [0.0, 1.0, 1.0, 1.0]

    cust_int = pl.DataFrame({
        'customer_id': ['i1', 'i2'],
        'age': pl.Series('age', [34, None], dtype=pl.Int64),
    })
    ctx_int = V2FeatureContext.from_frames(tx, articles, cust_int, cutoff=cutoff)
    out_int = ctx_int.transform(_make_candidates([('i1', '001'), ('i2', '001')]))
    assert out_int['user_age'].to_list() == [34.0, -1.0]
    assert out_int['user_age_missing'].to_list() == [0.0, 1.0]


# F6: Customer flags FN, Active, club_member_status, fashion_news_frequency variants & missing customers
def test_customer_flags_fn_active_club_news_variants():
    cutoff = date(2020, 9, 10)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 8)],
        'customer_id': ['c0'],
        'article_id': ['001'],
    })
    articles = pl.DataFrame({'article_id': ['001']})

    customers = pl.DataFrame({
        'customer_id': ['c0', 'c1', 'c2', 'c3', 'c4', 'c5'],
        'FN': ['1.0', '0', '0.0', '1', None, 'true'],
        'Active': ['1.0', '0.0', '1', None, 'NaN', 'yes'],
        'club_member_status': ['ACTIVE', ' active ', 'PRE-CREATE', 'LEFT CLUB', None, 'ACTIVE'],
        'fashion_news_frequency': ['Regularly', ' regularLy ', 'Monthly', 'NONE', 'None', None],
    })
    ctx = V2FeatureContext.from_frames(tx, articles, customers, cutoff=cutoff)
    out = ctx.transform(_make_candidates([(f'c{i}', '001') for i in range(6)]))

    assert out['user_fn_active'].to_list() == [1.0, 0.0, 0.0, 1.0, 0.0, 1.0]
    assert out['user_active_flag'].to_list() == [1.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    assert out['user_club_active'].to_list() == [1.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    assert out['user_news_freq_code'].to_list() == [2.0, 2.0, 1.0, 0.0, 0.0, 0.0]

    # Numeric 1.0/0.0 flags and missing customers=None
    cust_num = pl.DataFrame({
        'customer_id': ['c0', 'c1'],
        'FN': [1.0, 0.0],
        'Active': [0.0, 1.0],
    })
    ctx_num = V2FeatureContext.from_frames(tx, articles, cust_num, cutoff=cutoff)
    out_num = ctx_num.transform(_make_candidates([('c0', '001'), ('c1', '001')]))
    assert out_num['user_fn_active'].to_list() == [1.0, 0.0]
    assert out_num['user_active_flag'].to_list() == [0.0, 1.0]

    ctx_none = V2FeatureContext.from_frames(tx, articles, None, cutoff=cutoff)
    out_none = ctx_none.transform(_make_candidates([('c0', '001'), ('unknown', '001')]))
    assert np.isfinite(out_none.select(V2_FEATURE_NAMES).to_numpy()).all()
    assert out_none['user_fn_active'].to_list() == [0.0, 0.0]
    assert out_none['user_active_flag'].to_list() == [0.0, 0.0]


# F7: Missing optional article & tx columns, duplicate keys, None vs '', empty 180d window
def test_missing_optional_article_and_tx_columns():
    cutoff = date(2020, 9, 10)
    # Transactions without price & sales_channel_id, and all transactions > 180d before cutoff
    old_tx = pl.DataFrame({
        't_dat': [date(2020, 1, 15), date(2020, 1, 16)],
        'customer_id': ['u1', 'u2'],
        'article_id': ['001', '002'],
    })
    # Minimal articles with duplicate article_id & None vs ''
    articles = pl.DataFrame({
        'article_id': ['001', '001', '002'],
        'product_type_name': [None, 'IgnoredDup', ''],
    })
    customers = pl.DataFrame({
        'customer_id': ['u1', 'u1', 'u2'],
        'age': ['28', '99', None],
    })

    ctx = V2FeatureContext.from_frames(old_tx, articles, customers, cutoff=cutoff)
    assert ctx.catalog.height == 2
    assert ctx.user_stats.height == 2
    assert ctx.histories.is_empty()
    assert ctx.user_affinities.is_empty()

    cands = _make_candidates([('u1', '001'), ('u2', '002')])
    out = ctx.transform(cands)
    assert out.height == 2
    assert np.isfinite(out.select(V2_FEATURE_NAMES).to_numpy()).all()
    # Neutral defaults for missing price & channel & empty 180d
    assert out['item_price'].to_list() == [0.0, 0.0]
    assert out['item_online_ratio'].to_list() == [0.5, 0.5]
    assert out['user_online_ratio'].to_list() == [0.5, 0.5]
    assert out['price_ratio'].to_list() == [1.0, 1.0]
    assert out['user_count180'].to_list() == [0.0, 0.0]
    assert out['aff_product_type'].to_list() == [0.0, 0.0]


# F8: from_tables vs from_frames vs from_dataset exact parity
def test_from_tables_vs_from_frames_exact_parity(tmp_path):
    cutoff = date(2020, 9, 10)
    tx = pl.DataFrame({
        't_dat': [
            date(2020, 9, 8),
            date(2020, 9, 2),
            date(2020, 8, 15),
            date(2020, 7, 20),
        ],
        'customer_id': ['u1', 'u1', 'u2', 'u2'],
        'article_id': ['001', '002', '002', '003'],
        'price': [0.05, 0.08, 0.07, 0.04],
        'sales_channel_id': [2, 1, 2, 1],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003'],
        'product_code': ['P1', 'P1', 'P2'],
        'product_type_name': ['Dress', 'Dress', 'Pants'],
        'product_group_name': ['Garment', 'Garment', 'Garment'],
        'colour_group_name': ['Black', 'Red', 'Blue'],
        'index_group_name': ['Ladieswear', 'Ladieswear', 'Menswear'],
        'department_no': ['10', '10', '20'],
        'graphical_appearance_name': ['Solid', 'Solid', 'Check'],
    })
    customers = pl.DataFrame({
        'customer_id': ['u1', 'u2', 'u_cold'],
        'age': ['26', '42', '31'],
        'club_member_status': ['ACTIVE', 'ACTIVE', 'PRE-CREATE'],
        'fashion_news_frequency': ['Regularly', 'NONE', 'Monthly'],
        'FN': ['1.0', None, '1.0'],
        'Active': ['1.0', None, None],
    })
    ds = _write_synthetic_dataset(tmp_path / 'ds', tx, articles, customers)

    ctx_frames = V2FeatureContext.from_frames(tx, articles, customers, cutoff=cutoff)
    ctx_ds = V2FeatureContext.from_dataset(ds, cutoff=cutoff)
    ctx_tables = V2FeatureContext.from_tables(
        catalog=ctx_frames.catalog,
        item_stats=ctx_frames.item_stats,
        user_stats=ctx_frames.user_stats,
        user_affinities=ctx_frames.user_affinities,
        histories=ctx_frames.histories,
    )
    # Multi-chunk user_affinities & duplicate item_stats should still reconstruct identically
    aff_chunked = pl.concat([ctx_frames.user_affinities.slice(0, 1), ctx_frames.user_affinities.slice(1)], rechunk=False)
    dup_item_stats = pl.concat([ctx_frames.item_stats, ctx_frames.item_stats.head(1)], rechunk=False)
    ctx_chunked = V2FeatureContext.from_tables(
        catalog=ctx_frames.catalog,
        item_stats=dup_item_stats,
        user_stats=ctx_frames.user_stats,
        user_affinities=aff_chunked,
        histories=ctx_frames.histories,
    )
    ctx_tables_rebuilt_aff = V2FeatureContext.from_tables(
        catalog=ctx_frames.catalog,
        item_stats=ctx_frames.item_stats,
        user_stats=ctx_frames.user_stats,
        user_affinities=None,
        histories=ctx_frames.histories,
        retain_dataframes=False,
    )
    assert ctx_tables_rebuilt_aff.histories.is_empty()
    assert ctx_tables_rebuilt_aff.user_affinities.is_empty()

    cands = _make_candidates([
        ('u1', '001'), ('u1', '002'), ('u1', '003'),
        ('u2', '001'), ('u2', '003'),
        ('u_cold', '001'), ('unseen', '002'),
    ])
    m_frames = ctx_frames.transform(cands).select(V2_FEATURE_NAMES).to_numpy()
    m_ds = ctx_ds.transform(cands).select(V2_FEATURE_NAMES).to_numpy()
    m_tables = ctx_tables.transform(cands).select(V2_FEATURE_NAMES).to_numpy()
    m_chunked = ctx_chunked.transform(cands).select(V2_FEATURE_NAMES).to_numpy()
    m_rebuilt = ctx_tables_rebuilt_aff.transform(cands).select(V2_FEATURE_NAMES).to_numpy()

    np.testing.assert_allclose(m_ds, m_frames, atol=1e-6)
    np.testing.assert_allclose(m_tables, m_frames, atol=1e-6)
    np.testing.assert_allclose(m_chunked, m_frames, atol=1e-6)
    np.testing.assert_allclose(m_rebuilt, m_frames, atol=1e-6)

    for cid in ('u1', 'u2', 'u_cold', 'unseen'):
        sub = cands.filter(pl.col('customer_id') == cid)
        x_f, c_f = ctx_frames.transform_online(sub, customer_id=cid)
        x_t, c_t = ctx_tables_rebuilt_aff.transform_online(sub, customer_id=cid)
        assert c_f == c_t
        np.testing.assert_allclose(x_f, x_t, atol=1e-6)

    # Single-chunk sliced/filtered user_affinities (head, tail, slice, filter) must match un-sliced copies
    sliced_variants = (
        ('u1', ctx_frames.user_affinities.head(1)),
        ('u2', ctx_frames.user_affinities.tail(1)),
        ('u2', ctx_frames.user_affinities.slice(1, 1)),
        ('u2', ctx_frames.user_affinities.filter(pl.col('customer_id') == 'u2')),
    )
    for target_cid, aff_sliced in sliced_variants:
        assert aff_sliced.n_chunks() == 1
        aff_copied = pl.DataFrame(aff_sliced.to_dict(as_series=False), schema=aff_sliced.schema)
        ctx_sliced = V2FeatureContext.from_tables(
            catalog=ctx_frames.catalog,
            item_stats=ctx_frames.item_stats,
            user_stats=ctx_frames.user_stats,
            user_affinities=aff_sliced,
            histories=ctx_frames.histories,
        )
        ctx_copied = V2FeatureContext.from_tables(
            catalog=ctx_frames.catalog,
            item_stats=ctx_frames.item_stats,
            user_stats=ctx_frames.user_stats,
            user_affinities=aff_copied,
            histories=ctx_frames.histories,
        )
        np.testing.assert_allclose(
            ctx_sliced.transform(cands).select(V2_FEATURE_NAMES).to_numpy(),
            ctx_copied.transform(cands).select(V2_FEATURE_NAMES).to_numpy(),
            atol=1e-6,
        )
        sub_target = cands.filter(pl.col('customer_id') == target_cid)
        np.testing.assert_allclose(
            ctx_sliced.transform(sub_target).select(V2_FEATURE_NAMES).to_numpy(),
            ctx_frames.transform(sub_target).select(V2_FEATURE_NAMES).to_numpy(),
            atol=1e-6,
        )
        x_s, c_s = ctx_sliced.transform_online(sub_target, customer_id=target_cid)
        x_f, c_f = ctx_frames.transform_online(sub_target, customer_id=target_cid)
        assert c_s == c_f
        np.testing.assert_allclose(x_s, x_f, atol=1e-6)


# F10: Middle-sliced user_affinities and invalid list lengths / null list validation
def test_from_tables_middle_slice_and_invalid_user_affinities():
    cutoff = date(2020, 9, 10)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 8), date(2020, 9, 7), date(2020, 9, 6)],
        'customer_id': ['u1', 'u2', 'u3'],
        'article_id': ['001', '002', '003'],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003'],
        'product_type_name': ['Dress', 'Top', 'Pants'],
        'colour_group_name': ['Black', 'Red', 'Blue'],
    })
    ctx = V2FeatureContext.from_frames(tx, articles, None, cutoff=cutoff)
    assert ctx.user_affinities.height == 3

    # Middle slice (offsets[0] > 0 and offsets[-1] < len(values)) and empty slice
    mid_slice = ctx.user_affinities.slice(1, 1)
    assert mid_slice.n_chunks() == 1
    mid_copy = pl.DataFrame(mid_slice.to_dict(as_series=False), schema=mid_slice.schema)
    ctx_mid = V2FeatureContext.from_tables(
        catalog=ctx.catalog,
        item_stats=ctx.item_stats,
        user_stats=ctx.user_stats,
        user_affinities=mid_slice,
        histories=ctx.histories,
    )
    ctx_mid_copy = V2FeatureContext.from_tables(
        catalog=ctx.catalog,
        item_stats=ctx.item_stats,
        user_stats=ctx.user_stats,
        user_affinities=mid_copy,
        histories=ctx.histories,
    )
    cands = _make_candidates([('u1', '001'), ('u2', '002'), ('u3', '003')])
    np.testing.assert_allclose(
        ctx_mid.transform(cands).select(V2_FEATURE_NAMES).to_numpy(),
        ctx_mid_copy.transform(cands).select(V2_FEATURE_NAMES).to_numpy(),
        atol=1e-6,
    )
    x_mid, _ = ctx_mid.transform_online(cands.filter(pl.col('customer_id') == 'u2'), customer_id='u2')
    x_orig, _ = ctx.transform_online(cands.filter(pl.col('customer_id') == 'u2'), customer_id='u2')
    np.testing.assert_allclose(x_mid, x_orig, atol=1e-6)

    # Empty slice (head(0))
    ctx_empty_aff = V2FeatureContext.from_tables(
        catalog=ctx.catalog,
        item_stats=ctx.item_stats,
        user_stats=ctx.user_stats,
        user_affinities=ctx.user_affinities.head(0),
        histories=ctx.histories,
    )
    assert np.isfinite(ctx_empty_aff.transform(cands).select(V2_FEATURE_NAMES).to_numpy()).all()

    # Mismatched attr_code / share list lengths raise ValueError
    bad_len = pl.DataFrame({
        'customer_id': ['u1'],
        'attr_code': pl.Series('attr_code', [[0, 1]], dtype=pl.List(pl.UInt16)),
        'share': pl.Series('share', [[0.5]], dtype=pl.List(pl.Float32)),
    })
    with pytest.raises(ValueError, match='match'):
        V2FeatureContext.from_tables(
            catalog=ctx.catalog,
            item_stats=ctx.item_stats,
            user_stats=ctx.user_stats,
            user_affinities=bad_len,
            histories=ctx.histories,
        )

    # Null list entry raises ValueError
    bad_null_list = pl.DataFrame({
        'customer_id': ['u1'],
        'attr_code': pl.Series('attr_code', [None], dtype=pl.List(pl.UInt16)),
        'share': pl.Series('share', [[0.5]], dtype=pl.List(pl.Float32)),
    })
    with pytest.raises(ValueError, match='null'):
        V2FeatureContext.from_tables(
            catalog=ctx.catalog,
            item_stats=ctx.item_stats,
            user_stats=ctx.user_stats,
            user_affinities=bad_null_list,
            histories=ctx.histories,
        )

    # Null element inside list raises ValueError
    bad_null_elem = pl.DataFrame({
        'customer_id': ['u1'],
        'attr_code': pl.Series('attr_code', [[0, None]], dtype=pl.List(pl.UInt16)),
        'share': pl.Series('share', [[0.5, 0.5]], dtype=pl.List(pl.Float32)),
    })
    with pytest.raises(ValueError, match='null'):
        V2FeatureContext.from_tables(
            catalog=ctx.catalog,
            item_stats=ctx.item_stats,
            user_stats=ctx.user_stats,
            user_affinities=bad_null_elem,
            histories=ctx.histories,
        )


# F9: transform_online parity, live_events weights, preferences, and cold-start flag
def test_transform_online_live_events_preferences_and_cold_start_flag():
    cutoff = date(2020, 9, 10)
    tx = pl.DataFrame({
        't_dat': [date(2020, 9, 5), date(2020, 9, 6), date(2020, 9, 4)],
        'customer_id': ['warm', 'other', 'other'],
        'article_id': ['001', '002', '003'],
        'price': [20.0, 40.0, 35.0],
        'sales_channel_id': [2, 1, 2],
    })
    articles = pl.DataFrame({
        'article_id': ['001', '002', '003'],
        'product_code': ['P1', 'P2', 'P2'],
        'product_type_name': ['Dress', 'Shoes', 'Shoes'],
        'index_group_name': ['Ladieswear', 'Ladieswear', 'Menswear'],
        'colour_group_name': ['Black', 'White', 'Blue'],
    })
    customers = pl.DataFrame({
        'customer_id': ['warm', 'cold'],
        'age': ['30', None],
    })
    ctx = V2FeatureContext.from_frames(tx, articles, customers, cutoff=cutoff)
    retriever = MultiCandidateRetriever.fit_frames(tx, articles, customers, cutoff=cutoff)

    # Exact parity with transform() when live_events=None and preferences=None (and empty live_events=[])
    cands_warm = retriever.retrieve_online('warm')
    expected_warm = ctx.transform(cands_warm).select(V2_FEATURE_NAMES).to_numpy()
    x_warm, is_cold_warm = ctx.transform_online(cands_warm, customer_id='warm')
    x_warm_empty, is_cold_empty = ctx.transform_online(cands_warm, customer_id='warm', live_events=[])
    assert is_cold_warm is False and is_cold_empty is False
    assert np.array_equal(x_warm, expected_warm)
    assert np.array_equal(x_warm_empty, expected_warm)

    # Cold user with preferences (no live_events): stays is_cold_start=True, fills age from age_bin
    cands_cold = retriever.retrieve_online('cold', preferences={'age_bin': '25-34', 'index_group_name': 'Ladieswear'})
    x_cold, is_cold_flag = ctx.transform_online(
        cands_cold,
        customer_id='cold',
        preferences={'age_bin': '25-34', 'index_group_name': 'Ladieswear'},
    )
    assert is_cold_flag is True
    age_idx = V2_FEATURE_NAMES.index('user_age')
    age_miss_idx = V2_FEATURE_NAMES.index('user_age_missing')
    assert x_cold[0, age_idx] == pytest.approx(29.5)
    assert x_cold[0, age_miss_idx] == pytest.approx(0.0)

    # Unknown-only article_id in live_events leaves is_cold_start=True
    x_unk, cold_unk = ctx.transform_online(
        cands_cold,
        customer_id='cold',
        live_events=[{'article_id': 'UNKNOWN_ID', 'kind': 'view', 'weight': None}],
    )
    assert cold_unk is True
    assert x_unk.shape == (cands_cold.height, len(V2_FEATURE_NAMES))

    # Invalid kind rejected
    with pytest.raises(ValueError, match='Unknown live event kind'):
        ctx.transform_online(cands_cold, customer_id='cold', live_events=[('002', 'share')])

    # Valid live_events flip cold-start to False and follow purchase (1.0) > click (0.55) > view (0.25)
    decay_idx = V2_FEATURE_NAMES.index('pair_decay_score')
    pc_cnt_idx = V2_FEATURE_NAMES.index('prodcode_count90')
    row_002 = cands_cold['article_id'].to_list().index('002')
    row_003 = cands_cold['article_id'].to_list().index('003')

    x_view, cold_view = ctx.transform_online(
        cands_cold, customer_id='cold', live_events=[{'article_id': '002', 'kind': 'view', 'weight': None}]
    )
    x_click, cold_click = ctx.transform_online(
        cands_cold, customer_id='cold', live_events=[('002', 'click')]
    )
    x_purch, cold_purch = ctx.transform_online(
        cands_cold, customer_id='cold', live_events=[('002', 'purchase')]
    )
    assert cold_view is False and cold_click is False and cold_purch is False
    assert x_purch[row_002, decay_idx] > x_click[row_002, decay_idx] > x_view[row_002, decay_idx] > 0.0
    assert x_purch[row_003, pc_cnt_idx] > x_click[row_003, pc_cnt_idx] > x_view[row_003, pc_cnt_idx] > 0.0

    # Warm user live event monotonically shifts attribute affinity (purchase > click > view)
    aff_colour_idx = V2_FEATURE_NAMES.index('aff_colour_group')
    row_002_warm = cands_warm['article_id'].to_list().index('002')
    xw_view, cw_v = ctx.transform_online(cands_warm, customer_id='warm', live_events=[('002', 'view')])
    xw_click, cw_c = ctx.transform_online(cands_warm, customer_id='warm', live_events=[('002', 'click')])
    xw_purch, cw_p = ctx.transform_online(cands_warm, customer_id='warm', live_events=[('002', 'purchase')])
    assert cw_v is False and cw_c is False and cw_p is False
    assert xw_purch[row_002_warm, aff_colour_idx] > xw_click[row_002_warm, aff_colour_idx] > xw_view[row_002_warm, aff_colour_idx] > 0.0
