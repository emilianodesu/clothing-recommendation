from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import polars as pl
import pytest

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json
from hm_recommender.model.metrics import ranking_metrics
from hm_recommender.ranking.history import rank_history
from hm_recommender.v2.evaluate import (
    QualityV2Config,
    compute_buyer_metrics,
    compute_user_segments,
    evaluate_fold,
    evaluate_hp_baselines_for_fold,
    evaluate_quality_gates,
    load_quality_v2_config,
    read_fold_labels,
    run_quality_v2,
)
from hm_recommender.v2.ranker import RankerConfig
from hm_recommender.v2.retrieval import RetrievalConfig


def _make_multiweek_dataset(root: Path, *, extra_future_rows: list[tuple[str, str, str, float, int]] | None = None) -> DatasetManifest:
    root.mkdir(parents=True, exist_ok=True)
    items = [f'a{i:03d}' for i in range(1, 21)]
    articles_df = pl.DataFrame({
        'article_id': items,
        'product_code': [f'pc{(i - 1) // 2:02d}' for i in range(1, 21)],
        'prod_name': [f'Prod {i}' for i in range(1, 21)],
        'product_type_name': ['Trousers' if i % 2 else 'T-shirt' for i in range(1, 21)],
        'product_group_name': ['Garment Lower body' if i % 2 else 'Garment Upper body' for i in range(1, 21)],
        'colour_group_name': ['Black' if i % 3 == 0 else 'Blue' for i in range(1, 21)],
        'graphical_appearance_name': ['Solid'] * 20,
        'index_group_name': ['Ladieswear' if i <= 10 else 'Menswear' for i in range(1, 21)],
        'department_no': ['101'] * 20,
        'garment_group_name': ['Jersey'] * 20,
        'detail_desc': [f'Cotton fashion garment {i}' for i in range(1, 21)],
    })
    customers_df = pl.DataFrame({
        'customer_id': ['u_recent1', 'u_recent2', 'u_recent3', 'u_mid', 'u_old', 'u_cold'],
        'age': ['26', '33', '41', '29', '52', '23'],
        'club_member_status': ['ACTIVE'] * 6,
        'fashion_news_frequency': ['Regularly'] * 6,
        'FN': ['1.0'] * 6,
        'Active': ['1.0'] * 6,
    })

    # Cover 5 training weeks before V1 (2020-08-26) + V1, V2, V3, TEST windows
    tx_rows: list[tuple[str, str, str, float, int]] = []
    # Older history (>90d before 2020-08-26)
    tx_rows.append(('2020-04-01', 'u_old', 'a001', 0.03, 2))
    tx_rows.append(('2020-04-05', 'u_old', 'a002', 0.03, 2))
    # Mid history (29-90d before 2020-08-26)
    tx_rows.append(('2020-07-01', 'u_mid', 'a003', 0.04, 1))
    tx_rows.append(('2020-07-05', 'u_mid', 'a004', 0.04, 1))

    base_start = date(2020, 7, 15)
    for w in range(10):
        d = (base_start + timedelta(days=7 * w)).isoformat()
        d2 = (base_start + timedelta(days=7 * w + 2)).isoformat()
        tx_rows.extend([
            (d, 'u_recent1', 'a001', 0.02, 2),
            (d2, 'u_recent1', 'a002', 0.02, 2),
            (d, 'u_recent2', 'a003', 0.03, 1),
            (d2, 'u_recent2', 'a005', 0.03, 2),
            (d, 'u_recent3', 'a011', 0.05, 2),
            (d2, 'u_recent3', 'a012', 0.05, 2),
        ])
    # Labels inside V1..V3 and TEST (plus u_cold purchase inside V1 & TEST)
    tx_rows.extend([
        ('2020-08-27', 'u_cold', 'a001', 0.02, 2),
        ('2020-08-28', 'u_mid', 'a003', 0.04, 1),
        ('2020-08-28', 'u_old', 'a006', 0.03, 2),
        ('2020-09-03', 'u_mid', 'a007', 0.04, 1),
        ('2020-09-10', 'u_mid', 'a008', 0.04, 1),
        ('2020-09-17', 'u_mid', 'a009', 0.04, 1),
    ])
    if extra_future_rows:
        tx_rows.extend(extra_future_rows)

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
        dataset_id='eval-fixture',
        source_sha256='c' * 64,
        min_date=date(2020, 4, 1),
        max_date=tx_df['t_dat'].max(),  # type: ignore[arg-type]
        counts={'transactions': tx_df.height, 'articles': articles_df.height, 'customers': customers_df.height},
        partitions=partitions,
    )
    ds._root = root
    atomic_json(root / 'manifest.json', ds.model_dump(mode='json'))
    return ds


