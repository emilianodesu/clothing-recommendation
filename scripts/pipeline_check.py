"""One T6 rehearsal after explicit preparation. Aggregates only; no training/test."""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from bootstrap import read_configuration
from clock_check import checkout_commit, runner_digest
from clock_guard import AwakeGuard
from events_memory import capture
from handoff import now, package_report, read_report, write_json
from pipeline_handoff import CHECKS, SERVICES, empty_result, validate, build_proof, validate_code, AIRFLOW
from project import compose

ROOT=Path(__file__).resolve().parents[1]


def require(value,message='T6 control failed'):
    if not value:raise ValueError(message)


def _run(root,machine,commit,source_run=None):
    root=Path(root);checkout_commit(root,commit)
    values=read_configuration(root/'.env');values['HMREC_SOURCE_COMMIT']=commit
    artifacts=Path(values['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else root/artifacts
    rid='pipeline-'+machine+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    handoff=artifacts/'handoff';handoff.mkdir(exist_ok=True)
    # Production is one-shot; synthetics may use a new isolated checkout/project.
    if source_run:
        require(not list(handoff.glob('pipeline-'+machine+'-*')),'Existing T6 attempt; inspect instead of repeating')
    folder=handoff/rid;folder.mkdir(mode=0o700)
    report=dict(schema_version=1,kind='pipeline-handoff',run_id=rid,machine_id=machine,context=os.environ.get('DOCKER_CONTEXT','default'),
        source_commit=commit,source_clean=True,started_at=now(),finished_at=None,status='failed',warnings=[],steps=[])
    result=empty_result();result['dataset']='hm' if source_run else 'synthetic'
    environment=dict(source_commit=commit,source_tree_sha256='',runner_tree_sha256=runner_digest(root))
    sys.path.insert(0,str(root/'src'))
    from hm_recommender.api.runtime import source_digest
    environment['source_tree_sha256']=source_digest(root/'src/hm_recommender')
    started=time.monotonic();wall=time.time();guard=AwakeGuard();code=1
    def call(*args,timeout=120):return compose(root,values,*args,capture=True,timeout=timeout).strip()
    def airflow(*args):return call('exec','-T','airflow-scheduler','/opt/airflow-env/bin/python','/opt/airflow/entrypoint.py',*args)
    def db(query,args=(),service='app'):
        program="import json,sys;from hm_recommender.database import connect\nwith connect(sys.argv[1]) as c:print(json.dumps(c.execute(sys.argv[2],json.loads(sys.argv[3])).fetchall(),default=str))"
        return json.loads(call('exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',program,service,query,json.dumps(args)))
    def http(path,body=None,expected=200):
        c=http.client.HTTPConnection('127.0.0.1',int(values['API_PORT']),timeout=30)
        try:
            c.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,
                headers={'Content-Type':'application/json','Authorization':'Bearer '+values['HM_APP_PASSWORD']})
            r=c.getresponse();raw=r.read(1024*1024+1);require(r.status==expected and len(raw)<=1024*1024)
            return json.loads(raw)
        finally:c.close()
    def snapshots(phase):
        for name in SERVICES:
            cid=call('ps','-q',name)
            s=capture(cid,name,'before_seed' if phase=='before' else 'after_replay',started)
            result['resources'].append(dict(service=name,phase=phase,container_id=s['container_id'],started_at=s['started_at'],image_id=s['image_id'],restart_count=s['restart_count'],cgroup_inode=s['cgroup_inode'],
                memory_bytes=s['memory_bytes'],nano_cpus=s['nano_cpus'],peak_bytes=s['peak_bytes'],swap_peak_bytes=s['swap_peak_bytes'],
                oom=s['events']['oom'],oom_kill=s['events']['oom_kill'],max_events=s['events']['max']))
    def wait_for(predicate,seconds=2100):
        deadline=time.monotonic()+seconds
        while True:
            guard.check()
            require(abs((time.time()-wall)-(time.monotonic()-started))<2,'Clock discontinuity')
            value=predicate()
            if value:return value
            require(time.monotonic()<deadline,'Deadline exceeded; preserve running job and inspect')
            time.sleep(5)
    try:
        guard.__enter__();print('T6: '+rid,flush=True)
        # No hidden bootstrap/recreation: these services must already be prepared.
        snapshots('before')
        require(http('/health/live')['source_tree_sha256']==environment['source_tree_sha256'],'API code differs from checkout')
        result['dag_tree_sha256']=source_digest(root/'dags')
        for service in AIRFLOW:
            program="import json;from hm_recommender.api.runtime import source_digest;print(json.dumps(dict(source_tree_sha256=source_digest(),dag_tree_sha256=source_digest('/opt/airflow/dags'))))"
            result['installed_code'][service]=json.loads(call('exec','-T',service,'/opt/hmrec/.venv/bin/python','-c',program))
        validate_code(result['installed_code'],environment['source_tree_sha256'],result['dag_tree_sha256'])
        if source_run:
            archive=artifacts/'handoff'/(source_run+'.zip')
            if not archive.exists():archive=artifacts/'incoming'/(source_run+'.zip')
            old=read_report(archive);require(old['status']=='passed' and old['result']['protocol']=='events-operational-v1')
            stream=old['result']['stream_id'];release=old['result']['source']['release_id']
            result.update(source_run_id=source_run,source_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
            oldpg=next(s for s in reversed(old['result']['diagnostics']['snapshots']) if s['service']=='postgres')
            pg=next(s for s in result['resources'] if s['service']=='postgres')
            require(all(pg[k]==oldpg[k] for k in ('container_id','started_at','image_id')),'Historical PostgreSQL identity changed')
        else:
            release=http('/health/ready')['release_id'];stream=str(uuid.uuid4())
            print('T6: seed synthetic stream',flush=True)
            call('run','--rm','--no-deps','-T','events-job','hmrec','events','seed','--dataset','/artifacts/t6-demo/input/dataset',
                 '--directory','/artifacts/pipeline/release','--stream-id',stream,'--mode','synthetic',timeout=1800)
        require(http('/health/ready')['release_id']==release,'Active H changed')
        config_bytes=(artifacts/'pipeline/build.json').read_bytes();config=json.loads(config_bytes)
        receipt=json.loads((artifacts/'pipeline/build.receipt.json').read_text())
        runs=db("SELECT run_id,state,start_date,end_date FROM dag_run WHERE dag_id='hm_build_release' AND run_id=%s",(receipt.get('orchestrator_run_id'),),service='airflow')
        require(len(runs)==1,'Missing exact build run')
        manifest=json.loads(call('run','--rm','--no-deps','-T','jobs','hmrec','release','verify',config['output_dir'],timeout=300))
        manifest_sha=call('exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',"import hashlib,sys;from pathlib import Path;print(hashlib.sha256((Path(sys.argv[1])/'manifest.json').read_bytes()).hexdigest())",config['output_dir'])
        frozen=json.loads((root/'configs/releases/history-v1.json').read_text())
        result['build']=build_proof(config_bytes,receipt,manifest,manifest_sha,runs[0],commit,bool(source_run),frozen)
        result['checks']['build_completed']=True
        call('run','--rm','--no-deps','-T','jobs','hmrec','pipeline','register','--stream-id',stream)
        customer=http('/customers')['items'][0];article=http('/catalog?limit=2')['items'][-1]['article_id']
        def base():
            b=http('/recommend',{'customer_id':customer,'k':12});b.pop('request_id',None);return b
        original=base();profile=lambda:http('/streams/'+stream+'/recommend',{'customer_id':customer,'k':12})
        count=db('SELECT processed_events FROM event_streams WHERE stream_id=%s',(stream,))[0][0]
        for index in range(2):
            if index:
                print('T6: stop scheduler, receive pending event, then restart',flush=True)
                snapshots('pre_restart')
                call('stop','airflow-scheduler')
            event=dict(event_id=str(uuid.uuid4()),stream_id=stream,release_id=release,customer_id=customer,article_id=article,
                       kind='purchase',event_time='2020-09-08T12:00:00Z',origin='simulation' if source_run else 'synthetic')
            http('/events',event,201);require(http('/events',event)['status']=='duplicate')
            result['checks']['idempotent']=True
            if index:
                call('start','airflow-scheduler')
                snapshots('post_restart')
            airflow('dags','unpause','hm_refresh_profiles')
            print('T6: waiting for scheduled refresh '+str(index+1),flush=True)
            expected=count+index+1
            def completed():
                row=db('SELECT processed_events,published_at FROM event_streams WHERE stream_id=%s',(stream,))[0]
                return row if row[0]>=expected else None
            row=wait_for(completed);require(row[0]==expected)
            receipt=db('SELECT received_at FROM interaction_events WHERE event_id=%s',(event['event_id'],))[0][0]
            latency=(datetime.fromisoformat(row[1])-datetime.fromisoformat(receipt)).total_seconds()
            result['freshness_seconds'].append(latency);result['processed_events']+=1
            require(latency<600,'Freshness exceeded 10 minutes')
            result['checks']['scheduled_refresh' if not index else 'restart_pending']=True
            require(profile()['revision']>=1)
            # The profile transaction commits before Airflow marks the task successful.
            # Wait for that terminal state before intentionally stopping the scheduler.
            wait_for(lambda: db("SELECT count(*) FROM dag_run WHERE dag_id='hm_refresh_profiles' AND state='success' AND run_type='scheduled' AND start_date >= %s",(report['started_at'],),service='airflow')[0][0]>=index+1,120)
        def completed_runs():
            count=db("SELECT count(*) FROM dag_run WHERE dag_id='hm_refresh_profiles' AND state='success' AND run_type='scheduled' AND start_date >= %s",(report['started_at'],),service='airflow')[0][0]
            return count if count>=2 else None
        result['scheduled_runs']=wait_for(completed_runs,120)
        result['checks']['base_unchanged']=base()==original
        code=0
    except BaseException as error:
        result['error_type']=type(error).__name__;code=130 if isinstance(error,KeyboardInterrupt) else 1
        print('T6 stopped: '+type(error).__name__+'; inspect private state, do not repeat',flush=True)
    finally:
        try:
            for dag in ('hm_build_release','hm_refresh_profiles'):airflow('dags','pause',dag)
            result['checks']['dags_paused']=db("SELECT count(*) FROM dag WHERE dag_id IN ('hm_build_release','hm_refresh_profiles') AND is_paused",service='airflow')[0][0]==2
            snapshots('after')
            pg=[s for s in result['resources'] if s['service']=='postgres']
            result['checks']['postgres_preserved']=len(pg)==4 and all(s[k]==pg[0][k] for s in pg for k in ('container_id','started_at','image_id','cgroup_inode','restart_count'))
        except BaseException as error:
            result['error_type']=type(error).__name__;code=1
        try:guard.__exit__(None,None,None)
        except BaseException as error:result['error_type']=type(error).__name__;code=1
    result['duration_seconds']=time.monotonic()-started
    report['status']=result['status']='passed' if code==0 else 'interrupted' if code==130 else 'failed'
    if code==0:
        try:validate(report,result,environment)
        except ValueError:report['status']=result['status']='failed';result['error_type']='AcceptanceFailure';code=1
    report.update(result=result,finished_at=now(),steps=[dict(name='pipeline',log='job.log',status=report['status'],exit_code=code)])
    write_json(folder/'result.json',result);write_json(folder/'environment.json',environment)
    (folder/'commit.txt').write_text(commit+'\n');(folder/'job.log').write_text('pipeline T6\nstatus: '+report['status']+'\n')
    archive=package_report(folder,report)
    print('Reporte cerrado: '+str(archive),flush=True)
    print('Estado: '+report['status']+'; T6 requiere revisión principal.',flush=True)
    return code


def run(root,machine,commit,source_run=None):
    import fcntl
    root=Path(root);values=read_configuration(root/'.env')
    artifacts=Path(values['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else root/artifacts
    with (artifacts/'.heavy-launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _run(root,machine,commit,source_run)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--machine-id',required=True);p.add_argument('--expected-commit',required=True);p.add_argument('--source-run-id')
    args=p.parse_args(argv)
    import re
    require(re.fullmatch('[a-z][a-z0-9_-]{0,31}',args.machine_id))
    if args.source_run_id:require(re.fullmatch('events-[a-zA-Z0-9_-]{1,90}',args.source_run_id))
    return run(ROOT,args.machine_id,args.expected_commit,args.source_run_id)
