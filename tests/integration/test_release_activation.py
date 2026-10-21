"""Persistence and failure boundaries with a real PostgreSQL registry."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import pytest
from test_recommendations import serving, activate

pytestmark=pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS')!='1',reason='requires isolated PostgreSQL')


def test_activation_failure_keeps_previous_release(serving,monkeypatch):
    client,registry,a,b,connection,_=serving; activate(serving)
    registry.prepare_release(b._root)
    (registry.root/b.release_id/'catalog.parquet').write_bytes(b'corrupt')
    with pytest.raises(ValueError):registry.activate_release(b.release_id)
    assert client.get('/health/ready').json()['release_id']==a.release_id
    with connection() as conn: assert conn.execute('SELECT active_id FROM serving_pointer').fetchone()[0]==a.release_id


def test_failed_database_activation_never_exposes_candidate(serving,monkeypatch):
    client,registry,a,b,_,_=serving; activate(serving); registry.prepare_release(b._root)
    original=registry.store.activate
    def fail(*args):raise OSError('database unavailable')
    monkeypatch.setattr(registry.store,'activate',fail)
    with pytest.raises(OSError):registry.activate_release(b.release_id)
    # Commit outcome could be unknown: fail closed until recovery from durable pointer.
    assert client.get('/health/ready').status_code==503
    monkeypatch.setattr(registry.store,'activate',original)
    registry.recover()
    assert client.get('/health/ready').json()['release_id']==a.release_id


def test_concurrent_requests_never_mix_releases(serving):
    client,registry,a,b,_,_=serving; activate(serving);registry.prepare_release(b._root)
    def read(_):
        r=client.post('/recommend',json={'customer_id':'buyer','k':3})
        assert r.status_code==200
        return r.json()
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks=[pool.submit(read,n) for n in range(60)]
        registry.activate_release(b.release_id)
        tasks += [pool.submit(read,n) for n in range(60)]
        results=[t.result() for t in tasks]
    for r in results:
        expected=['000000','000001','000002'] if r['release_id']==a.release_id else ['000254','000001']
        assert r['release_id'] in (a.release_id,b.release_id)
        assert [i['article_id'] for i in r['items']]==expected
        assert r['features_timestamp']==('2020-09-09' if r['release_id']==a.release_id else '2020-09-08')


def test_restart_recovers_committed_release_and_rollback(serving):
    from hm_recommender.releases.registry import ReleaseRegistry
    _,registry,a,b,connection,_=serving;activate(serving);registry.prepare_release(b._root)
    registry.activate_release(b.release_id);registry.close()
    restarted=ReleaseRegistry(registry.root,connection)
    restarted.open()
    try:
        assert restarted.snapshot().release_id==b.release_id
        restarted.rollback()
        assert restarted.snapshot().release_id==a.release_id
        restarted.activate_release(a.release_id)  # Idempotent activation must not erase previous.
        restarted.rollback()
        assert restarted.snapshot().release_id==b.release_id
    finally:restarted.close()


def test_only_one_api_process_owns_registry(serving):
    from hm_recommender.releases.registry import ReleaseRegistry
    _,registry,_,_,connection,_=serving
    competing=ReleaseRegistry(registry.root,connection)
    with pytest.raises(RuntimeError):competing.open()


def test_prepare_copies_verified_state_without_activating(serving):
    client,registry,a,*_=serving
    first=registry.prepare_release(a._root); second=registry.prepare_release(a._root)
    assert first==second
    assert client.get('/health/ready').status_code==503
    (a._root/'catalog.parquet').write_bytes(b'source changed after preparation')
    registry.activate_release(a.release_id)
    assert client.post('/recommend',json={'customer_id':'buyer'}).status_code==200


def test_lost_commit_reply_fails_closed_then_recovers_new_pointer(serving,monkeypatch):
    client,registry,a,b,_,_=serving;activate(serving);registry.prepare_release(b._root)
    original=registry.store.activate
    def commit_then_disconnect(release_id):
        original(release_id)
        raise OSError('lost commit reply')
    monkeypatch.setattr(registry.store,'activate',commit_then_disconnect)
    with pytest.raises(OSError):registry.activate_release(b.release_id)
    assert client.get('/health/ready').status_code==503
    registry.recover()
    assert client.get('/health/ready').json()['release_id']==b.release_id


def test_lost_owner_session_stops_serving(serving):
    client,registry,*_=serving;activate(serving)
    registry.store.lease.close()
    assert client.post('/recommend',json={'customer_id':'buyer'}).status_code==503


def test_owner_loss_between_check_and_write_cannot_change_replacement_pointer(serving,monkeypatch):
    import psycopg
    from hm_recommender.releases.registry import ReleaseRegistry
    client,registry,a,b,connection,_=serving
    activate(serving);registry.prepare_release(b._root)
    original=registry.store.activate
    replacement=ReleaseRegistry(registry.root,connection)

    def lose_lease_then_write(release_id):
        pid=registry.store.lease.info.backend_pid
        with connection() as conn:
            assert conn.execute('SELECT pg_terminate_backend(%s)',(pid,)).fetchone()[0]
        replacement.open()
        assert replacement.snapshot().release_id==a.release_id
        original(release_id)

    monkeypatch.setattr(registry.store,'activate',lose_lease_then_write)
    try:
        with pytest.raises(psycopg.OperationalError):registry.activate_release(b.release_id)
        assert client.get('/health/ready').status_code==503
        assert replacement.store.pointer()[0]==a.release_id
        assert replacement.snapshot().release_id==a.release_id
    finally:replacement.close()


def test_corrupt_committed_release_blocks_restart(serving):
    from hm_recommender.releases.registry import ReleaseRegistry
    _,registry,a,_,connection,_=serving;activate(serving);registry.close()
    (registry.root/a.release_id/'histories.parquet').write_bytes(b'broken')
    restarted=ReleaseRegistry(registry.root,connection)
    with pytest.raises(ValueError):restarted.open()
