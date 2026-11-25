from datetime import date, datetime, timedelta
import math
import subprocess
import sys

import numpy as np
import polars as pl
import pytest

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json
from hm_recommender.model.metrics import ranking_metrics
from hm_recommender.v2.features import V2_FEATURE_NAMES, V2FeatureContext
from hm_recommender.v2.ranker import (
    RankerConfig,
    TrainedRankerResult,
    load_booster,
    predict_scores,
    project_ndcg_at_12,
    project_ndcg_evaluator,
    sample_group,
    score_candidates,
    train_sliding_ranker,
)
from hm_recommender.v2.retrieval import (
    CANDIDATE_SCHEMA,
    MISSING_RANK_SENTINEL,
    MultiCandidateRetriever,
    RANK_COLUMNS,
    RetrievalConfig,
    SCORE_COLUMNS,
)


def _build_multiweek_frames(cutoff: date, *, mutate_post_cutoff: bool = False):
    """Build synthetic multi-week dataset supporting 5 sliding weeks [T-35d, T)."""
    articles = pl.DataFrame({
        'article_id': [f'{i:03d}' for i in range(1, 13)],
        'product_code': [f'P{(i - 1) // 2}' for i in range(1, 13)],
        'product_type_name': ['Dress' if i % 2 else 'Top' for i in range(1, 13)],
        'product_group_name': ['Garment'] * 12,
        'colour_group_name': ['Black' if i % 3 == 0 else 'Blue' for i in range(1, 13)],
        'index_group_name': ['Ladieswear' if i <= 8 else 'Menswear' for i in range(1, 13)],
        'department_no': ['10' if i <= 6 else '20' for i in range(1, 13)],
        'graphical_appearance_name': ['Solid'] * 12,
        'detail_desc': [f'Stylish item {i}' for i in range(1, 13)],
    })
    customers = pl.DataFrame({
        'customer_id': [f'u{i}' for i in range(1, 9)] + ['cold_user'],
        'age': ['22', '28', '35', '42', '26', '31', '50', '24', '29'],
        'club_member_status': ['ACTIVE'] * 9,
        'fashion_news_frequency': ['Regularly'] * 8 + ['NONE'],
        'FN': ['1.0'] * 8 + [None],
        'Active': ['1.0'] * 8 + [None],
    })

    rows: list[tuple[date, str, str, float, int]] = []
    # History before T-35d so all u1..u8 are warm across all 5 sliding weeks
    pre_start = cutoff - timedelta(days=50)
    for u_idx in range(1, 9):
        a1 = f'{((u_idx - 1) % 6) + 1:03d}'
        a2 = f'{((u_idx) % 6) + 1:03d}'
        rows.append((pre_start + timedelta(days=u_idx), f'u{u_idx}', a1, 0.05 + 0.01 * u_idx, 2))
        rows.append((pre_start + timedelta(days=u_idx + 5), f'u{u_idx}', a2, 0.04 + 0.01 * u_idx, 1))
        # Seed catalog items 7..12 so all items exist before T-35d
        rows.append((pre_start + timedelta(days=1), f'u{u_idx}', f'{((u_idx - 1) % 6) + 7:03d}', 0.06, 2))

    # Weekly label & sliding history transactions across T-35d .. T-1d
    for dist in range(5, 0, -1):
        w_start = cutoff - timedelta(days=7 * dist)
        for u_idx in range(1, 9):
            fav = f'{((u_idx - 1) % 6) + 1:03d}'
            alt = f'{((u_idx + dist) % 6) + 1:03d}'
            rows.append((w_start + timedelta(days=1), f'u{u_idx}', fav, 0.05, 2))
            rows.append((w_start + timedelta(days=3), f'u{u_idx}', alt, 0.07, 1))

    if mutate_post_cutoff:
        for u_idx in range(1, 9):
            rows.append((cutoff, f'u{u_idx}', '012', 0.99, 2))
            rows.append((cutoff + timedelta(days=2), f'u{u_idx}', '011', 0.88, 1))

    tx = pl.DataFrame(
        rows,
        schema={'t_dat': pl.Date, 'customer_id': pl.String, 'article_id': pl.String, 'price': pl.Float64, 'sales_channel_id': pl.Int8},
        orient='row',
    )
    return tx, articles, customers