def test_evaluate_import_isolation():
    code = (
        'import sys; '
        'import hm_recommender.v2; '
        'import hm_recommender.v2.evaluate; '
        'bad = {"lightgbm", "sklearn", "torch", "hm_recommender.ranking.history", "hm_recommender.quality.baselines"} & set(sys.modules); '
        'assert not bad, f"Forbidden modules imported: {bad}"; '
        'print("EVAL_ISOLATED_OK")'
    )
    out = subprocess.check_output([sys.executable, '-c', code], text=True)
    assert out.strip() == 'EVAL_ISOLATED_OK'


def test_quality_v2_config_schema():
    cfg_path = Path(__file__).resolve().parents[2] / 'configs/quality/quality-v2.json'
    if not cfg_path.is_file():
        cfg_path = Path('/opt/hmrec/configs/quality/quality-v2.json')
    cfg = load_quality_v2_config(cfg_path)
    assert cfg.protocol == 'quality-v2'
    assert cfg.seed == 42
    assert cfg.folds == {'V1': date(2020, 8, 26), 'V2': date(2020, 9, 2), 'V3': date(2020, 9, 9)}
    assert cfg.descriptive_test_window.cutoff == date(2020, 9, 16)
    assert cfg.descriptive_test_window.label_end == date(2020, 9, 23)
    assert cfg.gates['target_avg_map_at_12'] == pytest.approx(0.031)
    assert cfg.to_retrieval_config().latent_dim == 64
    assert cfg.to_ranker_config().weeks == 5


def test_compute_buyer_metrics_parity_and_validation():
    ranked = ['a', 'b', 'c', 'd', 'e']
    relevant = {'b', 'e', 'z'}
    ref = ranking_metrics(ranked, relevant)
    got = compute_buyer_metrics(ranked, relevant, ks=(200, 300))
    assert got['map_at_12'] == pytest.approx(ref['map_at_12'], abs=1e-12)
    assert got['ndcg_at_12'] == pytest.approx(ref['ndcg_at_12'], abs=1e-12)
    assert got['recall_at_200'] == pytest.approx(ref['recall_at_200'], abs=1e-12)
    assert got['recall_at_300'] == pytest.approx(ref['recall_at_200'], abs=1e-12)

    long_ranked = [f'item_{i}' for i in range(320)]
    long_relevant = {'item_10', 'item_250', 'item_310'}
    long_got = compute_buyer_metrics(long_ranked, long_relevant, ks=(200, 300))
    assert long_got['recall_at_200'] == pytest.approx(1.0 / 3.0, abs=1e-12)
    assert long_got['recall_at_300'] == pytest.approx(2.0 / 3.0, abs=1e-12)
    assert long_got['recall_at_300'] > long_got['recall_at_200']

    with pytest.raises(ValueError):
        compute_buyer_metrics(ranked, set())


def test_labels_segments_and_hp_baseline_parity(tmp_path):
    ds = _make_multiweek_dataset(tmp_path / 'ds')
    tx_lf = pl.scan_parquet(str(tmp_path / 'ds/transactions/part.parquet'))
    cutoff = date(2020, 8, 26)
    label_end = date(2020, 9, 2)

    buyers, truth = read_fold_labels(tx_lf, cutoff, label_end)
    assert set(buyers) >= {'u_cold', 'u_mid', 'u_old', 'u_recent1'}
    # u_mid bought a003 before cutoff -> repeat; u_old bought a006 only after cutoff -> discovery
    assert 'a003' in truth['u_mid'][1]
    assert 'a006' in truth['u_old'][0] and 'a006' not in truth['u_old'][1]

    segs = compute_user_segments(tx_lf, cutoff, ['u_recent1', 'u_mid', 'u_old', 'u_cold'])
    assert segs == {
        'u_recent1': 'recent28d',
        'u_mid': 'history29_90d',
        'u_old': 'older_history',
        'u_cold': 'no_history',
    }

    # Verify evaluate_hp_baselines_for_fold matches rank_history exactly
    past = tx_lf.filter(pl.col('t_dat') < pl.lit(cutoff)).collect()
    catalog_set = set(past['article_id'].unique().to_list())
    pop_counts = (
        past.filter(pl.col('t_dat') >= pl.lit(cutoff - timedelta(days=7)))
        .group_by('article_id').len()
    )
    pop_list = (
        pl.DataFrame({'article_id': sorted(catalog_set)})
        .join(pop_counts, on='article_id', how='left')
        .with_columns(pl.col('len').fill_null(0))
        .sort(['len', 'article_id'], descending=[True, False])['article_id']
        .to_list()[:200]
    )
    hist_28 = (
        past.filter(pl.col('t_dat') >= pl.lit(cutoff - timedelta(days=28)))
        .group_by('customer_id', 'article_id')
        .agg(pl.col('t_dat').max().alias('lp'), pl.len().alias('freq'))
        .sort(['customer_id', 'lp', 'freq', 'article_id'], descending=[False, True, True, False])
    )
    expected_h_maps = []
    for b in buyers:
        u_hist = hist_28.filter(pl.col('customer_id') == b)['article_id'].to_list()
        ranked_h = rank_history(u_hist, pop_list, catalog_set)
        expected_h_maps.append(ranking_metrics(ranked_h, truth[b][0])['map_at_12'])

    reports, _ = evaluate_hp_baselines_for_fold(tx_lf, cutoff, buyers, truth, segs, catalog_set, ks=(200, 300))
    assert reports['H']['metrics']['map_at_12'] == pytest.approx(float(np.mean(expected_h_maps)), abs=1e-12)


