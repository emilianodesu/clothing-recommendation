#!/usr/bin/env python3
"""T8-C: one final evaluation in sequential maintenance; no automatic retry."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import uuid
from datetime import datetime,timezone
from contextlib import contextmanager
from clock_check import checkout_commit,runner_digest
from clock_guard import AwakeGuard
from handoff import write_json,now,package_report,read_report
from mac24_terminal import command,service,same_service,RELEASE
from mac24_t8_concurrent import load_context,state_hash,restored_ready,NEW,OLD
from mac24_t7 import check_airflow
from pipeline_check import request_json
from project import compose
from concurrency_transition import inspect,verify,stop
from final_control import reserve,recovery_allowed,maintenance_targets,restore_maintenance
from final_handoff import empty_result,validate,resources_ok,require,WINDOW
from events_memory import capture

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.runtime/t8-final-maintenance'

@contextmanager
def stage(name):
    print('T8-C: '+name,flush=True)
    try:yield
    except BaseException as e:
        print('FALLO: '+name+' ('+type(e).__name__+')',flush=True);raise


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def context(commit):
    v,art,base,cap,oldaux=load_context(commit)
    selection=json.loads((ROOT/'configs/releases/history-v1.json').read_text())
    concurrency=json.loads((ROOT/'evidence/concurrency-review-mac24.json').read_text())
    for rid,digest,status in [(selection['selection']['run_id'],selection['selection']['archive_sha256'],'passed'),(concurrency['run_id'],concurrency['archive']['sha256'],'failed')]:
        path=art/'handoff'/(rid+'.zip');require(sha(path)==digest and read_report(path)['status']==status)
    data=Path(v['DATA_DIR']);data=data if data.is_absolute() else ROOT/data
    dataset=data/'processed/hm-v1'
    require(sha(dataset/'manifest.json')==selection['dataset_manifest_sha256'])
    manifest=json.loads((dataset/'manifest.json').read_text());require(manifest['source_sha256']==selection['source_sha256'])
    return dict(v=v,art=art,base=base,cap=cap,selection=selection,concurrency=concurrency,dataset=dataset,aux=maintenance_targets(cap['result']['containers']))


def no_attempt(ctx):
    require(not STATE.exists() and not (ctx['art']/'.final-test-once.json').exists())
    require(not list((ctx['art']/'handoff').glob('final-mac24-*')) and not list((ctx['art']/'final-private').glob('final-mac24-*')))


def preflight(commit):
    with stage('checkout_and_evidence'):ctx=context(commit);no_attempt(ctx)
    v=ctx['v'];rows=ctx['cap']['result']['containers']
    with stage('hardware_power'):
        require(command(['uname','-m'])=='arm64' and command(['sysctl','-n','hw.memsize'])==str(24*2**30))
        require('AC Power' in command(['pmset','-g','batt']))
    with stage('inventory_and_capacity'):
        require(set(command(['docker','ps','--no-trunc','--format','{{.ID}}']).splitlines())=={r['container_id'] for r in rows})
        for row in rows:
            current=inspect(row['container_id']);verify(row,current);require(current['running'] and current['health'] in ('healthy','none'))
        engine=json.loads(command(['docker','info','--format','{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}']))
        retained=[r for r in rows if r not in ctx['aux']]
        require(engine['cpus']==4 and 13*2**30<=engine['memory']<=14*2**30)
        require(sum(r['memory_bytes'] for r in retained)+6*2**30<=engine['memory'])
        require(shutil.disk_usage(ctx['art']).free>=20*2**30)
    with stage('airflow_paused_idle'):
        check_airflow(ROOT,v)
        program="from hm_recommender.database import connect\nwith connect('airflow') as c:print(c.execute(\"SELECT count(*) FROM dag_run WHERE state IN ('running','queued')\").fetchone()[0])"
        require(compose(ROOT,v,'exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',program,capture=True,timeout=30).strip()=='0')
    with stage('sources_and_release'):
        code="from hm_recommender.api.runtime import source_digest;print(source_digest())"
        require(compose(ROOT,v,'exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',code,capture=True,timeout=30).strip()==OLD)
        require(request_json(int(v['API_PORT']),v['HM_APP_PASSWORD'],'/health/live')['source_tree_sha256']==NEW)
        require(request_json(int(v['API_PORT']),v['HM_APP_PASSWORD'],'/health/ready')['release_id']==RELEASE)
        restored_ready()
    logs=ROOT/'.runtime'/('t8-final-preflight-'+uuid.uuid4().hex);logs.mkdir(parents=True)
    with stage('host_tests'):command([sys.executable,'-m','unittest','discover','-s','tests/host','-q'],log=logs/'host-tests.log',timeout=180)
    ctx['before']={s:service(v,s) for s in ('api','postgres','ui')}
    return ctx


def container_args(name,image):
    return ['docker','run','--name',name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--memory','5g','--memory-swap','5g','--cpus','2','--pids-limit','256','--tmpfs','/tmp:rw,nosuid,size=512m',
        '--user',str(os.getuid())+':'+str(os.getgid()),'-e','POLARS_MAX_THREADS=2','-e','OMP_NUM_THREADS=2','-e','MKL_NUM_THREADS=2',
        '--entrypoint','python']


def recover(ctx,saved):
    # The deterministic name is saved before docker run, so a lost response cannot hide a worker.
    name=saved['worker_name']
    synthetic=command(['docker','ps','-a','--no-trunc','--filter','name=^/'+name+'-synthetic$','--format','{{.ID}}']).splitlines()
    require(len(synthetic)<=1)
    if synthetic:
        item=inspect(synthetic[0]);require(item['image_id']==saved['image_id'])
        if item['running']:command(['docker','stop','--time','15',synthetic[0]],timeout=45)
    ids=command(['docker','ps','-a','--no-trunc','--filter','name=^/'+name+'$','--format','{{.ID}}']).splitlines()
    require(len(ids)<=1)
    if ids:
        current=inspect(ids[0]);require(current['image_id']==saved['image_id'])
        private=Path(saved['private'])
        require(recovery_allowed(current['running'],(private/'go.json').exists(),(private/'complete.json').exists()))
        if current['running']:command(['docker','stop','--time','15',ids[0]],timeout=45)
        require(not inspect(ids[0])['running'])
    recovered=restore_maintenance(ctx['aux']);restored_ready();check_airflow(ROOT,ctx['v'])
    for s,identity in saved['before'].items():same_service(identity,service(ctx['v'],s))
    require(request_json(int(ctx['v']['API_PORT']),ctx['v']['HM_APP_PASSWORD'],'/health/ready')['release_id']==RELEASE)
    write_json(STATE/'recovery.json',recovered)
    return recovered


def restore_only(commit):
    ctx=context(commit)
    with (ctx['art']/'.heavy-launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        saved=json.loads((STATE/'state.json').read_text())
        require(saved['source_commit']==commit and saved['aux']==ctx['aux'])
        recover(ctx,saved)
    print('Servicios recuperados. Conservar reserva; no repetir test.',flush=True)


def run(commit):
    ctx=preflight(commit);art=ctx['art'];v=ctx['v'];selection=ctx['selection']
    with AwakeGuard() as awake,(art/'.heavy-launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        no_attempt(ctx);checkout_commit(ROOT,commit)
        rid='final-mac24-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        private=art/'final-private'/rid;private.mkdir(parents=True)
        STATE.mkdir(parents=True)
        saved=dict(source_commit=commit,aux=ctx['aux'],aux_before={r['container_id']:inspect(r['container_id']) for r in ctx['aux']},before=ctx['before'],worker_name='hmrec-'+rid,image_id=None,private=str(private),run_id=rid)
        write_json(STATE/'state.json',saved)
        r=empty_result();samples=[];started_at=now()
        env=dict(source_commit=commit,runner_tree_sha256=runner_digest(ROOT),worker_source_sha256='0'*64,image_sha256='0'*64,
            dataset_manifest_sha256=selection['dataset_manifest_sha256'],source_sha256=selection['source_sha256'],
            selection_archive_sha256=selection['selection']['archive_sha256'],baseline_archive_sha256=ctx['base']['archive']['sha256'],
            concurrency_archive_sha256=ctx['concurrency']['archive']['sha256'],state_before_sha256='0'*64,state_after_sha256='0'*64)
        try:
            env['state_before_sha256']=state_hash(v)
            with stage('maintenance_stop_six_auxiliaries'):stop(ctx['aux'])
            require(set(command(['docker','ps','--no-trunc','--format','{{.ID}}']).splitlines())=={r['container_id'] for r in ctx['cap']['result']['containers'] if r not in ctx['aux']})
            with stage('build_jobs_only'):
                compose(ROOT,v,'build','jobs',log_path=private/'build-private.log',timeout=1800)
                image=command(['docker','image','inspect','hmrec-jobs:0.1.0','--format','{{.Id}}'])
                saved['image_id']=image;env['image_sha256']=image.removeprefix('sha256:');write_json(STATE/'state.json',saved)
            with stage('worker_source_identity'):
                digest=hashlib.sha256()
                source_root=ROOT/'src/hm_recommender'
                for path in sorted(source_root.rglob('*.py')):
                    digest.update(str(path.relative_to(source_root)).encode()+b'\0'+path.read_bytes()+b'\0')
                expected=digest.hexdigest()
                actual=command(container_args(saved['worker_name']+'-synthetic',image)+['--rm',image,'-c','from hm_recommender.api.runtime import source_digest;print(source_digest())'])
                require(actual==expected);env['worker_source_sha256']=actual
            with stage('synthetic_tests_in_frozen_image'):
                args=container_args(saved['worker_name']+'-synthetic',image)
                command(args+['--rm',image,'-m','pytest','-q','-p','no:cacheprovider','tests/integration/test_final_evaluation.py','tests/integration/test_quality_folds.py'],log=private/'synthetic-private.log',timeout=600)
            awake.check();require('AC Power' in command(['pmset','-g','batt']))
            require(shutil.disk_usage(art).free>=20*2**30)
            checkout_commit(ROOT,commit)
            with stage('reserve_real_test_once'):
                reserve(art/'.final-test-once.json',dict(run_id=rid,protocol='final-hp-v1',selected_method='H',window=WINDOW,limits=dict(memory_bytes=5*2**30,cpus=2,threads=2,max_seconds=5400),**env))
            config=dict(dataset_dir='/dataset',source_sha256=selection['source_sha256'],dataset_manifest_sha256=selection['dataset_manifest_sha256'])
            write_json(private/'config.json',config)
            args=container_args(saved['worker_name'],image)
            cid=command(args+['-d','--mount','type=bind,src='+str(ctx['dataset'])+',dst=/dataset,readonly',
                '--mount','type=bind,src='+str(private)+',dst=/output',image,'-m','hm_recommender.quality.final_worker','supervise','/output'])
            require(inspect(cid)['image_id']==image)
            deadline=time.monotonic()+60
            while not (private/'ready.json').exists():
                require(time.monotonic()<deadline and inspect(cid)['running']);time.sleep(.5)
            started=time.monotonic();samples.append(capture(cid,'final','before_seed',started,services=('final',)))
            with stage('evaluate_H_and_P_once'):
                r['test_started']=True;write_json(private/'go.json',{'run_id':rid})
                deadline=time.monotonic()+5460
                while not (private/'complete.json').exists():
                    awake.check();require(time.monotonic()<deadline and inspect(cid)['running'])
                    require(shutil.disk_usage(art).free>=5*2**30)
                    time.sleep(5);samples.append(capture(cid,'final','before_seed',started,services=('final',)))
                samples.append(capture(cid,'final','before_seed',started,services=('final',)))
            result=json.loads((private/'complete.json').read_text())
            for k in ('exit_code','duration_seconds','clock_discrepancy_seconds','error_type'):r[k]=result[k]
            r['resources_ok']=resources_ok(samples)
            if (private/'evaluation-result.json').exists():r['evaluation']=json.loads((private/'evaluation-result.json').read_text())
            require(r['exit_code']==0 and r['error_type'] is None)
        except BaseException as error:
            r['error_type']=r['error_type'] or type(error).__name__
            (private/'failure-private.log').write_text(traceback.format_exc())
        finally:
            try:
                with stage('recover_auxiliaries'):
                    r['transitions']=recover(ctx,saved);r['auxiliaries_restored']=True
                env['state_after_sha256']=state_hash(v)
                r['source_preserved']=env['state_after_sha256']==env['state_before_sha256']!='0'*64
            except BaseException as error:
                r['error_type']=r['error_type'] or type(error).__name__
                (private/'recovery-private.log').write_text(traceback.format_exc())
                print('Recuperación pendiente; no repetir test. Conservar estado para --restore-only.',flush=True)
        report=dict(schema_version=1,kind='final-handoff',run_id=rid,machine_id='mac24',context=os.environ['DOCKER_CONTEXT'],source_commit=commit,source_clean=True,status='failed',started_at=started_at,finished_at=now(),warnings=[],steps=[])
        if r['error_type'] is None:
            r['status']=report['status']='passed'
            try:validate(report,r,env,samples)
            except Exception as error:r['status']=report['status']='failed';r['error_type']=type(error).__name__
        folder=art/'handoff'/rid;folder.mkdir(parents=True)
        report.update(result=r,steps=[dict(name='final',log='job.log',status=r['status'],exit_code=0 if r['status']=='passed' else 1),dict(name='resources',log='resources.log',status='passed' if samples else 'failed',exit_code=0 if samples else 1)])
        write_json(folder/'result.json',r);write_json(folder/'environment.json',env);write_json(folder/'resources.log',samples)
        (folder/'commit.txt').write_text(commit+'\n');(folder/'job.log').write_text('final T8-C\nstatus: '+r['status']+'\n')
        archive=package_report(folder,report);read_report(archive)
        print('Reporte cerrado: '+str(archive),flush=True)
        command([sys.executable,'scripts/github-handoff.py','upload',str(archive)],log=private/'upload-private.log',timeout=300)
        print('Estado: '+r['status']+'; revisión principal pendiente. No repetir test.',flush=True)
        return 0 if r['status']=='passed' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True)
    modes=p.add_mutually_exclusive_group();modes.add_argument('--preflight-only',action='store_true');modes.add_argument('--restore-only',action='store_true')
    a=p.parse_args();os.environ.update(SSL_CERT_FILE='/etc/ssl/cert.pem',DOCKER_CONTEXT='colima-hm-project');os.umask(0o077)
    if a.restore_only:return restore_only(a.expected_commit)
    if a.preflight_only:
        preflight(a.expected_commit);print('Preflight OK; no se detuvo ningún servicio ni se reservó test.',flush=True);return 0
    return run(a.expected_commit)

if __name__=='__main__':
    try:sys.exit(main())
    except BaseException as e:
        if isinstance(e,SystemExit):raise
        print('DETENIDO: '+type(e).__name__+'. Conservar logs/reserva; no repetir test.',file=sys.stderr);sys.exit(1)