# R1: Lazy lightgbm import isolation
def test_ranker_lazy_lightgbm_import_isolation(tmp_path):
    code = (
        'import sys; '
        'import hm_recommender.v2; '
        'import hm_recommender.v2.ranker; '
        'bad = {"lightgbm", "sklearn", "torch", "hm_recommender.ranking.history"} & set(sys.modules); '
        'assert not bad, f"Forbidden modules loaded at import time: {bad}"; '
        'print("RANKER_LAZY_OK")'
    )
    out = subprocess.check_output([sys.executable, '-c', code], text=True)
    assert out.strip() == 'RANKER_LAZY_OK'


# R2: RankerConfig & cutoff validation
def test_ranker_config_and_cutoff_validation():
    cfg = RankerConfig()
    params = cfg.to_lgb_params()
    assert params['objective'] == 'lambdarank'
    assert params['device_type'] == 'cpu'
    assert params['seed'] == 42

    # Bool rejected for int/float fields
    for kwargs in (
        {'weeks': True},
        {'val_weeks': False},
        {'num_threads': True},
        {'learning_rate': True},
        {'feature_fraction': True},
        {'weeks': 1},
        {'weeks': 5, 'val_weeks': 5},
        {'negatives_per_group': 0},
        {'negatives_per_group': 10, 'hard_negatives_per_group': 11},
        {'num_leaves': 1},
        {'num_boost_round': 0},
        {'early_stopping_rounds': 0},
        {'learning_rate': 0.0},
        {'learning_rate': float('nan')},
        {'lambda_l2': float('inf')},
        {'lambda_l2': -0.1},
        {'feature_fraction': 0.0},
        {'feature_fraction': 1.1},
        {'bagging_fraction': 0.0},
        {'bagging_fraction': 1.5},
        {'min_data_in_leaf': 0},
        {'max_bin': 256},
        {'cold_start_col': ''},
        {'exclude_cold_start_train': 'yes'},
        {'label_gain': (0, True)},
        {'label_gain': (0, float('nan'))},
    ):
        with pytest.raises(ValueError):
            RankerConfig(**kwargs)

    tx = pl.DataFrame({'t_dat': [date(2020, 9, 1)], 'customer_id': ['u1'], 'article_id': ['001']})
    art = pl.DataFrame({'article_id': ['001']})
    for bad_cutoff in (datetime(2020, 9, 9, 0, 0), '2020-09-09', 20200909):
        with pytest.raises(ValueError):
            train_sliding_ranker((tx, art), cutoff=bad_cutoff)  # type: ignore[arg-type]


# R3: project_ndcg_at_12 exact parity, full relevant denominator, stable ties, validation
def test_project_ndcg_at_12_and_group_validation():
    # Group 1 (size 4): relevant=5 (2 missed by Stage 1), scores tied at 1.0 -> stable order wins
    # Group 2 (size 3): relevant=2
    scores = np.array([1.0, 1.0, 0.5, 0.1, 0.2, 0.9, 0.4], dtype=np.float64)
    labels = np.array([0, 1, 1, 0, 1, 0, 1], dtype=np.uint8)
    groups = np.array([4, 3], dtype=np.int32)
    relevant = np.array([5, 2], dtype=np.int32)

    got = project_ndcg_at_12(scores, labels, groups, relevant)

    # Compare against model/metrics.py ranking_metrics
    g1_order = ['a1', 'a2', 'a3', 'a4']  # stable tie-break: a1 before a2, then a3, a4
    g1_rel = {'a2', 'a3', 'missed1', 'missed2', 'missed3'}
    g2_order = ['b2', 'b3', 'b1']  # scores 0.9, 0.4, 0.2
    g2_rel = {'b1', 'b3'}
    expected = (
        ranking_metrics(g1_order, g1_rel)['ndcg_at_12']
        + ranking_metrics(g2_order, g2_rel)['ndcg_at_12']
    ) / 2.0
    assert got == pytest.approx(expected, abs=1e-12)

    # Equal-sized groups fast path parity
    eq_scores = np.array([0.8, 0.8, 0.1, 0.3, 0.9, 0.2], dtype=np.float64)
    eq_labels = np.array([1, 0, 0, 0, 1, 1], dtype=np.uint8)
    eq_groups = np.array([3, 3], dtype=np.int32)
    eq_rel = np.array([2, 3], dtype=np.int32)
    ev = project_ndcg_evaluator(eq_labels, eq_groups, eq_rel)
    name, val, higher_is_better = ev(eq_scores, None)
    assert name == 'project_ndcg_at_12' and higher_is_better is True
    assert val == pytest.approx(project_ndcg_at_12(eq_scores, eq_labels, eq_groups, eq_rel), abs=1e-12)

    # Validation errors
    with pytest.raises(ValueError):
        project_ndcg_at_12(eq_scores, eq_labels, np.array([3, 2]), eq_rel)
    with pytest.raises(ValueError):
        project_ndcg_at_12(eq_scores, eq_labels, np.array([0, 6]), eq_rel)
    with pytest.raises(ValueError):
        project_ndcg_at_12(eq_scores, eq_labels, eq_groups, np.array([0, 2]))
    with pytest.raises(ValueError):
        project_ndcg_at_12(eq_scores, np.array([0, 2, 0, 0, 1, 1]), eq_groups, eq_rel)
    with pytest.raises(ValueError):
        project_ndcg_at_12(np.array([0.1, np.nan, 0.2, 0.3, 0.4, 0.5]), eq_labels, eq_groups, eq_rel)


