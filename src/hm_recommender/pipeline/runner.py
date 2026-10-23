"""Retry-safe opt-in refresh. Reports expose counts, never customer/event payloads."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import resource
import time
import uuid
from hm_recommender.database import connect
from hm_recommender.data.io import atomic_json
from hm_recommender.events.aggregate import aggregate_events, stream_identity

LOCK=784226


@contextmanager
def heavy_lock(connection=connect):
    with connection() as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(%s)',(LOCK,)).fetchone()[0]:
            raise RuntimeError('Another pipeline job is active')
        try:yield
        finally:conn.execute('SELECT pg_advisory_unlock(%s)',(LOCK,))


def register(stream_id, *, connection=connect):
    stream_identity(stream_id)
    with connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
        if conn.execute('SELECT 1 FROM pipeline_targets WHERE enabled AND stream_id<>%s',(stream_id,)).fetchone():raise ValueError('Only one stream may be scheduled')
        row=conn.execute('SELECT release_id,data_as_of,received_before,revision FROM event_streams WHERE stream_id=%s FOR UPDATE',(stream_id,)).fetchone()
        active=conn.execute('SELECT active_id FROM serving_pointer').fetchone()
        if row is None or row[3]<1 or active is None or row[0]!=active[0]:raise ValueError('A seeded active stream is required')
        saved=conn.execute('SELECT release_id,data_as_of FROM pipeline_targets WHERE stream_id=%s',(stream_id,)).fetchone()
        if saved is not None and saved!=row[:2]:raise ValueError('Registered stream clock/version changed')
        conn.execute('INSERT INTO pipeline_targets(stream_id,release_id,data_as_of,receipt_cursor) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(stream_id,*row[:3]))
    return {'registered':True}


def refresh(reports: Path, *, connection=connect):
    reports=Path(reports);reports.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();report=dict(schema_version=1,kind='pipeline-refresh',status='running',run_id='refresh-'+uuid.uuid4().hex,
        started_at=datetime.now(timezone.utc).isoformat(),targets=0,processed_events=0,pending_events=0,max_receipt_to_publish_seconds=0.,error_type=None,
        window_seconds=300,event_limit=1000,training_performed=False,test_evaluated=False)
    try:
        with heavy_lock(connection):
            with connection() as conn:
                targets=conn.execute('SELECT stream_id FROM pipeline_targets WHERE enabled ORDER BY stream_id LIMIT 2').fetchall()
                if len(targets)>1:raise ValueError('One explicit stream target is supported within the local budget')
            for (stream,) in targets:
                with connection() as conn:
                    target=conn.execute('SELECT release_id,data_as_of,receipt_cursor FROM pipeline_targets WHERE stream_id=%s AND enabled FOR UPDATE',(stream,)).fetchone()
                    if target is None:continue
                    release,cutoff,cursor=target
                    state=conn.execute('SELECT release_id,data_as_of FROM event_streams WHERE stream_id=%s FOR UPDATE',(stream,)).fetchone()
                    active=conn.execute('SELECT active_id FROM serving_pointer').fetchone()
                    if state!=(release,cutoff) or active is None or active[0]!=release:raise ValueError('Target no longer matches active stream/version')
                    now=conn.execute('SELECT clock_timestamp()').fetchone()[0]
                    receipts=conn.execute('SELECT received_at FROM interaction_events WHERE stream_id=%s AND received_at >= %s AND received_at < %s ORDER BY received_at LIMIT 1001',(stream,cursor,now)).fetchall()
                    report['targets']+=1
                    end=min(now,receipts[0][0]+timedelta(seconds=300)) if receipts else now
                    if len(receipts)>1000:end=min(end,receipts[1000][0])
                    if receipts and end<=receipts[0][0]:raise ValueError('Too many events share a receipt timestamp; no cursor advanced')
                    count=sum(r[0]<end for r in receipts)
                    if count:
                        result=aggregate_events(release,end,stream_id=str(stream),data_as_of=cutoff,connection=lambda:nullcontext(conn))
                        published=datetime.fromisoformat(result['published_at'])
                        report['max_receipt_to_publish_seconds']=max(report['max_receipt_to_publish_seconds'],(published-receipts[0][0]).total_seconds())
                    conn.execute('UPDATE pipeline_targets SET receipt_cursor=%s,updated_at=clock_timestamp() WHERE stream_id=%s',(end,stream))
                    report['processed_events']+=count
                    report['pending_events']+=conn.execute('SELECT count(*) FROM interaction_events WHERE stream_id=%s AND received_at >= %s',(stream,end)).fetchone()[0]
            report['status']='passed'
        return report
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__)
        raise
    finally:
        report.update(finished_at=datetime.now(timezone.utc).isoformat(),duration_seconds=time.monotonic()-started,
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                      freshness_goal_met=report['status']=='passed' and report['pending_events']==0 and report['max_receipt_to_publish_seconds']<600)
        atomic_json(reports/(report['run_id']+'.json'),report)


def build_configuration(path, roots):
    import json
    from datetime import date
    from pydantic import BaseModel, ConfigDict
    class Build(BaseModel):
        model_config=ConfigDict(extra='forbid',strict=True)
        dataset_dir:str
        output_dir:str
        data_as_of:str
        source_zip:str|None=None
        synthetic:bool=False
        evidence_archive:str|None=None
        activate:bool=False
    cfg=Build.model_validate(json.loads(Path(path).read_text())).model_dump()
    cfg['data_as_of']=date.fromisoformat(cfg['data_as_of'])
    # The held-out cutoff remains reserved for T8, including when called from a DAG.
    if cfg['data_as_of']!=date(2020,9,9):raise ValueError('T6 uses the frozen validation cutoff only')
    for key in ('dataset_dir','output_dir','source_zip','evidence_archive'):
        if cfg[key] is None:continue
        p=Path(cfg[key])
        if not p.is_absolute() or p.is_symlink() or not any(p.resolve().is_relative_to(Path(r).resolve()) for r in roots):raise ValueError('Pipeline path is outside its authorized roots')
        cfg[key]=p
    return cfg


def build(config_path, reports, *, connection=connect, roots=(Path('/data'),Path('/artifacts'))):
    from hm_recommender.data.ingest import ingest,load_dataset
    from hm_recommender.data.validate import HM_SOURCE
    from hm_recommender.data.io import sha256
    from hm_recommender.releases.package import build_history_release
    report=dict(schema_version=1,kind='pipeline-build',run_id='build-'+uuid.uuid4().hex,status='running',published=False,error_type=None)
    start=time.monotonic();reports=Path(reports);reports.mkdir(parents=True,exist_ok=True)
    try:
        with heavy_lock(connection):
            cfg=build_configuration(config_path,roots)
            report['config_sha256']=sha256(Path(config_path))
            if cfg['source_zip']:
                dataset=ingest(cfg['source_zip'],cfg['dataset_dir'],expected=None if cfg['synthetic'] else HM_SOURCE)
            else:dataset=load_dataset(cfg['dataset_dir'])
            if not cfg['synthetic'] and dataset.source_sha256!=HM_SOURCE['sha256']:raise ValueError('Expected verified H&M source')
            if not cfg['synthetic'] and cfg['evidence_archive'] is None:raise ValueError('H&M publication requires frozen Q4 evidence')
            package=build_history_release(dataset,cfg['data_as_of'],cfg['output_dir'],evidence_archive=cfg['evidence_archive'])
            report.update(status='passed',release_id=package.release_id,rows=dataset.counts,dataset_manifest_sha256=package.dataset_manifest_sha256)
            atomic_json(Path(config_path).with_suffix('.receipt.json'),dict(config_sha256=report['config_sha256'],release_id=package.release_id))
        return report
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__);raise
    finally:
        report.update(duration_seconds=time.monotonic()-start,finished_at=datetime.now(timezone.utc).isoformat(),peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
        atomic_json(reports/(report['run_id']+'.json'),report)


def publish(config_path, *, connection=connect, roots=(Path('/data'),Path('/artifacts'))):
    import os
    import httpx
    from hm_recommender.releases.package import verify_history_release
    import json
    from hm_recommender.data.io import sha256
    cfg=build_configuration(config_path,roots)
    receipt=json.loads(Path(config_path).with_suffix('.receipt.json').read_text())
    if receipt['config_sha256']!=sha256(Path(config_path)):raise ValueError('Build configuration changed; rebuild before publication')
    # A manual build does not authorize activation unless this explicit flag is set.
    if not cfg['activate']:return dict(published=False,reason='activation_disabled')
    with heavy_lock(connection):
        package=verify_history_release(cfg['output_dir'])
        if package.release_id!=receipt['release_id']:raise ValueError('Built package changed')
        if package.data_as_of!=cfg['data_as_of']:raise ValueError('Package cutoff differs')
        if not cfg['synthetic'] and not package.selection_archive_verified:raise ValueError('Q4 evidence not verified')
        relative=cfg['output_dir'].relative_to(Path('/artifacts'))
        with httpx.Client(base_url=os.environ.get('HMREC_API_URL','http://api:8000'),trust_env=False,timeout=300) as client:
            headers={'Authorization':'Bearer '+os.environ['HM_APP_PASSWORD']}
            response=client.post('/admin/releases/prepare',json={'directory':str(relative)},headers=headers)
            if response.status_code!=200:raise ValueError('Package preparation failed')
            ready=client.get('/health/ready')
            if ready.status_code==200 and ready.json().get('release_id')==package.release_id:return dict(published=True,release_id=package.release_id,reused=True)
            response=client.post('/admin/releases/activate',json={'release_id':package.release_id},headers=headers)
            if response.status_code!=200:raise ValueError('Activation not confirmed; inspect readiness before retry')
            return dict(published=True,release_id=package.release_id,reused=False)
