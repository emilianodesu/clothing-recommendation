"""Real package + PostgreSQL + HTTP contract. No customer data fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from pathlib import Path
import uuid

import polars as pl
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS') != '1', reason='requires isolated PostgreSQL')


@pytest.fixture
def serving(tmp_path, monkeypatch):
    from hm_recommender import database
    from hm_recommender.contracts import DatasetManifest
    from hm_recommender.data.ingest import write_partition
    from hm_recommender.data.io import atomic_json
    from hm_recommender.releases.package import build_history_release
    # A runtime that truncates before filtering or silently drops old buyers fails these fixtures.
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a'*40)
    ids=[f'{n:06}' for n in range(255)]
    root=tmp_path/'data'; root.mkdir()
    tx=pl.DataFrame([('buyer',i,'2020-09-08') for i in ids]+[
        ('old-only','000001','2020-07-01'),('other','000254','2020-09-07')],
        schema=['customer_id','article_id','t_dat'],orient='row').with_columns(pl.col('t_dat').str.to_date())
    catalog=pl.DataFrame({'article_id':ids,'product_type_name':['shirt']*255,
        'product_group_name':['garment']*255,'colour_group_name':['blue']*250+['red']*5})
    files=[write_partition(tx.to_arrow(),root,'transactions/part.parquet'),
           write_partition(catalog.to_arrow(),root,'articles/part.parquet')]
    ds=DatasetManifest(dataset_id='synthetic',source_sha256='b'*64,min_date=date(2020,7,1),
        max_date=date(2020,9,8),counts={'transactions':257},partitions=files)
    ds._root=root; atomic_json(root/'manifest.json',ds.model_dump(mode='json'))
    a=build_history_release(ds,date(2020,9,9),tmp_path/'input'/'a')
    b=build_history_release(ds,date(2020,9,8),tmp_path/'input'/'b')
    schema='test_serving_'+uuid.uuid4().hex
    with database.connect() as conn: conn.execute('CREATE SCHEMA '+schema)
    def connection():
        conn=database.connect(); conn.execute('SET search_path TO '+schema); conn.commit(); return conn
    with connection() as conn:
        conn.execute((Path('/workspace/migrations') if Path('/workspace/migrations').exists() else Path('/opt/hmrec/migrations')).joinpath('002_releases.sql').read_text())
    from hm_recommender.releases.registry import ReleaseRegistry
    from hm_recommender.api.app import create_app
    registry=ReleaseRegistry(tmp_path/'registry',connection)
    with TestClient(create_app(registry, admin_token='local-test-token', input_root=tmp_path/'input')) as client:
        yield client,registry,a,b,connection,tmp_path
    with database.connect() as conn: conn.execute('DROP SCHEMA '+schema+' CASCADE')


def activate(serving, which=2):
    client,registry,*_=serving; release=serving[which]
    registry.prepare_release(release._root)
    registry.activate_release(release.release_id)
    return release


def test_http_parity_with_t4a(serving):
    from hm_recommender.ranking.history import rank_history
    client,registry,a,*_=serving; activate(serving)
    body=client.post('/recommend',json={'customer_id':'buyer','k':12}).json()
    assert body['release_id']==a.release_id and body['features_timestamp']=='2020-09-09'
    assert body['strategy']=='recent_then_popular' and body['fallback'] is False
    assert [i['article_id'] for i in body['items']]==[f'{n:06}' for n in range(12)]
    assert [i['rank'] for i in body['items']]==list(range(1,13))
    assert all(i['reason']=='recent_purchase' for i in body['items'])
    assert registry.snapshot().rank('buyer',{})==rank_history([f'{n:06}' for n in range(255)],
        ['000254']+[f'{n:06}' for n in range(254)],{f'{n:06}' for n in range(255)})


def test_unknown_user_uses_popularity(serving):
    activate(serving); client=serving[0]
    for user in ('unknown','old-only'):
        r=client.post('/recommend',json={'customer_id':user,'k':3}).json()
        assert r['fallback'] is True
        assert [i['article_id'] for i in r['items']]==['000254','000000','000001']
        assert all(i['reason']=='popular_7d' for i in r['items'])


def test_filters_apply_before_candidate_limit(serving):
    activate(serving)
    body=serving[0].post('/recommend',json={'customer_id':'buyer','k':12,'filters':{'colour_group_name':'red'}}).json()
    assert [i['article_id'] for i in body['items']]==[f'{n:06}' for n in range(250,255)]
    assert body['fallback'] is False


def test_empty_filter_returns_empty(serving):
    activate(serving)
    body=serving[0].post('/recommend',json={'customer_id':'buyer','filters':{'product_type_name':'absent'}}).json()
    assert body['items']==[] and body['fallback'] is True


def test_filter_fallback_depends_on_eligible_history(serving):
    activate(serving)
    body=serving[0].post('/recommend',json={'customer_id':'other','k':2,'filters':{'colour_group_name':'blue'}}).json()
    assert body['fallback'] is True
    assert [i['article_id'] for i in body['items']]==['000000','000001']


@pytest.mark.parametrize('payload',[{'k':0},{'k':13},{'k':True},{'k':'2'}, {'filters':{'unknown':'x'}},
    {'filters':{'colour_group_name':None}}, {'customer_id':''}, {'customer_id':12}, {'extra':'x'}])
def test_invalid_request_rejected_without_echoing_customer(serving,payload):
    r=serving[0].post('/recommend',json={'customer_id':'private-buyer',**payload})
    assert r.status_code==422 and 'private-buyer' not in r.text


def test_health_no_version_and_metrics_do_not_leak_ids(serving):
    client=serving[0]
    assert client.get('/health/live').status_code==200
    assert client.get('/health/ready').status_code==503
    assert client.post('/recommend',json={'customer_id':'private-buyer'}).status_code==503
    activate(serving)
    assert client.get('/health/ready').status_code==200
    client.post('/recommend',json={'customer_id':'private-buyer'})
    metrics=client.get('/metrics').text
    assert 'hmrec_recommendations_total' in metrics and 'private-buyer' not in metrics


def test_admin_requires_auth_and_cannot_escape_input_root(serving):
    client,_,a,*_=serving
    assert client.post('/admin/releases/prepare',json={'directory':'a'}).status_code==403
    headers={'Authorization':'Bearer local-test-token'}
    assert client.post('/admin/releases/prepare',headers=headers,json={'directory':'../data'}).status_code==400
    r=client.post('/admin/releases/prepare',headers=headers,json={'directory':'a'})
    assert r.status_code==200 and r.json()['release_id']==a.release_id
    assert client.post('/admin/releases/activate',headers=headers,json={'release_id':a.release_id}).status_code==200
    assert client.get('/health/ready').json()['release_id']==a.release_id