# R4: Negative sampling determinism, hard negatives, zero-pos/zero-neg skipping
def test_negative_sampling_determinism_and_zero_positive_groups():
    articles = np.array(['005', '001', '003', '002', '004', '006'])
    # Zero positives -> empty
    assert sample_group('2020-09-02', 'u1', articles, np.zeros(6, dtype=np.uint8)).size == 0
    # Zero negatives -> empty
    assert sample_group('2020-09-02', 'u1', articles, np.ones(6, dtype=np.uint8)).size == 0

    labels = np.array([0, 1, 0, 0, 0, 0], dtype=np.uint8)  # positive is '001'
    min_rank = np.array([10, 1, 2, 50, 3, 99], dtype=np.int16)
    max_score = np.array([0.1, 0.9, 0.8, 0.05, 0.7, 0.01], dtype=np.float32)

    sel = sample_group(
        '2020-09-02',
        'u1',
        articles,
        labels,
        min_gen_rank=min_rank,
        max_gen_score=max_score,
        negatives_per_group=3,
        hard_negatives_per_group=2,
        seed=42,
    )
    selected_arts = articles[sel].tolist()
    # Must keep positive '001', hard negatives '003' (rank 2) and '004' (rank 3), plus 1 hash negative, sorted ASC
    assert selected_arts == sorted(selected_arts)
    assert '001' in selected_arts
    assert '003' in selected_arts and '004' in selected_arts
    assert len(selected_arts) == 4

    # Permutation invariance & determinism
    perm = np.array([4, 2, 5, 0, 3, 1])
    sel_perm = sample_group(
        '2020-09-02',
        'u1',
        articles[perm],
        labels[perm],
        min_gen_rank=min_rank[perm],
        max_gen_score=max_score[perm],
        negatives_per_group=3,
        hard_negatives_per_group=2,
        seed=42,
    )
    assert articles[perm][sel_perm].tolist() == selected_arts


# R5: train_sliding_ranker 5 sliding weeks, early stopping, refit, cleanup, < cutoff isolation
def test_train_sliding_ranker_5_weeks_early_stop_and_refit(tmp_path):
    cutoff = date(2020, 9, 9)
    tx_clean, articles, customers = _build_multiweek_frames(cutoff, mutate_post_cutoff=False)
    tx_future, _, _ = _build_multiweek_frames(cutoff, mutate_post_cutoff=True)

    r_cfg = RetrievalConfig(
        history_limit=6,
        variant_limit=4,
        cooc_limit=4,
        latent_dim=4,
        latent_limit=4,
        content_limit=4,
        segment_pop_limit=6,
        global_pop_limit=6,
    )
    cfg = RankerConfig(
        weeks=5,
        val_weeks=1,
        negatives_per_group=6,
        hard_negatives_per_group=3,
        num_boost_round=10,
        early_stopping_rounds=4,
        num_leaves=7,
        min_data_in_leaf=1,
        num_threads=1,
        inference_num_threads=1,
        cleanup_work_files=True,
    )

    res_clean = train_sliding_ranker(
        (tx_clean, articles, customers),
        cutoff=cutoff,
        config=cfg,
        retrieval_config=r_cfg,
        work_dir=tmp_path / 'run_clean',
    )
    res_future = train_sliding_ranker(
        (tx_future, articles, customers),
        cutoff=cutoff,
        config=cfg,
        retrieval_config=r_cfg,
        work_dir=tmp_path / 'run_future',
    )

    assert isinstance(res_clean, TrainedRankerResult)
    assert res_clean.model_path.is_file()
    assert 1 <= res_clean.best_iteration <= cfg.num_boost_round
    assert res_clean.feature_names == V2_FEATURE_NAMES
    assert len(res_clean.week_stats) == 5
    assert [w['role'] for w in res_clean.week_stats] == [
        'inner_train', 'inner_train', 'inner_train', 'inner_train', 'validation'
    ]
    # Work files cleaned up except model.txt
    assert list((tmp_path / 'run_clean').glob('week-*')) == []
    assert list((tmp_path / 'run_clean').glob('*.features.f32')) == []

    # Strict < cutoff isolation: post-cutoff mutations do not alter trained model or metrics
    assert res_clean.best_iteration == res_future.best_iteration
    assert res_clean.val_ndcg_at_12 == pytest.approx(res_future.val_ndcg_at_12, abs=1e-12)
    assert res_clean.model_path.read_text() == res_future.model_path.read_text()


