from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
import polars as pl
from pydantic import ValidationError
import pytest

from hm_recommender.api.app import create_app
from hm_recommender.api.recommend import HistoryState
from hm_recommender.api.recommend_v2 import TwoStageState, load_state as load_v2_state, load_v2_state as load_v2_alias
from hm_recommender.api.schemas import (
    ActivateRequest,
    ColdStartPreferences,
    EventInput,
    HistoryRecommendationResponse,
    RecommendationItem,
    RecommendationRequest,
    RecommendationResponse,
)
from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json
from hm_recommender.releases.contracts import HistoryPublicationResult, V2PublicationResult
from hm_recommender.releases.package import build_history_release
from hm_recommender.releases.registry import ReleaseRegistry
from hm_recommender.releases.transfer import export_release, import_release
from hm_recommender.v2.package import build_v2_release
from hm_recommender.v2.ranker import RankerConfig
from hm_recommender.v2.retrieval import EVENT_KIND_WEIGHTS, EXPLAINABLE_REASONS, RetrievalConfig


def _build_synthetic_datasets(
    root: Path,
) -> tuple[DatasetManifest, DatasetManifest]:
    root.mkdir(parents=True, exist_ok=True)

    # 20 product_codes x 2 colorways = 40 articles so discovery & pagination can be thoroughly tested
    articles_rows = []
    for idx in range(1, 41):
        pc_idx = (idx - 1) // 2
        ig = 'Menswear' if idx <= 20 else 'Ladieswear'
        ptype = 'Trousers' if idx % 2 else 'Sweater'
        pgroup = 'Garment Lower body' if idx % 2 else 'Garment Upper body'
        colour = 'Black' if idx % 2 == 0 else 'White'
        prod_name = f'Atelier Coat {idx:02d}' if idx % 5 == 0 else f'Classic Garment {idx:02d}'
        articles_rows.append({
            'article_id': f'{idx:010d}',
            'product_code': f'{1000 + pc_idx:07d}',
            'prod_name': prod_name,
            'product_type_name': ptype,
            'product_group_name': pgroup,
            'colour_group_name': colour,
            'graphical_appearance_name': 'Solid',
            'index_group_name': ig,
            'department_no': '101' if ig == 'Menswear' else '202',
            'garment_group_name': 'Jersey Fancy',
            'detail_desc': f'Tailored {ig.lower()} {colour.lower()} piece {idx:02d}',
        })
    articles_df = pl.DataFrame(articles_rows)

    customers_df = pl.DataFrame({
        'customer_id': ['cust_0000', 'cust_0001', 'cust_0002', 'cust_0003', 'cust_0007'],
        'age': ['28', '36', '48', '24', None],
        'club_member_status': ['ACTIVE'] * 5,
        'fashion_news_frequency': ['Regularly', 'Monthly', 'NONE', 'NONE', 'NONE'],
        'FN': ['1.0', None, None, None, None],
        'Active': ['1.0', '1.0', None, None, None],
    })

    tx_rows: list[tuple[str, str, str, float, int]] = []
    base = date(2020, 7, 25)
    for w in range(8):
        d1 = (base + timedelta(days=7 * w)).isoformat()
        d2 = (base + timedelta(days=7 * w + 2)).isoformat()
        tx_rows.extend([
            (d1, 'cust_0000', '0000000001', 0.03, 2),
            (d2, 'cust_0000', '0000000003', 0.03, 2),
            (d1, 'cust_0000', '0000000021', 0.04, 1),
            (d1, 'cust_0001', '0000000005', 0.05, 2),
            (d2, 'cust_0001', '0000000006', 0.05, 2),
            (d1, 'cust_0002', '0000000023', 0.04, 1),
            (d2, 'cust_0002', '0000000025', 0.04, 2),
        ])
    # Recent 7d popularity across Menswear & Ladieswear so segment_popularity and pop7d are rich
    for i in range(1, 21):
        tx_rows.append(('2020-09-14', 'cust_0001', f'{i:010d}', 0.04, 2))
    for i in range(21, 41):
        tx_rows.append(('2020-09-14', 'cust_0002', f'{i:010d}', 0.04, 2))
    # Future transaction after cutoff 2020-09-16 for cust_0003 (cold-start before cutoff)
    tx_rows.append(('2020-09-20', 'cust_0003', '0000000039', 0.05, 1))

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

    v2_root = root / 'ds_v2'
    v2_root.mkdir(parents=True, exist_ok=True)
    v2_parts = [
        write_partition(tx_df.to_arrow(), v2_root, 'transactions/part.parquet'),
        write_partition(articles_df.to_arrow(), v2_root, 'articles/part.parquet'),
        write_partition(customers_df.to_arrow(), v2_root, 'customers/part.parquet'),
    ]
    ds_v2 = DatasetManifest(
        dataset_id='synthetic-v2',
        source_sha256='c' * 64,
        min_date=date(2020, 7, 25),
        max_date=date(2020, 9, 20),
        counts={'transactions': tx_df.height, 'articles': articles_df.height, 'customers': customers_df.height},
        partitions=v2_parts,
    )
    ds_v2._root = v2_root
    atomic_json(v2_root / 'manifest.json', ds_v2.model_dump(mode='json'))

    h_root = root / 'ds_h'
    h_root.mkdir(parents=True, exist_ok=True)
    h_parts = [
        write_partition(tx_df.to_arrow(), h_root, 'transactions/part.parquet'),
        write_partition(
            articles_df.select('article_id', 'product_type_name', 'product_group_name', 'colour_group_name').to_arrow(),
            h_root,
            'articles/part.parquet',
        ),
    ]
    ds_h = DatasetManifest(
        dataset_id='synthetic-h',
        source_sha256='b' * 64,
        min_date=date(2020, 7, 25),
        max_date=date(2020, 9, 20),
        counts={'transactions': tx_df.height, 'articles': articles_df.height},
        partitions=h_parts,
    )
    ds_h._root = h_root
    atomic_json(h_root / 'manifest.json', ds_h.model_dump(mode='json'))
    return ds_h, ds_v2