def test_fold_strict_lt_cutoff_no_future_leakage(tmp_path):
    ds_clean = _make_multiweek_dataset(tmp_path / 'clean')
    ds_mutated = _make_multiweek_dataset(
        tmp_path / 'mutated',
        extra_future_rows=[
            ('2020-09-02', 'u_recent1', 'a019', 0.09, 2),
            ('2020-09-18', 'u_cold', 'a020', 0.09, 2),
        ],
    )
    r_cfg = RetrievalConfig(
        history_limit=8, variant_limit=6, cooc_limit=6, latent_dim=4,
        latent_limit=6, content_limit=6, segment_pop_limit=8, global_pop_limit=8,
    )
    rk_cfg = RankerConfig(
        weeks=4, val_weeks=1, negatives_per_group=6, hard_negatives_per_group=3,
        num_boost_round=6, early_stopping_rounds=3, num_leaves=7, min_data_in_leaf=1, num_threads=1,
    )
    rep_a = evaluate_fold(
        ds_clean, 'V1', date(2020, 8, 26),
        retrieval_config=r_cfg, ranker_config=rk_cfg, eval_batch_users=4,
    )
    rep_b = evaluate_fold(
        ds_mutated, 'V1', date(2020, 8, 26),
        retrieval_config=r_cfg, ranker_config=rk_cfg, eval_batch_users=4,
    )
    assert rep_a['v2']['metrics'] == rep_b['v2']['metrics']
    assert rep_a['baselines']['H']['metrics'] == rep_b['baselines']['H']['metrics']
    assert rep_a['cold_start_routing']['cold_users'] >= 1


def test_evaluate_quality_gates_pass_and_reject():
    base_cfg = QualityV2Config(
        folds={'V1': date(2020, 8, 26), 'V2': date(2020, 9, 2), 'V3': date(2020, 9, 9)},
        descriptive_test_window={'fold_id': 'TEST', 'cutoff': '2020-09-16', 'label_end': '2020-09-23'},
        gates={
            'target_avg_map_at_12': 0.031,
            'min_avg_map_over_h': 0.02222467,
            'min_avg_map_over_rg': 0.02544361,
            'min_test_map_over_h': 0.02628451,
            'max_segment_relative_loss': 0.02,
            'min_segment_users': 1000,
        },
        baselines_v1={
            'quality_v1_avg': {
                'H': {'map_at_12': 0.02222467, 'discovery_map_at_12': 0.00458949},
                'P': {'map_at_12': 0.00672156, 'discovery_map_at_12': 0.00643740},
                'RG': {'map_at_12': 0.02544361},
            },
            'folds': {},
        },
    )

    def _fold(v2_map: float, v2_disc: float, seg_no_hist: float, h_no_hist: float) -> dict:
        return {
            'v2': {
                'metrics': {'map_at_12': v2_map, 'ndcg_at_12': 0.04, 'recall_at_200': 0.21},
                'targets': {
                    'discovery': {'metrics': {'map_at_12': v2_disc}},
                    'repeat': {'metrics': {'map_at_12': 0.35}},
                },
                'segments': {
                    'recent28d': {'users': 30000, 'metrics': {'map_at_12': v2_map + 0.01}},
                    'history29_90d': {'users': 20000, 'metrics': {'map_at_12': 0.01}},
                    'older_history': {'users': 10000, 'metrics': {'map_at_12': 0.008}},
                    'no_history': {'users': 5000, 'metrics': {'map_at_12': seg_no_hist}},
                },
            },
            'baselines': {
                'H': {
                    'metrics': {'map_at_12': 0.022},
                    'targets': {'discovery': {'metrics': {'map_at_12': 0.0045}}},
                    'segments': {
                        'recent28d': {'users': 30000, 'metrics': {'map_at_12': 0.035}},
                        'history29_90d': {'users': 20000, 'metrics': {'map_at_12': 0.006}},
                        'older_history': {'users': 10000, 'metrics': {'map_at_12': 0.006}},
                        'no_history': {'users': 5000, 'metrics': {'map_at_12': h_no_hist}},
                    },
                },
                'P': {
                    'targets': {'discovery': {'metrics': {'map_at_12': 0.0064}}},
                },
            },
        }

    good_reports = {
        'V1': _fold(0.031, 0.009, 0.0050, 0.0050),
        'V2': _fold(0.032, 0.009, 0.0059, 0.0060),  # -1.67% <= 2%
        'V3': _fold(0.033, 0.009, 0.0064, 0.0064),
        'TEST': _fold(0.034, 0.010, 0.0079, 0.0079),
    }
    res_good = evaluate_quality_gates(good_reports, base_cfg)
    assert res_good['all_required_gates_passed'] is True
    assert res_good['checks']['reached_target_map'] is True

    # >2% regression in no_history on V2 fails gate
    bad_seg_reports = {**good_reports, 'V2': _fold(0.032, 0.009, 0.0055, 0.0060)}
    res_bad_seg = evaluate_quality_gates(bad_seg_reports, base_cfg)
    assert res_bad_seg['checks']['zero_segment_regressions'] is False
    assert res_bad_seg['all_required_gates_passed'] is False

    # Discovery MAP <= P fails gate
    bad_disc_reports = {
        'V1': _fold(0.031, 0.0060, 0.0050, 0.0050),
        'V2': _fold(0.032, 0.0060, 0.0060, 0.0060),
        'V3': _fold(0.033, 0.0060, 0.0064, 0.0064),
        'TEST': _fold(0.034, 0.010, 0.0079, 0.0079),
    }
    res_bad_disc = evaluate_quality_gates(bad_disc_reports, base_cfg)
    assert res_bad_disc['checks']['beat_p_discovery'] is False
    assert res_bad_disc['all_required_gates_passed'] is False


