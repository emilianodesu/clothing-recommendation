"""Catch duplicate purchases, time leakage, non-atomic profiles and false acknowledgements."""
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4
import pytest
from test_recommendations import serving, activate

pytestmark = pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS') != '1', reason='requires isolated PostgreSQL')


@pytest.fixture
def events(serving):
    client, registry, release, _, connection, root = serving
    activate(serving)
    migration = (Path('/workspace/migrations') if Path('/workspace/migrations').exists() else Path('/opt/hmrec/migrations'))/'003_events.sql'
    assert migration.exists(), 'T5 must install durable events and profiles'
    with connection() as conn: conn.execute(migration.read_text())
    from hm_recommender.events.aggregate import initialize_stream
    stream = str(uuid4())
    initialize_stream(root/'data', release._root, stream, mode='synthetic', connection=connection)
    return serving, stream


def event(events, **changes):
    serving, stream = events
    return dict(event_id=str(uuid4()), stream_id=stream, release_id=serving[2].release_id,
        customer_id='new-customer', article_id='000010', kind='purchase',
        event_time='2020-09-08T12:00:00Z', origin='synthetic', **changes)


def refresh(events, **kwargs):
    from hm_recommender.events.aggregate import aggregate_events
    serving, stream = events
    return aggregate_events(serving[2].release_id, datetime.now(timezone.utc),
        stream_id=stream, connection=serving[4], **kwargs)


def recommend(events, user='new-customer'):
    serving, stream = events
    response=serving[0].post(f'/streams/{stream}/recommend',json={'customer_id':user,'k':3})
    assert response.status_code == 200, response.text
    return response.json()


def test_duplicate_event_is_counted_once(events):
    serving, stream = events; client=serving[0]
    before=recommend(events)
    assert [x['article_id'] for x in before['items']]==['000254','000000','000001']
    payload=event(events)
    assert client.post('/events',json=payload).status_code==201
    assert client.post('/events',json=payload).status_code==200
    assert client.post('/events',json={**payload,'article_id':'000011'}).status_code==409
    assert recommend(events)['revision']==before['revision']
    result=refresh(events)
    after=recommend(events)
    assert [x['article_id'] for x in after['items']]==['000010','000254','000000']
    assert after['revision']>before['revision'] and after['stream_id']==stream
    with serving[4]() as conn:
        assert conn.execute('SELECT count(*) FROM interaction_events').fetchone()[0]==1
        assert conn.execute('SELECT frequency FROM operational_daily WHERE stream_id=%s AND customer_id=%s',(stream,'new-customer')).fetchone()[0]==1
    # Base package endpoint stays frozen and does not learn demonstration purchases.
    assert client.post('/recommend',json={'customer_id':'new-customer','k':3}).json()['items']==before['items']


def test_late_event_rebuilds_correct_profile_and_expiry(events):
    serving, stream=events; client=serving[0]
    for item, when, kind in [('000010','2020-09-08T10:00:00Z','purchase'),
                              ('000011','2020-09-07T10:00:00Z','purchase'),
                              ('000012','2020-09-09T00:00:00Z','purchase'),
                              ('000013','2020-09-08T11:00:00Z','click')]:
        payload={**event(events),'article_id':item,'event_time':when,'kind':kind}
        assert client.post('/events',json=payload).status_code==201
    refresh(events)
    assert [x['article_id'] for x in recommend(events)['items']]==['000010','000011','000254']
    refresh(events,data_as_of=date(2020,9,10))
    assert [x['article_id'] for x in recommend(events)['items']]==['000012','000010','000011']
    # 28-day lower bound is inclusive; 7-day popularity expires independently.
    refresh(events,data_as_of=date(2020,10,7))
    assert [x['article_id'] for x in recommend(events)['items']]==['000012','000000','000001']
    refresh(events,data_as_of=date(2020,10,8))
    assert [x['article_id'] for x in recommend(events)['items']]==['000000','000001','000002']
    assert recommend(events)['fallback'] is True


def test_database_failure_is_not_acknowledged(events,monkeypatch):
    from hm_recommender.events.store import EventStore
    def unavailable(self, payload): raise OSError('database unavailable')
    monkeypatch.setattr(EventStore,'insert',unavailable)
    assert events[0][0].post('/events',json=event(events)).status_code==503
    with events[0][4]() as conn: assert conn.execute('SELECT count(*) FROM interaction_events').fetchone()[0]==0


