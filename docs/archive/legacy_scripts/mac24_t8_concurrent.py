#!/usr/bin/env python3
"""One T8-B build in existing scheduler with exact auxiliary recovery."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import resource
from pathlib import Path
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime,timezone
from bootstrap import read_configuration
from clock_check import checkout_commit,runner_digest
from clock_guard import AwakeGuard
from handoff import write_json,now,package_report,read_report
from mac24_terminal import command,service,same_service,RELEASE
from mac24_t7 import check_airflow
from project import compose
from pipeline_check import request_json
from benchmark import phase,http_request
from benchmark_resources import sample,accepted
from concurrency_handoff import LIMITS,overlap,validate,require
from concurrency_transition import targets,inspect,verify,stop,restore

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.runtime/t8-concurrency-transition'
OLD='5e0ba69395ee17158d4b823d63776dadc072a5559204b3101df00b348765d442'
NEW='b1f69269be9960d1df0f2693e0f13b53ce0cbaabcd98ea1f6ad68703f5a3a7b3'


@contextmanager
def stage(name):
    print('T8-B preflight: '+name,flush=True)
    try:yield
    except Exception as error:
        print('FALLO: '+name+' ('+type(error).__name__+')',flush=True)
        raise
    print('OK: '+name,flush=True)


def state_hash(v):
    program="""import hashlib,json
from hm_recommender.database import connect
from psycopg import sql
with connect() as c:
 c.execute('SET TRANSACTION READ ONLY')
 state=dict(pointer=c.execute('SELECT active_id,previous_id FROM serving_pointer').fetchall(),streams=c.execute('SELECT stream_id,release_id,data_as_of,revision,processed_events,received_before FROM event_streams ORDER BY stream_id').fetchall(),targets=c.execute('SELECT stream_id,release_id,data_as_of,receipt_cursor,enabled FROM pipeline_targets ORDER BY stream_id').fetchall())
 tables=('app_metadata','schema_migrations','serving_releases','serving_pointer','event_streams','stream_catalog','stream_daily_seed','interaction_events','operational_daily','operational_profiles','pipeline_targets')
 value=dict(state=state,counts={t:c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in tables})
 print(hashlib.sha256(json.dumps(value,default=str,sort_keys=True).encode()).hexdigest())