# R6: Cold-start routing and deterministic tie breaking
def test_cold_start_routing_and_deterministic_tie_breaking(tmp_path):
    cutoff = date(2020, 9, 9)
    tx, articles, customers = _build_multiweek_frames(cutoff)
    r_cfg = RetrievalConfig(
        history_limit=6, variant_limit=4, cooc_limit=4, latent_dim=4,
        latent_limit=4, content_limit=4, segment_pop_limit=6, global_pop_limit=6,
    )
    cfg = RankerConfig(
        weeks=3, val_weeks=1, negatives_per_group=6, hard_negatives_per_group=3,
        num_boost_round=5, early_stopping_rounds=3, num_leaves=7, min_data_in_leaf=1, num_threads=1,
    )
    res = train_sliding_ranker((tx, articles, customers), cutoff=cutoff, config=cfg, retrieval_config=r_cfg, work_dir=tmp_path / 'm')

    retriever = MultiCandidateRetriever.fit_frames(tx, articles, customers, cutoff=cutoff, config=r_cfg)
    feat_ctx = V2FeatureContext.from_frames(tx, articles, customers, cutoff=cutoff)

    # Empty input
    empty_cands = pl.DataFrame(schema={**CANDIDATE_SCHEMA, 'primary_reason': pl.String})
    empty_feats = feat_ctx.transform(empty_cands)
    empty_scored = score_candidates(empty_cands, empty_feats, booster=None)
    assert empty_scored.is_empty()
    assert empty_scored.columns == ['customer_id', 'article_id', 'score', 'rank', 'is_cold_start', 'score_source', 'primary_reason']

    # Pure cold-start routing with booster=None + deterministic tie-breaking on (rank_seg_pop, rank_pop7d, article_id)
    cold_cands = pl.DataFrame({
        'customer_id': ['cold_user', 'cold_user', 'cold_user', 'cold_user'],
        'article_id': ['004', '002', '003', '001'],
        **{c: pl.Series(c, [0.0] * 4, dtype=pl.Float32) for c in SCORE_COLUMNS},
        'rank_history_decay': pl.Series('rank_history_decay', [MISSING_RANK_SENTINEL] * 4, dtype=pl.Int16),
        'rank_variant': pl.Series('rank_variant', [MISSING_RANK_SENTINEL] * 4, dtype=pl.Int16),
        'rank_cooc': pl.Series('rank_cooc', [MISSING_RANK_SENTINEL] * 4, dtype=pl.Int16),
        'rank_latent': pl.Series('rank_latent', [MISSING_RANK_SENTINEL] * 4, dtype=pl.Int16),
        'rank_content': pl.Series('rank_content', [MISSING_RANK_SENTINEL] * 4, dtype=pl.Int16),
        'rank_seg_pop': pl.Series('rank_seg_pop', [2, 1, 1, 2], dtype=pl.Int16),
        'rank_pop7d': pl.Series('rank_pop7d', [5, 3, 3, 2], dtype=pl.Int16),
    })
    cold_feats = feat_ctx.transform(cold_cands)
    cold_scored = score_candidates(cold_cands, cold_feats, booster=None)
    # Order: (seg=1,pop=3,002), (seg=1,pop=3,003) [tie -> article_id ASC], (seg=2,pop=2,001), (seg=2,pop=5,004)
    assert cold_scored['article_id'].to_list() == ['002', '003', '001', '004']
    assert cold_scored['rank'].to_list() == [1, 2, 3, 4]
    assert cold_scored['is_cold_start'].to_list() == [True, True, True, True]
    assert cold_scored['score_source'].to_list() == ['segment_popularity'] * 4

    # Mixed warm + cold batch with real booster
    mixed_cands = retriever.retrieve(['u1', 'cold_user'])
    mixed_feats = feat_ctx.transform(mixed_cands)
    mixed_scored = score_candidates(mixed_cands, mixed_feats, booster=res.booster, num_threads=1)
    u1_rows = mixed_scored.filter(pl.col('customer_id') == 'u1')
    cold_rows = mixed_scored.filter(pl.col('customer_id') == 'cold_user')
    assert set(u1_rows['score_source'].to_list()) == {'lambdarank'}
    assert set(cold_rows['score_source'].to_list()) == {'segment_popularity'}

    # Explicit is_cold_start=False overrides user_count180 == 0 (for live session events)
    override_scored = score_candidates(cold_cands, cold_feats, booster=res.booster, is_cold_start=False, num_threads=1)
    assert set(override_scored['score_source'].to_list()) == {'lambdarank'}
    assert set(override_scored['is_cold_start'].to_list()) == {False}

    # Non-finite features rejected
    bad_x = np.zeros((2, len(V2_FEATURE_NAMES)), dtype=np.float32)
    bad_x[0, 0] = np.nan
    with pytest.raises(ValueError):
        predict_scores(bad_x, booster=res.booster, is_cold_start=False)