def test_run_quality_v2_subprocess_vs_inprocess_parity(tmp_path):
    ds = _make_multiweek_dataset(tmp_path / 'ds')
    cfg_payload = {
        'protocol': 'quality-v2',
        'seed': 42,
        'label_days': 7,
        'recall_ks': [200, 300],
        'min_segment_users': 1000,
        'segments': ['recent28d', 'history29_90d', 'older_history', 'no_history'],
        'folds': {'V1': '2020-08-26', 'V2': '2020-09-02', 'V3': '2020-09-09'},
        'descriptive_test_window': {'fold_id': 'TEST', 'cutoff': '2020-09-16', 'label_end': '2020-09-23'},
        'retrieval': {
            'history_limit': 8, 'variant_limit': 6, 'cooc_limit': 6, 'latent_dim': 4,
            'latent_limit': 6, 'content_limit': 6, 'segment_pop_limit': 8, 'global_pop_limit': 8,
        },
        'ranker': {
            'weeks': 4, 'val_weeks': 1, 'negatives_per_group': 6, 'hard_negatives_per_group': 3,
            'num_boost_round': 5, 'early_stopping_rounds': 3, 'num_leaves': 7, 'min_data_in_leaf': 1, 'num_threads': 1,
        },
        'gates': {'target_avg_map_at_12': 0.0, 'min_avg_map_over_h': 0.0, 'min_avg_map_over_rg': 0.0, 'min_test_map_over_h': 0.0},
        'baselines_v1': {},
        'budget': {'eval_batch_users': 4, 'bootstrap_resamples': 50},
    }
    cfg_path = tmp_path / 'quality-v2-test.json'
    cfg_path.write_text(json.dumps(cfg_payload))

    rep_sub = run_quality_v2(
        tmp_path / 'ds', cfg_path, tmp_path / 'out_sub',
        include_test=True, subprocess_per_fold=True,
    )
    rep_in = run_quality_v2(
        ds, cfg_path, tmp_path / 'out_in',
        include_test=True, subprocess_per_fold=False,
    )
    assert (tmp_path / 'out_sub/quality-v2-report.json').is_file()
    assert (tmp_path / 'out_sub/test-2020-09-16-report.json').is_file()
    assert rep_sub['summary_v1_v3']['v2']['map_at_12'] == pytest.approx(rep_in['summary_v1_v3']['v2']['map_at_12'], abs=1e-12)
    assert rep_sub['descriptive_test_2020_09_16']['v2']['metrics'] == rep_in['descriptive_test_2020_09_16']['v2']['metrics']
    assert rep_sub['summary_v1_v3']['bootstrap']['resamples'] == 50