def test_refresh_is_atomic_replayable_and_persists(events):
    from hm_recommender.events.aggregate import aggregate_events
    serving,stream=events
    serving[0].post('/events',json=event(events))
    watermark=datetime.now(timezone.utc)
    first=aggregate_events(serving[2].release_id,watermark,stream_id=stream,connection=serving[4])
    second=aggregate_events(serving[2].release_id,watermark,stream_id=stream,connection=serving[4])
    assert first==second
    old=recommend(events)
    with serving[4]() as conn:
        conn.execute("CREATE FUNCTION fail_profiles() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'test interruption'; END $$ LANGUAGE plpgsql")
        conn.execute('CREATE TRIGGER fail_profiles BEFORE INSERT ON operational_profiles FOR EACH ROW EXECUTE FUNCTION fail_profiles()')
    serving[0].post('/events',json={**event(events),'article_id':'000009'})
    with pytest.raises(Exception):refresh(events)
    assert recommend(events)['revision']==old['revision']
    assert recommend(events)['items']==old['items']
    with serving[4]() as conn: conn.execute('DROP TRIGGER fail_profiles ON operational_profiles')
    refresh(events)
    assert recommend(events)['items'][0]['article_id']=='000009'
    serving[1].recover()
    assert recommend(events)['items'][0]['article_id']=='000009'


def test_stream_identity_time_and_source_are_enforced(events):
    serving,stream=events; client=serving[0]
    for changes,code in [({'origin':'simulation'},409),({'release_id':serving[3].release_id},409),
                         ({'article_id':'missing'},422),({'event_time':'2020-09-08'},422)]:
        assert client.post('/events',json={**event(events),**changes}).status_code==code
    with pytest.raises(ValueError): refresh(events,data_as_of=date(2020,9,8))
    from hm_recommender.events.aggregate import aggregate_events
    with pytest.raises(ValueError): aggregate_events(serving[2].release_id,datetime.now(timezone.utc)+timedelta(days=1),stream_id=stream,connection=serving[4])
    client.post('/events',json=event(events));refresh(events)
    serving[1].prepare_release(serving[3]._root);serving[1].activate_release(serving[3].release_id)
    assert client.post(f'/streams/{stream}/recommend',json={'customer_id':'new-customer'}).status_code==409


def test_ui_endpoints_are_bounded_and_refresh_requires_admin(events):
    serving,stream=events;client=serving[0]
    response=client.get('/catalog',params={'limit':12,'offset':250})
    assert response.status_code==200
    assert len(response.json()['items'])==5 and response.json()['total']==255
    assert response.json()['items'][0]['article_id']=='000250'
    assert client.get('/catalog',params={'limit':10000}).status_code==422
    assert set(client.get('/customers').json()['items'])=={'buyer','old-only','other'}
    assert client.get('/streams').json()['items'][0]['stream_id']==stream
    assert client.post(f'/admin/streams/{stream}/refresh',json={'data_as_of':'2020-09-10'}).status_code==403
    r=client.post(f'/admin/streams/{stream}/refresh',json={'data_as_of':'2020-09-10'},headers={'Authorization':'Bearer local-test-token'})
    assert r.status_code==200 and r.json()['data_as_of']=='2020-09-10'


def test_concurrent_duplicate_events_and_failed_commit_reply(events,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from hm_recommender.events.store import EventStore
    from hm_recommender.api.schemas import EventInput
    serving,stream=events;payload=event(events)
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes=list(pool.map(lambda _:serving[0].post('/events',json=payload).status_code,range(12)))
    assert codes.count(201)==1 and codes.count(200)==11
    refresh(events)
    with serving[4]() as conn:
        assert conn.execute('SELECT frequency FROM operational_daily WHERE stream_id=%s AND customer_id=%s',(stream,'new-customer')).fetchone()[0]==1
    original=EventStore.insert
    def commit_then_disconnect(self,event):
        original(self,event);raise OSError('lost commit reply')
    monkeypatch.setattr(EventStore,'insert',commit_then_disconnect)
    second=event(events)
    assert serving[0].post('/events',json=second).status_code==503
    monkeypatch.setattr(EventStore,'insert',original)
    assert serving[0].post('/events',json=second).status_code==200


def test_receipt_clock_starts_after_waiting_for_publication_lock(events):
    from concurrent.futures import ThreadPoolExecutor
    import threading,time
    from hm_recommender.events.store import EventStore
    from hm_recommender.api.schemas import EventInput
    from hm_recommender.events.aggregate import aggregate_events
    serving,stream=events;ready=threading.Event();pid=[]
    def connection():
        conn=serving[4]();pid.append(conn.info.backend_pid);ready.set();return conn
    with ThreadPoolExecutor(max_workers=1) as pool:
        with serving[4]() as blocker:
            blocker.execute('SELECT 1 FROM event_streams WHERE stream_id=%s FOR UPDATE',(stream,))
            future=pool.submit(EventStore(connection).insert,EventInput.model_validate(event(events)))
            assert ready.wait(5)
            deadline=time.monotonic()+5
            while True:
                with serving[4]() as conn:
                    waiting=conn.execute("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=%s",(pid[0],)).fetchone()[0]
                if waiting:break
                assert time.monotonic()<deadline
                time.sleep(.01)
            watermark=datetime.now(timezone.utc)
        assert future.result(timeout=5)['status']=='created'
    result=aggregate_events(serving[2].release_id,watermark,stream_id=stream,connection=serving[4])
    assert result['processed_events']==0 and recommend(events)['fallback']
    assert refresh(events)['processed_events']==1
    assert not recommend(events)['fallback']