# R7: Booster save/load & predict_scores parity
def test_booster_save_load_and_predict_parity(tmp_path):
    cutoff = date(2020, 9, 9)
    tx, articles, customers = _build_multiweek_frames(cutoff)
    r_cfg = RetrievalConfig(
        history_limit=6, variant_limit=4, cooc_limit=4, latent_dim=4,
        latent_limit=4, content_limit=4, segment_pop_limit=6, global_pop_limit=6,
    )
    cfg = RankerConfig(
        weeks=3, val_weeks=1, negatives_per_group=6, hard_negatives_per_group=3,
        num_boost_round=5, early_stopping_rounds=3, num_leaves=7, min_data_in_leaf=1, num_threads=1,
    )
    res = train_sliding_ranker((tx, articles, customers), cutoff=cutoff, config=cfg, retrieval_config=r_cfg, work_dir=tmp_path / 'model_dir')

    loaded = load_booster(res.model_path)
    assert loaded.feature_name() == list(V2_FEATURE_NAMES)

    with pytest.raises(ValueError):
        load_booster(res.model_path, expected_feature_names=['wrong_col'])

    retriever = MultiCandidateRetriever.fit_frames(tx, articles, customers, cutoff=cutoff, config=r_cfg)
    feat_ctx = V2FeatureContext.from_frames(tx, articles, customers, cutoff=cutoff)
    cands = retriever.retrieve(['u1', 'u2'])
    feats = feat_ctx.transform(cands)
    X = feats.select(V2_FEATURE_NAMES).to_numpy().astype(np.float32)

    p_train = res.booster.predict(X, num_threads=1)
    p_loaded = predict_scores(X, booster=loaded, is_cold_start=False, num_threads=1)
    np.testing.assert_allclose(p_loaded, p_train, atol=1e-6)

    # Subprocess check: loading booster and scoring succeed even when sklearn is unavailable (hmrec-app parity)
    code = (
        'import sys; '
        'sys.modules["sklearn"] = None; '
        'from hm_recommender.v2.ranker import load_booster, predict_scores; '
        'import numpy as np; '
        f'b = load_booster({str(res.model_path)!r}); '
        'assert "lightgbm" in sys.modules; '
        'assert "torch" not in sys.modules; '
        'assert "hm_recommender.ranking.history" not in sys.modules; '
        f'p = predict_scores(np.zeros((2, {len(V2_FEATURE_NAMES)}), dtype=np.float32), booster=b, is_cold_start=False); '
        'assert p.shape == (2,); '
        'print("SUBPROC_BOOSTER_OK")'
    )
    out = subprocess.check_output([sys.executable, '-c', code], text=True)
    assert out.strip() == 'SUBPROC_BOOSTER_OK'