"""
    return compose(ROOT,v,'exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',program,capture=True,timeout=300).strip()


def load_context(commit):
    with stage('checkout'):checkout_commit(ROOT,commit)
    v=read_configuration(ROOT/'.env');art=Path(v['ARTIFACTS_DIR'])
    if not art.is_absolute():art=ROOT/art
    baseline=json.loads((ROOT/'evidence/benchmark-review-mac24.json').read_text())
    capacity=json.loads((ROOT/'evidence/capacity-review-mac24.json').read_text())
    t7=json.loads((ROOT/'evidence/portability-review-mac24.json').read_text())
    for audit in (baseline,capacity,t7):
        p=art/'handoff'/(audit['run_id']+'.zip')
        with stage('archive_'+audit['run_id'].split('-')[0]):
            require(hashlib.sha256(p.read_bytes()).hexdigest()==audit['archive']['sha256'])
            require(read_report(p)['status']=='passed')
    with stage('auxiliary_targets'):aux=targets(capacity,t7)
    with stage('restored_configuration'):
        restored=read_configuration(ROOT/'.runtime/T7 restored installation airflow followup/.env')
        require(hashlib.sha256(restored['COMPOSE_PROJECT_NAME'].encode()).hexdigest()==aux[0]['project_sha256'])
    return v,art,baseline,capacity,aux


def restored_ready():
    values=read_configuration(ROOT/'.runtime/T7 restored installation airflow followup/.env')
    ready=request_json(int(values['API_PORT']),values['HM_APP_PASSWORD'],'/health/ready')
    require(ready['release_id']==RELEASE)


def recovery(commit):
    v,art,_,_,aux=load_context(commit)
    with (art/'.heavy-launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        saved=json.loads((STATE/'state.json').read_text())
        require(saved['aux']==aux)
        if saved['worker_started']:
            result=Path(saved['worker_output']).with_suffix('.exit.json')
            require(result.is_file()) # A lost SSH connection must not restore over a live build.
            require(type(json.loads(result.read_text())['exit_code']) is int)
        recovered=restore(aux);restored_ready();write_json(STATE/'recovery.json',recovered)
    print('Auxiliares recuperados; no repetir carga. Conservar evidencia para revisión.',flush=True)


def preflight(commit):
    print('T8-B: verificar antecedentes, imágenes y ausencia de trabajos',flush=True)
    v,art,baseline,capacity,aux=load_context(commit)
    with stage('no_prior_attempt'):
        require(not STATE.exists() and not list((art/'handoff').glob('concurrency-mac24-*')))
    with stage('hardware'):
        require(command(['uname','-m'])=='arm64' and command(['sysctl','-n','hw.memsize'])==str(24*2**30))
    with stage('power'):require('AC Power' in command(['pmset','-g','batt']))
    rows_expected=capacity['result']['containers']
    with stage('running_inventory'):
        require(set(command(['docker','ps','--no-trunc','--format','{{.ID}}']).splitlines())=={c['container_id'] for c in rows_expected})
    for row in rows_expected:
        with stage('container_'+row['scope']+'_'+row['service']):
            current=inspect(row['container_id']);verify(row,current)
            print('Salud: '+current['health'],flush=True)
            require(current['running'] and current['health'] in ('healthy','none'))
    engine=json.loads(command(['docker','info','--format','{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}']))
    with stage('engine_capacity'):
        require(engine['cpus']==4 and 13*2**30<=engine['memory']<=14*2**30)
        require(sum(c['memory_bytes'] for c in rows_expected if c not in aux)+2**30<=engine['memory'])
    print('T8-B: comprobar Airflow pausado y sin ejecuciones',flush=True)
    with stage('airflow_paused'):
        check_airflow(ROOT,v)
    idle="from hm_recommender.database import connect\nwith connect('airflow') as c:print(c.execute(\"SELECT count(*) FROM dag_run WHERE state IN ('running','queued')\").fetchone()[0])"
    with stage('airflow_idle'):
        require(compose(ROOT,v,'exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',idle,capture=True,timeout=30).strip()=='0')
    print('T8-B: comprobar fuentes, H activo y cohorte privada',flush=True)
    code="from hm_recommender.api.runtime import source_digest;print(source_digest())"
    with stage('scheduler_code'):
        require(compose(ROOT,v,'exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',code,capture=True,timeout=30).strip()==OLD)
    port=int(v['API_PORT']);http=lambda path:request_json(port,v['HM_APP_PASSWORD'],path)
    with stage('api_code'):
        require(http('/health/live')['source_tree_sha256']==NEW)
    with stage('active_release'):
        ready=http('/health/ready');require(ready['release_id']==RELEASE)
    cases_file=art/'benchmark-private'/baseline['run_id']/'cases.json'
    with stage('private_cases_checksum'):
        require(hashlib.sha256(cases_file.read_bytes()).hexdigest()==baseline['environment']['cases_sha256'])
    with stage('private_cases_shape'):
        cases=json.loads(cases_file.read_text())['cases'];require(len(cases)==96)
    logs=ROOT/'.runtime'/('t8-concurrency-preflight-'+uuid.uuid4().hex);logs.mkdir(parents=True)
    print('T8-B: suite host; logs privados: '+str(logs),flush=True)
    with stage('host_tests'):
        command([sys.executable,'-m','unittest','discover','-s','tests/host','-q'],log=logs/'host-tests.log',timeout=180)
    with stage('source_services'):
        before={s:service(v,s) for s in (*LIMITS,'airflow-api','airflow-dag-processor')}
    return dict(v=v,art=art,baseline=baseline,capacity=capacity,aux=aux,before=before,cases=cases,port=port,http=http,ready=ready,rows_expected=rows_expected)


def run(commit):
    generator_cpu_start=time.process_time()
    ctx=preflight(commit)
    v=ctx['v'];art=ctx['art'];baseline=ctx['baseline'];capacity=ctx['capacity'];aux=ctx['aux']
    before=ctx['before'];cases=ctx['cases'];port=ctx['port'];http=ctx['http'];ready=ctx['ready'];rows_expected=ctx['rows_expected']
    rid='concurrency-mac24-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    private=art/'concurrency-private'/rid;folder=art/'handoff'/rid
    STATE.mkdir(parents=True);private.mkdir(parents=True);folder.mkdir()
    worker_output=private/'worker.json'
    saved=dict(source_commit=commit,aux=aux,worker_started=False,worker_output=str(worker_output),run_id=rid)
    write_json(STATE/'state.json',saved)
    r=dict(protocol='scheduler-concurrency-v1',status='failed',warmup=None,measurement=None,overlap=None,worker_seconds=0.,build_seconds=0.,build_release_id=None,build_exit_code=None,resources_ok=False,source_preserved=False,auxiliaries_restored=False,auxiliary_transitions=[],generator_cpu_seconds=0.,generator_max_rss_bytes=0,error_type=None,training_performed=False,test_evaluated=False,t8_closed=False)
    env=dict(source_commit=commit,runner_tree_sha256=runner_digest(ROOT),baseline_archive_sha256=baseline['archive']['sha256'],capacity_archive_sha256=capacity['archive']['sha256'],cases_sha256=baseline['environment']['cases_sha256'],api_code_sha256=NEW,scheduler_code_sha256=OLD,state_before_sha256='0'*64,state_after_sha256='0'*64)
    rows=[];done=threading.Event();observer=None;worker=None;errors=[];window={};started=now();t0=time.monotonic()
    report=dict(schema_version=1,kind='concurrency-handoff',run_id=rid,machine_id='mac24',context=os.environ['DOCKER_CONTEXT'],source_commit=commit,source_clean=True,started_at=started,status='failed',warnings=[],steps=[])
    with AwakeGuard(),(art/'.heavy-launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            initial_aux={c['container_id']:inspect(c['container_id']) for c in aux}
            write_json(STATE/'before.json',{c['container_id']:inspect(c['container_id']) for c in rows_expected})
            env['state_before_sha256']=state_hash(v)
            print('T8-B: detener únicamente restauración T7 y MLflow',flush=True)
            stop(aux)
            expected={c['container_id'] for c in rows_expected if c not in aux}
            require(set(command(['docker','ps','--no-trunc','--format','{{.ID}}']).splitlines())==expected)
            check_airflow(ROOT,v)
            # New output/config only. No edits to historical T6 config or active registry.
            remote='/artifacts/concurrency-private/'+rid
            config=dict(dataset_dir='/data/processed/hm-v1',output_dir=remote+'/release',data_as_of='2020-09-09',synthetic=False,evidence_archive='/artifacts/handoff/'+json.loads((ROOT/'configs/releases/history-v1.json').read_text())['selection']['run_id']+'.zip',activate=False)
            write_json(private/'build.json',config)
            containers={s:before[s]['container_id'] for s in LIMITS}
            rows.extend(sample(containers,t0))
            def observe():
                due=time.monotonic()+10
                while not done.wait(max(0,due-time.monotonic())):
                    try:rows.extend(sample(containers,t0))
                    except Exception as error:errors.append(type(error).__name__);return
                    due+=10
            observer=threading.Thread(target=observe);observer.start()
            request=lambda case:http_request(port,case,2.)
            require(all(request(c) for c in cases))
            print('T8-B: calentamiento 120 s; auxiliares detenidos',flush=True)
            r['warmup']=phase(cases,rate=20,seconds=120,workers=64,request=request,max_lag=.05)
            def build():
                window['start']=time.monotonic()
                try:
                    program=(ROOT/'scripts/concurrency_worker.py').read_text()
                    compose(ROOT,v,'exec','-T','-e','HMREC_SOURCE_COMMIT=13af71c012cc8f0142e2b6eba111357160fb6f69','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',program,remote+'/build.json',remote+'/worker.json',capture=True,timeout=580)
                except Exception as error:window['error']=type(error).__name__
                finally:window['end']=time.monotonic()
            def launch():
                nonlocal worker
                saved['worker_started']=True;write_json(STATE/'state.json',saved)
                worker=threading.Thread(target=build);worker.start()
            trace={}
            print('T8-B: medición 600 s y una construcción H sin activación',flush=True)
            r['measurement']=phase(cases,rate=20,seconds=600,workers=64,request=request,max_lag=.05,trace=trace,on_start=launch)
            worker.join()
            r['worker_seconds']=window['end']-window['start']
            require(window['end']<=trace['started']+600)
            r['overlap']=overlap(trace,cases,window['start'],window['end'])
            exit_record=json.loads(worker_output.with_suffix('.exit.json').read_text());r['build_exit_code']=exit_record['exit_code']
            built=json.loads(worker_output.read_text());r['build_seconds']=built['duration_seconds'];r['build_release_id']=built.get('release_id')
            require(built['status']=='passed' and built['published'] is False)
            require('error' not in window)
        except BaseException as error:
            r['error_type']=type(error).__name__; (private/'failure.log').write_text(traceback.format_exc())
        finally:
            done.set()
            if observer:observer.join()
            if worker:worker.join(timeout=600)
            try:
                rows.extend(sample({s:before[s]['container_id'] for s in LIMITS},t0))
                r['resources_ok']=not errors and accepted(rows,LIMITS)
                for s,identity in before.items():same_service(identity,service(v,s))
                env['state_after_sha256']=state_hash(v)
                require(http('/health/ready')==ready and http('/health/live')['source_tree_sha256']==NEW)
                check_airflow(ROOT,v)
                r['source_preserved']=env['state_after_sha256']==env['state_before_sha256']
            except Exception as error:r['error_type']=r['error_type'] or type(error).__name__
            try:
                require(not saved['worker_started'] or worker_output.with_suffix('.exit.json').is_file())
                recovered=restore(aux);restored_ready();write_json(STATE/'after.json',recovered);r['auxiliaries_restored']=True
                r['auxiliary_transitions']=[dict(service=next(c['service'] for c in aux if c['container_id']==item['container_id']),before=initial_aux[item['container_id']],after=item) for item in recovered]
            except Exception as error:
                r['error_type']=r['error_type'] or type(error).__name__
                print('Recuperación pendiente: conservar estado y usar --restore-only; no repetir carga.',flush=True)
    r['generator_cpu_seconds']=time.process_time()-generator_cpu_start
    r['generator_max_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if r['error_type'] is None:
        r['status']=report['status']='passed'
        try:validate(report,r,env,rows)
        except Exception as error:r['status']=report['status']='failed';r['error_type']=type(error).__name__
    report.update(result=r,finished_at=now(),steps=[dict(name='concurrency',log='job.log',status=r['status'],exit_code=0 if r['status']=='passed' else 1),dict(name='resources',log='resources.log',status='passed' if rows else 'failed',exit_code=0 if rows else 1)])
    write_json(folder/'result.json',r);write_json(folder/'environment.json',env);write_json(folder/'resources.log',rows)
    (folder/'commit.txt').write_text(commit+'\n');(folder/'job.log').write_text('concurrency T8-B\nstatus: '+r['status']+'\n')
    archive=package_report(folder,report);read_report(archive)
    print('Reporte cerrado: '+str(archive),flush=True)
    command([sys.executable,'scripts/github-handoff.py','upload',str(archive)],log=private/'upload.log',timeout=300)
    print('Estado: '+r['status']+'; auxiliares recuperados: '+str(r['auxiliaries_restored'])+'; revisión principal pendiente.',flush=True)
    return 0 if r['status']=='passed' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True)
    mode=p.add_mutually_exclusive_group();mode.add_argument('--restore-only',action='store_true');mode.add_argument('--preflight-only',action='store_true')
    os.environ.update(SSL_CERT_FILE='/etc/ssl/cert.pem',DOCKER_CONTEXT='colima-hm-project');os.umask(0o077)
    a=p.parse_args()
    if a.preflight_only:
        preflight(a.expected_commit)
        print('Preflight OK; no se reservó, detuvo ni inició ningún servicio/carga.',flush=True)
        return 0
    return recovery(a.expected_commit) if a.restore_only else run(a.expected_commit)


if __name__=='__main__':
    try:sys.exit(main())
    except BaseException as error:
        if isinstance(error,SystemExit):raise
        print('DETENIDO: '+type(error).__name__+'. Conservar logs/reserva; no repetir carga.',file=sys.stderr);sys.exit(1)