class _FakeSqlCursor:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakePostgresConn:
    def __init__(self, state: dict) -> None:
        self._state = state
        self.autocommit = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return self

    def close(self):
        self.closed = True

    def execute(self, sql: str, params=()):
        norm = ' '.join(sql.split()).lower()
        if 'pg_try_advisory_lock' in norm:
            return _FakeSqlCursor([(True,)])
        if norm == 'select 1':
            return _FakeSqlCursor([(1,)])
        if norm.startswith('insert into serving_releases'):
            rid, sha, _ = params
            self._state['releases'].setdefault(rid, sha)
            return _FakeSqlCursor()
        if norm.startswith('select manifest_sha256 from serving_releases'):
            rid = params[0]
            val = self._state['releases'].get(rid)
            return _FakeSqlCursor([(val,)] if val is not None else [])
        if norm.startswith('select active_id,previous_id from serving_pointer'):
            return _FakeSqlCursor([(self._state['active'], self._state['previous'])])
        if norm.startswith('update serving_pointer'):
            rid = params[0]
            if self._state['active'] != rid:
                self._state['previous'] = self._state['active']
                self._state['active'] = rid
            return _FakeSqlCursor()
        if norm.startswith('select stream_id from event_streams'):
            rel_id = params[0]
            return _FakeSqlCursor([(sid,) for sid, s in self._state['streams'].items() if s['release_id'] == rel_id and s['revision'] > 0])
        if norm.startswith('select release_id,mode from event_streams'):
            s = self._state['streams'].get(params[0])
            return _FakeSqlCursor([(s['release_id'], s['mode'])] if s else [])
        if 'from event_streams' in norm and 'operational_profiles' in norm:
            cid, sid = params[0], params[1]
            s = self._state['streams'].get(sid)
            if not s:
                return _FakeSqlCursor([])
            hist = list(s['histories'].get(cid, ()))
            return _FakeSqlCursor([(
                s['release_id'],
                s['data_as_of'],
                s['revision'],
                s['received_before'],
                s['published_at'],
                list(s['popularity']),
                hist,
                s['mode'],
            )])
        if 'from stream_catalog' in norm:
            sid, aid = params[0], params[1]
            ok = sid in self._state['streams'] and aid in self._state['catalog_ids']
            return _FakeSqlCursor([(1,)] if ok else [])
        if norm.startswith('insert into interaction_events'):
            eid, sid, cid, aid, kind, ev_time, raw_payload = params[:7]
            body = getattr(raw_payload, 'obj', raw_payload)
            if eid in self._state['events_by_id']:
                return _FakeSqlCursor([])
            self._state['events_by_id'][eid] = body
            self._state['events_log'].append({
                'event_id': str(eid),
                'stream_id': str(sid),
                'customer_id': str(cid),
                'article_id': str(aid),
                'kind': str(kind),
                'event_time': ev_time,
                'received_at': datetime.now(timezone.utc),
            })
            return _FakeSqlCursor([(eid,)])
        if 'from interaction_events' in norm and 'select payload' in norm:
            existing = self._state['events_by_id'].get(params[0])
            return _FakeSqlCursor([(existing,)] if existing is not None else [])
        if 'from interaction_events' in norm and norm.startswith('select '):
            sid, cid = str(params[0]), str(params[1])
            s = self._state['streams'].get(sid)
            cutoff = s['data_as_of'] if s else None
            out = []
            for ev in self._state['events_log']:
                if ev['stream_id'] != sid or ev['customer_id'] != cid:
                    continue
                ev_dt = ev['event_time']
                ev_date = ev_dt.astimezone(timezone.utc).date() if isinstance(ev_dt, datetime) else ev_dt
                if cutoff is not None and ev_date >= cutoff:
                    continue
                out.append((ev['article_id'], ev['kind'], ev['event_time']))
            return _FakeSqlCursor(out)
        raise AssertionError(f'Unhandled SQL: {sql}')


