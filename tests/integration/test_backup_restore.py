import importlib.util
from pathlib import Path
import os
import uuid
import pytest
from test_pipeline import pipeline_schema
from test_events import events, event, recommend
from test_recommendations import serving
pytestmark=pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS')!='1',reason='isolated PostgreSQL required')


def backup_module():
    assert importlib.util.find_spec('hm_recommender.portability'), 'backup module missing'
    from hm_recommender import portability
    return portability

@pytest.fixture
def empty_database(events):
    from hm_recommender.database import connect
    schema='test_restore_'+uuid.uuid4().hex
    with connect() as c:c.execute('CREATE SCHEMA '+schema)
    def connection():
        c=connect();c.execute('SET search_path TO '+schema);c.commit();return c
    with connection() as c:
        for p in sorted(Path('/opt/hmrec/migrations').glob('00[234]_*.sql')):c.execute(p.read_text())
    yield connection
    with connect() as c:c.execute('DROP SCHEMA '+schema+' CASCADE')

def test_restore_into_empty_database_preserves_profiles_and_rejects_nonempty(events,pipeline_schema,empty_database,tmp_path):
    m=backup_module();s,stream=events
    from hm_recommender.pipeline.runner import register,refresh
    register(stream,connection=s[4]);s[0].post('/events',json=event(events));refresh(tmp_path/'reports',connection=s[4])
    archive=m.backup(tmp_path/'backup.zip',s[1].root,connection=s[4])
    target=tmp_path/'new registry'
    result=m.restore(archive,target,connection=empty_database)
    assert result['status']=='passed'
    from hm_recommender.events.store import EventStore
    assert EventStore(s[4]).profile(stream,'buyer')==EventStore(empty_database).profile(stream,'buyer')
    with empty_database() as c:assert c.execute('SELECT count(*) FROM interaction_events').fetchone()[0]==1
    from hm_recommender.releases.registry import ReleaseRegistry
    r=ReleaseRegistry(target,empty_database);r.open()
    try:assert r.snapshot().rank('buyer',{})==s[1].snapshot().rank('buyer',{})
    finally:r.close()
    with pytest.raises(ValueError):m.restore(archive,tmp_path/'another',connection=empty_database)

def test_failed_backup_releases_write_locks(events,pipeline_schema,tmp_path,monkeypatch):
    m=backup_module();s,_=events
    monkeypatch.setattr(m,'export_release',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):m.backup(tmp_path/'failed.zip',s[1].root,connection=s[4])
    assert not (tmp_path/'failed.zip').exists()
    assert s[0].post('/events',json=event(events)).status_code==201

def test_backup_blocks_writes_but_allows_reads(events,pipeline_schema,tmp_path,monkeypatch):
    m=backup_module();s,_=events;original=m.export_release;checks=[]
    def inspect(*args):
        import psycopg
        with s[4]() as c:
            assert c.execute('SELECT count(*) FROM event_streams').fetchone()[0]==1
            c.execute("SET LOCAL lock_timeout='100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):c.execute('UPDATE event_streams SET revision=revision+1')
        checks.append(True)
        return original(*args)
    monkeypatch.setattr(m,'export_release',inspect)
    m.backup(tmp_path/'backup.zip',s[1].root,connection=s[4]);assert checks

def test_corrupt_backup_rejected_before_database_changes(events,pipeline_schema,empty_database,tmp_path):
    m=backup_module();s,_=events
    archive=m.backup(tmp_path/'backup.zip',s[1].root,connection=s[4])
    import zipfile
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive) as src,zipfile.ZipFile(bad,'w') as dst:
        for name in src.namelist():dst.writestr(name,b'corrupt' if name=='event_streams.csv' else src.read(name))
    with pytest.raises(ValueError):m.restore(bad,tmp_path/'restored-registry',connection=empty_database)
    with empty_database() as c:assert c.execute('SELECT active_id FROM serving_pointer').fetchone()==(None,)
    assert not (tmp_path/'restored-registry').exists()
