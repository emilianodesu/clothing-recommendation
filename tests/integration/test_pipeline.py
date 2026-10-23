"""Real PostgreSQL retries and receipt boundaries; no scientific test split."""
from datetime import datetime, timedelta, timezone
import importlib.util
import os
import pytest
from test_events import events, event, recommend
from test_recommendations import serving

pytestmark=pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS')!='1',reason='isolated PostgreSQL required')


@pytest.fixture(autouse=True)
def pipeline_schema(events):
    from pathlib import Path
    with events[0][4]() as conn:conn.execute(Path("/opt/hmrec/migrations/004_pipeline.sql").read_text())


def module():
    assert importlib.util.find_spec('hm_recommender.pipeline') and importlib.util.find_spec('hm_recommender.pipeline.runner'), 'pipeline runner missing'
    from hm_recommender.pipeline import runner
    return runner


def test_restart_processes_pending_events_once(events,tmp_path):
    m=module();s,stream=events;client=s[0];connection=s[4]
    m.register(stream,connection=connection)
    payload=event(events);assert client.post('/events',json=payload).status_code==201
    first=m.refresh(tmp_path,connection=connection)
    assert first['processed_events']==1 and first['status']=='passed'
    revision=recommend(events)['revision']
    assert m.refresh(tmp_path,connection=connection)['processed_events']==0
    assert recommend(events)['revision']==revision
    assert len(list(tmp_path.glob('*.json')))==2


def test_failed_refresh_does_not_advance_cursor(events,tmp_path,monkeypatch):
    m=module();s,stream=events;connection=s[4];m.register(stream,connection=connection)
    s[0].post('/events',json=event(events));real=m.aggregate_events
    def fail(*args,**kwargs):raise OSError('private details must not be exported')
    monkeypatch.setattr(m,'aggregate_events',fail)
    with pytest.raises(OSError):m.refresh(tmp_path,connection=connection)
    import json
    report=json.loads(next(tmp_path.glob('*.json')).read_text());assert report['status']=='failed' and report['error_type']=='OSError'
    assert 'private details' not in json.dumps(report)
    monkeypatch.setattr(m,'aggregate_events',real)
    assert m.refresh(tmp_path,connection=connection)['processed_events']==1


def test_unregistered_stream_never_processed(events,tmp_path):
    m=module();events[0][0].post('/events',json=event(events));revision=recommend(events)['revision']
    assert m.refresh(tmp_path,connection=events[0][4])['targets']==0
    assert recommend(events)['revision']==revision


def test_window_is_bounded_after_sleep(events,tmp_path):
    m=module();s,stream=events;c=s[4];m.register(stream,connection=c)
    with c() as conn:
        conn.execute("UPDATE pipeline_targets SET receipt_cursor=clock_timestamp()-interval '1 day' WHERE stream_id=%s",(stream,))
        conn.execute("UPDATE event_streams SET received_before=clock_timestamp()-interval '1 day' WHERE stream_id=%s",(stream,))
    for age in (1200,600):
        payload=event(events);s[0].post('/events',json=payload)
        with c() as conn:conn.execute("UPDATE interaction_events SET received_at=clock_timestamp()-(%s * interval '1 second') WHERE event_id=%s",(age,payload['event_id']))
    result=m.refresh(tmp_path,connection=c)
    assert result['processed_events']==1 and result['pending_events']==1
    assert m.refresh(tmp_path,connection=c)['processed_events']==1


def test_failure_after_aggregation_rolls_back_profiles_and_cursor(events,tmp_path,monkeypatch):
    m=module();s,stream=events;c=s[4];m.register(stream,connection=c);s[0].post('/events',json=event(events))
    revision_before=recommend(events)['revision']
    real=m.aggregate_events
    def crash(*args,**kwargs):
        real(*args,**kwargs)
        raise RuntimeError('after commit')
    monkeypatch.setattr(m,'aggregate_events',crash)
    with pytest.raises(RuntimeError):m.refresh(tmp_path,connection=c)
    revision=recommend(events)['revision'];assert revision==revision_before
    monkeypatch.setattr(m,'aggregate_events',real)
    assert m.refresh(tmp_path,connection=c)['status']=='passed'
    assert recommend(events)['revision']==revision+1


def test_failed_build_never_calls_publication(events,tmp_path,monkeypatch):
    from hm_recommender.pipeline import runner
    assert hasattr(runner,'build'), 'build pipeline missing'
    import json
    config=tmp_path/'build.json';config.write_text(json.dumps(dict(dataset_dir=str(tmp_path/'missing'),output_dir=str(tmp_path/'release'),data_as_of='2020-09-09',activate=True)))
    called=[];monkeypatch.setattr(runner,'publish',lambda *a,**k:called.append(True))
    with pytest.raises((ValueError,FileNotFoundError)):runner.build(config,tmp_path/'reports',connection=events[0][4],roots=(tmp_path,))
    assert called==[]
    assert next((tmp_path/'reports').glob('*.json')).is_file()


def test_retry_does_not_duplicate_partition_or_release(events,tmp_path):
    from hm_recommender.pipeline import runner
    assert hasattr(runner,'build'), 'build pipeline missing'
    import json
    from hm_recommender.data.synthetic import make_zip
    source=make_zip(tmp_path/'source.zip')
    config=tmp_path/'build.json';config.write_text(json.dumps(dict(source_zip=str(source),synthetic=True,dataset_dir=str(tmp_path/'dataset'),output_dir=str(tmp_path/'package'),data_as_of='2020-09-09',activate=False)))
    one=runner.build(config,tmp_path/'reports',connection=events[0][4],roots=(tmp_path,))
    two=runner.build(config,tmp_path/'reports',connection=events[0][4],roots=(tmp_path,))
    assert one['release_id']==two['release_id'] and one['rows']==two['rows']
    assert one['published'] is False and two['published'] is False


def test_api_metrics_have_latency_errors_and_fallback_without_customer_ids(events):
    c=events[0][0]
    c.post('/recommend',json={'customer_id':'private-unknown-customer','k':3})
    c.post('/recommend',json={'customer_id':'private-invalid','k':0})
    text=c.get('/metrics').text
    assert 'hmrec_http_seconds_bucket' in text
    assert 'hmrec_fallback_total 1' in text
    assert 'status="4xx"' in text
    assert 'private-unknown' not in text and 'private-invalid' not in text


def test_second_stream_registration_is_rejected_before_scheduling(events):
    m=module();s,stream=events;m.register(stream,connection=s[4])
    import uuid
    from hm_recommender.events.aggregate import initialize_stream
    other=str(uuid.uuid4());initialize_stream(s[5]/'data',s[2]._root,other,mode='synthetic',connection=s[4])
    with pytest.raises(ValueError):m.register(other,connection=s[4])


def test_publication_rejects_config_change_after_build(events,tmp_path):
    m=module();import json
    config=tmp_path/'build.json';config.write_text(json.dumps(dict(dataset_dir=str(events[0][5]/'data'),output_dir=str(tmp_path/'package'),data_as_of='2020-09-09',synthetic=True,activate=False)))
    m.build(config,tmp_path/'reports',connection=events[0][4],roots=(tmp_path,events[0][5]))
    cfg=json.loads(config.read_text());cfg['activate']=True;config.write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='configuration changed'):m.publish(config,connection=events[0][4],roots=(tmp_path,events[0][5]))