@pytest.fixture(scope='module')
def releases_bundle(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp('v2_serving_bundle')
    prev_commit = None
    import os
    prev_commit = os.environ.get('HMREC_SOURCE_COMMIT')
    os.environ['HMREC_SOURCE_COMMIT'] = 'a' * 40
    try:
        ds_h, ds_v2 = _build_synthetic_datasets(root)
        h_dir = root / 'rel_h'
        v2_dir = root / 'rel_v2'
        h_manifest = build_history_release(ds_h, date(2020, 9, 16), h_dir)
        r_cfg = RetrievalConfig(
            history_limit=12,
            variant_limit=10,
            cooc_limit=10,
            latent_dim=6,
            latent_limit=10,
            content_limit=10,
            segment_pop_limit=20,
            global_pop_limit=20,
        )
        rk_cfg = RankerConfig(
            weeks=4,
            val_weeks=1,
            negatives_per_group=6,
            hard_negatives_per_group=3,
            num_boost_round=6,
            early_stopping_rounds=2,
            num_leaves=7,
            min_data_in_leaf=1,
            num_threads=1,
        )
        v2_manifest = build_v2_release(
            ds_v2,
            date(2020, 9, 16),
            v2_dir,
            retrieval_config=r_cfg,
            ranker_config=rk_cfg,
        )
    finally:
        if prev_commit is None:
            os.environ.pop('HMREC_SOURCE_COMMIT', None)
        else:
            os.environ['HMREC_SOURCE_COMMIT'] = prev_commit
    return {
        'root': root,
        'h_dir': h_dir,
        'h_id': h_manifest.release_id,
        'v2_dir': v2_dir,
        'v2_id': v2_manifest.release_id,
    }


def _make_fake_db(catalog_ids: set[str]) -> tuple[dict, callable]:
    db_state = {
        'releases': {},
        'active': None,
        'previous': None,
        'streams': {},
        'catalog_ids': catalog_ids,
        'events_by_id': {},
        'events_log': [],
    }
    return db_state, lambda: _FakePostgresConn(db_state)


def test_migration_005_v2_releases_ddl_and_idempotency():
    sql_path = Path('migrations/005_v2_releases.sql')
    assert sql_path.is_file()
    ddl = sql_path.read_text()
    low = ddl.lower()
    assert 'serving_releases' in low
    assert '(h|v2)-[a-f0-9]{64}' in ddl
    assert 'drop constraint if exists serving_releases_release_id_check' in low
    assert 'interaction_events' in low
    assert 'create index if not exists' in low
    assert 'create table' not in low
    assert 'pipeline_targets' not in low


def test_schemas_and_contracts_validation():
    req_default = RecommendationRequest(customer_id='cust_0000')
    assert req_default.mode == 'balanced'
    assert req_default.preferences is None

    req_disc = RecommendationRequest(
        customer_id='cust_0000',
        k=8,
        mode='discovery',
        preferences=ColdStartPreferences(age_bin='<25', index_group_name='Menswear'),
    )
    assert req_disc.mode == 'discovery'
    assert req_disc.preferences.index_group_name == 'Menswear'

    for bad_payload in (
        {'customer_id': 'cust_0000', 'mode': 'exploit'},
        {'customer_id': 'cust_0000', 'preferences': 'Menswear'},
        {'customer_id': 'cust_0000', 'preferences': {'size': 'M'}},
        {'customer_id': 'cust_0000', 'k': 0},
        {'customer_id': 'cust_0000', 'k': 13},
        {'customer_id': 'bad user'},
    ):
        with pytest.raises(ValidationError):
            RecommendationRequest.model_validate(bad_payload)

    for reason in EXPLAINABLE_REASONS:
        item = RecommendationItem(article_id='0000000001', rank=1, reason=reason)
        assert item.reason == reason
    with pytest.raises(ValidationError):
        RecommendationItem(article_id='0000000001', rank=1, reason='unknown_reason')

    h_id = 'h-' + ('a' * 64)
    v2_id = 'v2-' + ('b' * 64)
    assert ActivateRequest(release_id=h_id).release_id == h_id
    assert ActivateRequest(release_id=v2_id).release_id == v2_id
    assert EventInput(
        event_id=str(uuid4()),
        stream_id=str(uuid4()),
        release_id=v2_id,
        customer_id='cust_0000',
        article_id='0000000001',
        kind='view',
        origin='synthetic',
        event_time=datetime(2020, 9, 15, tzinfo=timezone.utc),
    ).release_id == v2_id

    for bad_rid in ('v1-' + ('a' * 64), 'v2-' + ('A' * 64), 'v2-' + ('a' * 63), 'h-' + ('b' * 65)):
        with pytest.raises(ValidationError):
            ActivateRequest(release_id=bad_rid)

    assert RecommendationResponse(
        request_id='r1',
        release_id=v2_id,
        strategy='two_stage_lgbm_v2',
        features_timestamp=date(2020, 9, 16),
        fallback=False,
        items=[],
    ).strategy == 'two_stage_lgbm_v2'

    with pytest.raises(ValidationError):
        HistoryRecommendationResponse(
            request_id='r1',
            release_id=v2_id,
            strategy='two_stage_lgbm_v2',
            features_timestamp=date(2020, 9, 16),
            fallback=False,
            items=[],
        )

    assert V2PublicationResult(release_id=v2_id, catalog_size=40, past_buyers=5, history_rows=10).status == 'prepared'
    with pytest.raises(ValidationError):
        V2PublicationResult(release_id=h_id, catalog_size=40, past_buyers=5, history_rows=10)
    with pytest.raises(ValidationError):
        HistoryPublicationResult(release_id=v2_id, catalog_size=40, past_buyers=5, history_rows=10)


def test_registry_and_transfer_support_both_h_and_v2(releases_bundle: dict, tmp_path: Path):
    _, conn_fn = _make_fake_db({f'{i:010d}' for i in range(1, 41)})
    serving_dir = tmp_path / 'serving'
    registry = ReleaseRegistry(serving_dir, connection=conn_fn)
    registry.open()
    try:
        for bad_rid in ('', 'v1-' + 'a' * 64, 'v3-' + 'a' * 64, 'v2-' + 'A' * 64, '../v2-' + 'a' * 64):
            with pytest.raises(ValueError, match='Invalid release ID'):
                registry._directory(bad_rid)

        h_res = registry.prepare_release(releases_bundle['h_dir'])
        assert h_res['release_id'] == releases_bundle['h_id']
        v2_res = registry.prepare_release(releases_bundle['v2_dir'])
        assert v2_res['release_id'] == releases_bundle['v2_id']
        assert v2_res['ranking_verified'] is True

        registry.activate_release(releases_bundle['h_id'])
        assert isinstance(registry.snapshot(), HistoryState)

        registry.activate_release(releases_bundle['v2_id'])
        assert isinstance(registry.snapshot(), TwoStageState)

        rolled = registry.rollback()
        assert rolled == releases_bundle['h_id']
        assert isinstance(registry.snapshot(), HistoryState)

        # Tampered copy rejected and cleans up .prepare-* temp directory
        tampered = tmp_path / 'tampered_v2'
        shutil.copytree(releases_bundle['v2_dir'], tampered)
        (tampered / 'model.txt').write_text('corrupted')
        with pytest.raises(ValueError):
            registry.prepare_release(tampered)
        assert not list(serving_dir.glob('.prepare-*'))
    finally:
        registry.close()

    # Transfer export/import roundtrip for both h-* and v2-*
    h_zip = tmp_path / 'h_pkg.zip'
    v2_zip = tmp_path / 'v2_pkg.zip'
    export_release(releases_bundle['h_dir'], h_zip)
    export_release(releases_bundle['v2_dir'], v2_zip)

    imported_h = import_release(h_zip, tmp_path / 'imp_h')
    imported_v2 = import_release(v2_zip, tmp_path / 'imp_v2')
    assert imported_h.release_id == releases_bundle['h_id']
    assert imported_v2.release_id == releases_bundle['v2_id']

    # Reject extra file inside archive before unpack
    bad_zip = tmp_path / 'bad_v2.zip'
    shutil.copyfile(v2_zip, bad_zip)
    with zipfile.ZipFile(bad_zip, 'a') as zf:
        zf.writestr('extra.txt', b'unexpected')
    with pytest.raises(ValueError, match='Unexpected archive inventory'):
        import_release(bad_zip, tmp_path / 'imp_bad')


def test_two_stage_state_modes_cold_start_filters_and_live_signals(releases_bundle: dict):
    assert load_v2_alias is load_v2_state
    state = load_v2_state(releases_bundle['v2_dir'])
    assert isinstance(state, TwoStageState)
    assert not hasattr(state, 'histories')
    assert len(state.catalog) == 40
    assert len(state.popularity) == 40
    assert state.release_id == releases_bundle['v2_id']

    # Zero sklearn in current or clean subprocess import
    proc = subprocess.run(
        [
            sys.executable,
            '-c',
            (
                'import sys; '
                'import hm_recommender.api.recommend_v2 as r2; '
                'assert "sklearn" not in sys.modules; '
                'assert hasattr(r2, "TwoStageState")'
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0

    # Returning buyer: balanced vs discovery
    bal = state.recommend(RecommendationRequest(customer_id='cust_0000', k=12, mode='balanced'))
    assert bal.strategy == 'two_stage_lgbm_v2'
    assert bal.fallback is False
    assert len(bal.items) == 12
    assert [it.rank for it in bal.items] == list(range(1, 13))
    assert {it.reason for it in bal.items}.issubset(set(EXPLAINABLE_REASONS))

    disc = state.recommend(RecommendationRequest(customer_id='cust_0000', k=12, mode='discovery'))
    assert disc.strategy == 'two_stage_lgbm_v2'
    assert disc.fallback is False
    disc_ids = {it.article_id for it in disc.items}
    purchased_by_0 = {'0000000001', '0000000003', '0000000021'}
    assert purchased_by_0.isdisjoint(disc_ids)
    assert 'recent_purchase' not in {it.reason for it in disc.items}
    # At most 1 variant per product_code
    pcode_by_aid = {aid: state._product_codes[idx] for aid, idx in state._article_to_idx.items()}
    disc_pcodes = [pcode_by_aid[aid] for aid in disc_ids]
    assert len(disc_pcodes) == len(set(disc_pcodes))

    # Empty filter match returns []
    empty_f = state.recommend(
        RecommendationRequest(customer_id='cust_0000', k=5, filters={'colour_group_name': 'NeonInvisible'})
    )
    assert empty_f.items == []

    # Colour filter applied before truncation
    black_f = state.recommend(
        RecommendationRequest(customer_id='cust_0000', k=8, filters={'colour_group_name': 'Black'})
    )
    assert len(black_f.items) == 8
    assert all(it.article_id in state.filter_index['colour_group_name']['Black'] for it in black_f.items)

    # Cold-start: bare unknown vs preferences
    bare = state.recommend(RecommendationRequest(customer_id='anon-bare-user', k=6))
    assert bare.fallback is True
    assert bare.items[0].reason == 'popular_7d'

    pref = state.recommend(
        RecommendationRequest(
            customer_id='anon-bare-user',
            k=6,
            preferences=ColdStartPreferences(age_bin='<25', index_group_name='Menswear'),
        )
    )
    assert pref.fallback is True
    assert pref.items[0].reason == 'popular_for_segment'
    assert int(pref.items[0].article_id) <= 20  # Menswear articles are 0000000001..0000000020

    # Live session signals in recommend_stream: view (0.25), click (0.55), purchase (1.0)
    assert EVENT_KIND_WEIGHTS == {'purchase': 1.0, 'click': 0.55, 'view': 0.25}
    cutoff = state.data_as_of
    before_cutoff = datetime.combine(cutoff - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    at_cutoff = datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc)

    live_evs = [
        {'article_id': '0000000018', 'kind': 'view', 'event_time': before_cutoff},
        {'article_id': '0000000024', 'kind': 'click', 'event_time': before_cutoff},
        {'article_id': '0000000028', 'kind': 'purchase', 'event_time': before_cutoff},
    ]
    stream_bal = state.recommend_stream(
        RecommendationRequest(customer_id='cust_0007', k=12, mode='balanced'),
        cutoff=cutoff,
        popular=state.popularity,
        history=('0000000028',),
        live_events=live_evs,
    )
    assert stream_bal.fallback is False
    bal_ids = [it.article_id for it in stream_bal.items]
    assert {'0000000028', '0000000024', '0000000018'}.issubset(set(bal_ids))
    assert bal_ids.index('0000000028') < bal_ids.index('0000000024') < bal_ids.index('0000000018')

    stream_disc = state.recommend_stream(
        RecommendationRequest(customer_id='cust_0007', k=12, mode='discovery'),
        cutoff=cutoff,
        popular=state.popularity,
        history=('0000000028',),
        live_events=live_evs,
    )
    assert stream_disc.fallback is False
    stream_disc_ids = [it.article_id for it in stream_disc.items]
    assert '0000000028' not in stream_disc_ids
    assert '0000000024' in stream_disc_ids
    assert '0000000018' in stream_disc_ids
    assert 'recent_purchase' not in {it.reason for it in stream_disc.items}

    # Discovery returns fewer than k without duplicates when unique product_codes exhausted
    # 20 product_codes total; purchase 12 product_codes -> at most 8 unique product_codes left
    many_purchases = tuple(f'{i:010d}' for i in range(1, 25))
    exhausted_disc = state.recommend_stream(
        RecommendationRequest(customer_id='cust_0007', k=12, mode='discovery'),
        cutoff=cutoff,
        popular=state.popularity,
        history=many_purchases,
        live_events=[],
    )
    ex_ids = [it.article_id for it in exhausted_disc.items]
    assert 1 <= len(ex_ids) <= 8
    assert set(many_purchases).isdisjoint(ex_ids)
    ex_pcodes = [pcode_by_aid[a] for a in ex_ids]
    assert len(ex_pcodes) == len(set(ex_pcodes))

    # Post-cutoff events (>= cutoff) ignored in recommend_stream
    post_only = state.recommend_stream(
        RecommendationRequest(customer_id='cust_0007', k=12, mode='balanced'),
        cutoff=cutoff,
        popular=state.popularity,
        history=(),
        live_events=[{'article_id': '0000000018', 'kind': 'purchase', 'event_time': at_cutoff}],
    )
    assert post_only.fallback is True

    # Mapping[str, float] fallback signature also works
    map_res = state.recommend_stream(
        RecommendationRequest(customer_id='cust_0007', k=12, mode='balanced'),
        cutoff=cutoff,
        popular=state.popularity,
        history=(),
        live_events={'0000000018': 0.25, '0000000024': 0.55},
    )
    assert map_res.fallback is False
    assert {'0000000018', '0000000024'}.issubset({it.article_id for it in map_res.items})

    # Synthetic latency check (< 40 ms p95)
    latencies_ms = []
    for _ in range(40):
        t0 = time.perf_counter()
        state.recommend(RecommendationRequest(customer_id='cust_0000', k=12, mode='balanced'))
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
    p95_ms = sorted(latencies_ms)[int(0.95 * len(latencies_ms)) - 1]
    assert p95_ms < 40.0


def test_api_routes_catalog_cache_events_streams_and_auth_preserved(
    releases_bundle: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    catalog_ids = {f'{i:010d}' for i in range(1, 41)}
    db_state, conn_fn = _make_fake_db(catalog_ids)

    input_root = tmp_path / 'input'
    input_root.mkdir()
    shutil.copytree(releases_bundle['h_dir'], input_root / 'rel_h')
    shutil.copytree(releases_bundle['v2_dir'], input_root / 'rel_v2')
    serving_dir = tmp_path / 'serving'

    registry = ReleaseRegistry(serving_dir, connection=conn_fn)
    app = create_app(registry, admin_token='adm-secret', input_root=str(input_root))
    headers = {'Authorization': 'Bearer adm-secret'}

    with TestClient(app) as client:
        # Unready before activation
        assert client.get('/health/ready').status_code == 503
        assert client.get('/catalog').status_code == 503
        assert client.post('/recommend', json={'customer_id': 'cust_0000', 'k': 5}).status_code == 503

        # M4 Auth preservation check
        assert client.post('/admin/releases/prepare', json={'directory': 'rel_h'}).status_code == 403
        assert client.get('/login').status_code == 200
        assert client.get('/auth/verify', headers={'accept': 'text/html'}, follow_redirects=False).status_code == 307
        assert client.get('/auth/verify', headers={'accept': 'application/json'}).status_code == 401

        # Prepare h-* and v2-*
        assert client.post('/admin/releases/prepare', headers=headers, json={'directory': 'rel_h'}).status_code == 200
        assert client.post('/admin/releases/prepare', headers=headers, json={'directory': 'rel_v2'}).status_code == 200

        # Activate h-* and verify 100% v1 parity
        assert client.post('/admin/releases/activate', headers=headers, json={'release_id': releases_bundle['h_id']}).status_code == 200
        h_rec = client.post('/recommend', json={'customer_id': 'cust_0000', 'k': 5})
        assert h_rec.status_code == 200
        assert h_rec.json()['strategy'] == 'recent_then_popular'
        assert client.get('/catalog?index_group_name=Menswear').json()['total'] == 0

        # Activate v2-*
        assert client.post('/admin/releases/activate', headers=headers, json={'release_id': releases_bundle['v2_id']}).status_code == 200
        assert client.get('/health/ready').json()['release_id'] == releases_bundle['v2_id']

        v2_rec = client.post('/recommend', json={'customer_id': 'cust_0000', 'k': 12, 'mode': 'discovery'})
        assert v2_rec.status_code == 200
        assert v2_rec.json()['strategy'] == 'two_stage_lgbm_v2'

        # Validation boundaries
        assert client.post('/recommend', json={'customer_id': 'cust_0000', 'mode': 'exploit'}).status_code == 422
        assert client.post('/recommend', json={'customer_id': 'cust_0000', 'preferences': {'size': 'M'}}).status_code == 422

        # /catalog search, index_group_name, pagination & in-memory cache
        warm = client.get('/catalog?offset=0&limit=12')
        assert warm.status_code == 200
        assert warm.json()['release_id'] == releases_bundle['v2_id']
        assert warm.json()['total'] == 40

        def _fail_parquet(*args, **kwargs):
            raise AssertionError('Parquet read called on warm /catalog cache')

        with monkeypatch.context() as m:
            m.setattr(pl, 'scan_parquet', _fail_parquet)
            m.setattr(pl, 'read_parquet', _fail_parquet)

            page2 = client.get('/catalog?offset=12&limit=12')
            assert page2.status_code == 200
            assert page2.json()['total'] == 40
            assert {r['article_id'] for r in warm.json()['items']}.isdisjoint(
                {r['article_id'] for r in page2.json()['items']}
            )

            # Literal q search (case-insensitive & regex metacharacters)
            q_res = client.get('/catalog', params={'q': 'atelier'})
            assert q_res.status_code == 200
            assert q_res.json()['total'] == 8  # every 5th of 40 articles

            q_id = client.get('/catalog', params={'q': '0000000025'})
            assert q_id.status_code == 200
            assert q_id.json()['total'] == 1

            q_regex = client.get('/catalog', params={'q': '[broken('})
            assert q_regex.status_code == 200
            assert q_regex.json()['total'] == 0

            ig_res = client.get('/catalog', params={'index_group_name': 'Menswear', 'limit': 48})
            assert ig_res.status_code == 200
            assert ig_res.json()['total'] == 20
            assert all(it['index_group_name'] == 'Menswear' for it in ig_res.json()['items'])

            combo = client.get('/catalog', params={'q': 'atelier', 'index_group_name': 'Menswear', 'offset': 1, 'limit': 2})
            assert combo.status_code == 200
            assert combo.json()['total'] == 4
            assert len(combo.json()['items']) == 2

        # Stream events + /streams/{id}/recommend
        sid = str(uuid4())
        cutoff = date(2020, 9, 16)
        db_state['streams'][sid] = {
            'release_id': releases_bundle['v2_id'],
            'mode': 'synthetic',
            'data_as_of': cutoff,
            'revision': 1,
            'received_before': datetime.now(timezone.utc),
            'published_at': datetime.now(timezone.utc),
            'popularity': tuple(f'{i:010d}' for i in range(1, 21)),
            'histories': {},
        }
        ev_time = datetime(2020, 9, 15, 12, tzinfo=timezone.utc).isoformat()
        ev_id = str(uuid4())
        for eid, aid, kind in (
            (ev_id, '0000000018', 'view'),
            (str(uuid4()), '0000000024', 'click'),
            (str(uuid4()), '0000000028', 'purchase'),
        ):
            r = client.post(
                '/events',
                json={
                    'event_id': eid,
                    'stream_id': sid,
                    'release_id': releases_bundle['v2_id'],
                    'customer_id': 'cust_0007',
                    'article_id': aid,
                    'kind': kind,
                    'origin': 'synthetic',
                    'event_time': ev_time,
                },
            )
            assert r.status_code == 201
        # Idempotent duplicate
        dup = client.post(
            '/events',
            json={
                'event_id': ev_id,
                'stream_id': sid,
                'release_id': releases_bundle['v2_id'],
                'customer_id': 'cust_0007',
                'article_id': '0000000018',
                'kind': 'view',
                'origin': 'synthetic',
                'event_time': ev_time,
            },
        )
        assert dup.status_code == 200
        assert dup.json()['status'] == 'duplicate'

        # Simulate purchase in operational_profiles as well
        db_state['streams'][sid]['histories']['cust_0007'] = ('0000000028',)

        s_bal = client.post(
            f'/streams/{sid}/recommend',
            json={'customer_id': 'cust_0007', 'k': 12, 'mode': 'balanced'},
        )
        assert s_bal.status_code == 200
        s_body = s_bal.json()
        assert s_body['stream_id'] == sid
        assert s_body['mode'] == 'synthetic'
        assert s_body['strategy'] == 'two_stage_lgbm_v2'
        assert {'0000000028', '0000000024', '0000000018'}.issubset({it['article_id'] for it in s_body['items']})

        s_disc = client.post(
            f'/streams/{sid}/recommend',
            json={'customer_id': 'cust_0007', 'k': 12, 'mode': 'discovery'},
        )
        assert s_disc.status_code == 200
        s_disc_ids = {it['article_id'] for it in s_disc.json()['items']}
        assert '0000000028' not in s_disc_ids
        assert {'0000000024', '0000000018'}.issubset(s_disc_ids)

        # Rollback back to h-* invalidates/swaps /catalog view & rejects stale v2-* /events
        assert client.post('/admin/releases/rollback', headers=headers).status_code == 200
        assert client.get('/health/ready').json()['release_id'] == releases_bundle['h_id']
        assert client.get('/catalog').json()['release_id'] == releases_bundle['h_id']
        stale_ev = client.post(
            '/events',
            json={
                'event_id': str(uuid4()),
                'stream_id': sid,
                'release_id': releases_bundle['v2_id'],
                'customer_id': 'cust_0007',
                'article_id': '0000000018',
                'kind': 'view',
                'origin': 'synthetic',
                'event_time': ev_time,
            },
        )
        assert stale_ev.status_code == 409


# ---------------------------------------------------------------------------
# M3b: ImageCache O(1) & Streamlit AppTest E2E Shopper Tests
# ---------------------------------------------------------------------------


def test_image_cache_o1_getinfo_and_edge_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hm_recommender.ui.images import PLACEHOLDER, ImageCache

    for bad_bool in (True, False):
        with pytest.raises(ValueError):
            ImageCache(None, tmp_path / f'bool_{bad_bool}', max_bytes=bad_bool)

    zip_path = tmp_path / 'imgs_m3b.zip'
    valid_jpeg_a = b'\xff\xd8\xff\xe0' + b'A' * 120 + b'\xff\xd9'
    valid_jpeg_b = b'\xff\xd8\xff\xe0' + b'B' * 120 + b'\xff\xd9'
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('images/000/0000000005.jpg', valid_jpeg_a)
        zf.writestr('images/000/0000000500.jpg', valid_jpeg_b)
        zf.writestr('images/000/0000000099.jpg/', b'')
        sym_info = zipfile.ZipInfo('images/000/0000000098.jpg')
        sym_info.create_system = 3
        sym_info.external_attr = 0o120777 << 16
        zf.writestr(sym_info, b'/etc/passwd')

    cache_dir = tmp_path / 'cache_m3b'
    cache = ImageCache(zip_path, cache_dir, max_bytes=64 * 1024)

    # Non-string / invalid article IDs safely fall back to PLACEHOLDER
    assert cache.read(1234567890) == (PLACEHOLDER, 'image/svg+xml')  # type: ignore[arg-type]
    assert cache.read('000000000') == (PLACEHOLDER, 'image/svg+xml')

    # Directory & symlink zip members must be rejected
    assert cache.read('0000000099') == (PLACEHOLDER, 'image/svg+xml')
    assert cache.read('0000000098') == (PLACEHOLDER, 'image/svg+xml')

    # Spy on ZipFile.infolist / namelist / getinfo during cache miss
    infolist_calls = 0
    namelist_calls = 0
    getinfo_calls = 0
    orig_infolist = zipfile.ZipFile.infolist
    orig_namelist = zipfile.ZipFile.namelist
    orig_getinfo = zipfile.ZipFile.getinfo

    def _spy_infolist(self: zipfile.ZipFile):
        nonlocal infolist_calls
        infolist_calls += 1
        return orig_infolist(self)

    def _spy_namelist(self: zipfile.ZipFile):
        nonlocal namelist_calls
        namelist_calls += 1
        return orig_namelist(self)

    def _spy_getinfo(self: zipfile.ZipFile, name: str):
        nonlocal getinfo_calls
        getinfo_calls += 1
        return orig_getinfo(self, name)

    with monkeypatch.context() as mp:
        mp.setattr(zipfile.ZipFile, 'infolist', _spy_infolist)
        mp.setattr(zipfile.ZipFile, 'namelist', _spy_namelist)
        mp.setattr(zipfile.ZipFile, 'getinfo', _spy_getinfo)
        body, mime = cache.read('0000000005')
        assert mime == 'image/jpeg' and body == valid_jpeg_a
        assert infolist_calls == 0
        assert namelist_calls == 0
        assert getinfo_calls >= 1

    # Disk cache hit must never open ZipFile
    def _fail_zip(*args, **kwargs):
        raise AssertionError('Disk cache hit reopened ZIP')

    with monkeypatch.context() as mp:
        mp.setattr(zipfile, 'ZipFile', _fail_zip)
        body_hit, mime_hit = cache.read('0000000005')
        assert mime_hit == 'image/jpeg' and body_hit == valid_jpeg_a

    # .partial cleanup when atomic replace fails
    def _fail_replace(self: Path, target: Path):
        raise OSError('disk full')

    with monkeypatch.context() as mp:
        mp.setattr(Path, 'replace', _fail_replace)
        body_err, mime_err = cache.read('0000000500')
        assert (body_err, mime_err) == (PLACEHOLDER, 'image/svg+xml')
    assert list(cache_dir.glob('*.partial')) == []


def test_ui_apptest_v2_onboarding_live_actions_mode_and_catalog(
    releases_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from unittest.mock import patch

    zip_path = tmp_path / 'ui_v2_imgs.zip'
    valid_jpeg = b'\xff\xd8\xff\xe0' + b'Z' * 120 + b'\xff\xd9'
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for idx in range(1, 41):
            aid = f'{idx:010d}'
            zf.writestr(f'images/{aid[:3]}/{aid}.jpg', valid_jpeg)

    monkeypatch.setenv('HMREC_IMAGE_ARCHIVE', str(zip_path))
    monkeypatch.setenv('HMREC_IMAGE_CACHE', str(tmp_path / 'ui_img_cache'))
    monkeypatch.setenv('HM_APP_PASSWORD', 'secret-token')

    db_state, make_conn = _make_fake_db({f'{i:010d}' for i in range(1, 41)})
    registry = ReleaseRegistry(tmp_path / 'ui_reg', connection=make_conn)
    app_dir = tmp_path / 'ui_input'
    app_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(releases_bundle['v2_dir'], app_dir / 'rel_v2')
    api_app = create_app(registry, admin_token='secret-token', input_root=str(app_dir))

    sid = str(uuid4())
    cutoff = date(2020, 9, 16)
    db_state['streams'][sid] = {
        'release_id': releases_bundle['v2_id'],
        'mode': 'synthetic',
        'data_as_of': cutoff,
        'revision': 1,
        'received_before': datetime.now(timezone.utc),
        'published_at': datetime.now(timezone.utc),
        'popularity': tuple(f'{i:010d}' for i in range(1, 21)),
        'histories': {'cust_0000': ('0000000001', '0000000003')},
    }

    http_calls: list[dict[str, object]] = []
    ui_source = Path(__file__).resolve().parents[2] / 'src' / 'hm_recommender' / 'ui' / 'app.py'

    with TestClient(api_app) as test_client:
        headers = {'Authorization': 'Bearer secret-token'}
        assert test_client.post('/admin/releases/prepare', headers=headers, json={'directory': 'rel_v2'}).status_code == 200
        assert test_client.post(
            '/admin/releases/activate',
            headers=headers,
            json={'release_id': releases_bundle['v2_id']},
        ).status_code == 200

        def _transport_handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            method = request.method.upper()
            req_json = None
            if request.content:
                try:
                    req_json = json.loads(request.content.decode('utf-8'))
                except Exception:
                    req_json = None

            if method == 'GET' and path == '/streams':
                s_row = db_state['streams'][sid]
                resp = httpx.Response(
                    200,
                    json={
                        'items': [
                            {
                                'stream_id': sid,
                                'release_id': s_row['release_id'],
                                'mode': s_row['mode'],
                                'data_as_of': str(s_row['data_as_of']),
                                'revision': s_row['revision'],
                                'received_before': s_row['received_before'].isoformat(),
                                'published_at': s_row['published_at'].isoformat(),
                            }
                        ]
                    },
                )
            elif method == 'GET' and path == f'/streams/{sid}/demo-profiles':
                resp = httpx.Response(
                    200,
                    json={
                        'release_id': releases_bundle['v2_id'],
                        'items': [
                            {
                                'customer_id': 'cust_0000',
                                'key': 'freq',
                                'label': 'Comprador frecuente',
                                'description': 'Prefiere prendas clásicas de Menswear.',
                                'history_count': 2,
                                'matched_count': 2,
                                'examples': [
                                    {
                                        'article_id': '0000000001',
                                        'product_type_name': 'Trousers',
                                        'colour_group_name': 'White',
                                    }
                                ],
                            }
                        ],
                    },
                )
            elif method == 'POST' and path == f'/admin/streams/{sid}/refresh':
                stream_row = db_state['streams'][sid]
                stream_row['revision'] += 1
                now_utc = datetime.now(timezone.utc) + timedelta(seconds=1)
                stream_row['received_before'] = now_utc
                stream_row['published_at'] = now_utc
                for ev in db_state['events_log']:
                    if ev['kind'] == 'purchase':
                        prev = list(stream_row['histories'].get(ev['customer_id'], ()))
                        if ev['article_id'] not in prev:
                            prev.insert(0, ev['article_id'])
                        stream_row['histories'][ev['customer_id']] = tuple(prev)
                resp = httpx.Response(200, json={'stream_id': sid, 'revision': stream_row['revision']})
            else:
                tc_resp = test_client.request(
                    method,
                    str(request.url.raw_path.decode('utf-8')),
                    headers=dict(request.headers),
                    content=request.content,
                )
                resp = httpx.Response(
                    tc_resp.status_code,
                    headers=dict(tc_resp.headers),
                    content=tc_resp.content,
                )

            resp_json = None
            if resp.headers.get('content-type', '').startswith('application/json'):
                try:
                    resp_json = resp.json()
                except Exception:
                    resp_json = None
            http_calls.append(
                {
                    'method': method,
                    'path': path,
                    'params': dict(request.url.params),
                    'json': req_json,
                    'status_code': resp.status_code,
                    'response_json': resp_json,
                }
            )
            return resp

        orig_client = httpx.Client

        def _patched_client(*args, **kwargs):
            kwargs.pop('transport', None)
            kwargs.setdefault('base_url', 'http://api.test')
            return orig_client(*args, transport=httpx.MockTransport(_transport_handler), **kwargs)

        def _visible(app_inst) -> str:
            return '\n'.join(
                str(item.value)
                for kind in ('title', 'header', 'subheader', 'text', 'caption', 'markdown', 'info', 'success', 'warning', 'error')
                for item in getattr(app_inst, kind)
            )

        st.cache_data.clear()
        try:
            with patch('httpx.Client', side_effect=_patched_client):
                app = AppTest.from_file(str(ui_source), default_timeout=25).run()
                assert not app.exception
                assert sum(1 for c in http_calls if c['method'] == 'GET' and c['path'] == '/catalog') == 1

                init_txt = _visible(app)
                assert releases_bundle['v2_id'] not in init_txt
                assert re.search(r'\b[0-9]{10}\b', init_txt)
                assert not re.search(r'ART\.\s*[0-9]{8}\b', init_txt)
                assert 'Tu actividad en esta sesión' in init_txt

                # 1. Cold-start onboarding preferences
                app.selectbox(key='onboard_group').select('Hombre (Menswear)').run()
                app.selectbox(key='age_bin').select('<25 años').run()
                assert not app.exception
                pref_recs = [
                    c
                    for c in http_calls
                    if c['method'] == 'POST'
                    and c['path'] == f'/streams/{sid}/recommend'
                    and isinstance(c['json'], dict)
                    and c['json'].get('preferences')
                ]
                assert pref_recs
                assert pref_recs[-1]['json']['preferences'] == {'age_bin': '<25', 'index_group_name': 'Menswear'}
                assert 'TENDENCIA PARA TI' in _visible(app)

                # 2. Live actions: view, click, purchase
                view_btns = [b for b in app.button if (b.key or '').startswith('rec_view_')]
                assert view_btns
                view_btns[0].click().run()
                assert not app.exception
                assert 'Detalle de la prenda' in _visible(app)

                fav_btns = [b for b in app.button if (b.key or '').startswith('rec_click_')]
                assert fav_btns
                fav_btns[0].click().run()
                assert not app.exception

                buy_btns = [b for b in app.button if (b.key or '').startswith('rec_purchase_')]
                assert buy_btns
                buy_btns[0].click().run()
                assert not app.exception

                assert {'view', 'click', 'purchase'}.issubset({e['kind'] for e in db_state['events_log']})
                purchased_aid = [e['article_id'] for e in db_state['events_log'] if e['kind'] == 'purchase'][-1]
                after_act_txt = _visible(app)
                assert 'Vistos:** 1' in after_act_txt or 'Vistos: 1' in after_act_txt

                # 3. Mode toggle -> discovery excludes purchased & deduplicates product_code
                http_calls.clear()
                app.radio(key='mode').set_value('Solo descubrimiento').run()
                assert not app.exception
                disc_recs = [
                    c
                    for c in http_calls
                    if c['method'] == 'POST'
                    and c['path'] == f'/streams/{sid}/recommend'
                    and isinstance(c['json'], dict)
                    and c['json'].get('mode') == 'discovery'
                ]
                assert disc_recs
                disc_items = disc_recs[-1]['response_json']['items']
                assert purchased_aid not in [it['article_id'] for it in disc_items]

                # 4. Catalog search, section filter, pagination (<= 1 /catalog call) & empty state
                http_calls.clear()
                app.button(key='btn_next_page').click().run()
                assert not app.exception
                page2_cat = [c for c in http_calls if c['method'] == 'GET' and c['path'] == '/catalog']
                assert len(page2_cat) <= 1
                if page2_cat:
                    assert page2_cat[0]['params'].get('offset') == '12'

                http_calls.clear()
                app.text_input(key='catalog_q').input('Atelier').run()
                assert any(
                    c['method'] == 'GET' and c['path'] == '/catalog' and c['params'].get('q') == 'Atelier'
                    for c in http_calls
                )
                app.text_input(key='catalog_q').input('zzznonexistent999').run()
                assert 'Sin resultados en el catálogo' in _visible(app)
                app.text_input(key='catalog_q').input('').run()
                app.selectbox(key='index_group_name').select('Mujer (Ladieswear)').run()
                assert any(
                    c['method'] == 'GET'
                    and c['path'] == '/catalog'
                    and c['params'].get('index_group_name') == 'Ladieswear'
                    for c in http_calls
                )
        finally:
            st.cache_data.clear()
