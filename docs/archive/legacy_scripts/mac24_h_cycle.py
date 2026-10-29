#!/usr/bin/env python3
"""One separately reserved H-cycle observation, with no model/test changes."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile

from bootstrap import read_configuration
from clock_check import checkout_commit,runner_digest
from clock_guard import AwakeGuard
from handoff import now,write_json,read_report
from benchmark import phase,http_request
from benchmark_resources import sample,accepted
from benchmark_handoff import phase as validate_phase
from concurrency_handoff import LIMITS
from h_cycle import clock_bounds,cycle_metrics,decision,public_trace,collect_periodically
from mac24_t8_concurrent import OLD,NEW,restored_ready
from mac24_t7 import check_airflow
from mac24_ui_deploy import signature,recovery_signals
from pipeline_check import request_json
from ui_recovery_control import require,identity,select_targets,admit,restored_services as restore_services,G
from ui_deploy_control import verify_inventory
import mac24_ui_recovery as r

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.runtime/h-cycle-v1'
UI_SOURCE=ROOT/'.runtime/ui-deploy-v1'
UI_REPORT_SHA='f27d1a084c6310b27260f4a78b2cff8625b0ee169ccf9aafa6d9fbea832f0b3c'
REPO='emilianodesu/clothing-recommendation'
FILES={'report.json','resources.json','trace.json','worker.json'}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(state):write_json(STATE/'state.json',state)


def reserve(state):
    STATE.mkdir(mode=0o700)  # Never remove/reuse this directory on a later run.
    save(state)


def verify_after(before,after,aux):
    require(set(before)==set(after),'Container inventory changed')
    ids={row['container_id'] for row in aux}
    for cid,row in before.items():
        current=after[cid]
        if cid in ids:
            identity(row,current)
            require({k:v for k,v in row.items() if k!='started_at'}==
                    {k:v for k,v in current.items() if k!='started_at'},'Auxiliary final state changed')
        else:require(row==current,'Preserved container changed')


def recover(state,values):
    if state['worker_started']:
        exit_path=Path(state['worker_output']).with_suffix('.exit.json')
        require(exit_path.is_file(),'Worker termination unconfirmed; keep reservation for diagnosis')
        require(type(json.loads(exit_path.read_text())['exit_code']) is int,'Invalid worker exit marker')
    state['auxiliaries_restored']=False
    state['restoration']=restore_services(state['aux'],r.inspect,
        lambda cid:r.docker('start',cid,timeout=60),r.healthy)
    save(state)
    require(all(row['restored'] for row in state['restoration']),'One or more auxiliaries not recovered')
    verify_after(state['before'],r.snapshot(),state['aux'])
    restored_ready();r.airflow(values)
    state['auxiliaries_restored']=True;save(state)


def current_inventory(rows):
    path=ROOT/'evidence/ui-deployment-mac24.json'
    require(sha(path)==UI_REPORT_SHA,'UI deployment evidence changed')
    published=json.loads(path.read_text());prior=json.loads((UI_SOURCE/'state.json').read_text())
    require(all(prior.get(k)==v for k,v in published.items()),'UI private state differs')
    require(published['status']=='passed' and published['recovery_complete'],'UI deployment not complete')
    temporaries=[cid for name in ('builder_container','probe_name','regression_name') if (cid:=r.named(prior[name]))]
    verify_inventory(prior['before'],rows,prior['aux'],prior['original_ui'],prior['current_ui'],temporaries)
    require(rows[prior['current_ui']]==prior['verified_ui'],'Current UI generation differs')


def preflight(commit,values,art):
    def stage(name):r.emit('H-cycle preflight: '+name)
    stage('checkout_and_prior_evidence');checkout_commit(ROOT,commit)
    require(r.run_command(['git','branch','--show-current'])=='main','Expected main')
    require(not STATE.exists(),'H-cycle already reserved; do not repeat load')
    baseline=json.loads((ROOT/'evidence/benchmark-review-mac24.json').read_text())
    historical=json.loads((ROOT/'evidence/concurrency-review-mac24.json').read_text())
    for audit in (baseline,historical):
        path=art/'handoff'/(audit['run_id']+'.zip')
        require(sha(path)==audit['archive']['sha256'],'Historical archive changed')
        read_report(path)
    stage('hardware_power')
    require(r.run_command(['uname','-m'])=='arm64' and r.run_command(['sysctl','-n','hw.memsize'])==str(24*G),'Expected mac24')
    require('AC Power' in r.run_command(['pmset','-g','batt']),'Connect AC')
    require(re.search(r'"AppleClamshellState"\s*=\s*No\b',r.run_command(['ioreg','-r','-k','AppleClamshellState','-d','4'])),'Open lid')
    stage('current_inventory_and_capacity');before=r.snapshot();current_inventory(before)
    t7=json.loads((ROOT/'evidence/portability-review-mac24.json').read_text())
    project=values['COMPOSE_PROJECT_NAME']
    all_aux=select_targets(before,project,t7['result']['destination_postgres']['container_id'])
    aux=[row for row in all_aux if row['project']!=project or row['service']=='mlflow']
    require(len(aux)==3 and {row['service'] for row in aux}=={'postgres','api','mlflow'},'Wrong pause targets')
    kept={cid:row for cid,row in before.items() if row['running'] and cid not in {a['container_id'] for a in aux}}
    engine=json.loads(r.query('info','--format','{"memory":{{.MemTotal}},"cpus":{{.NCPU}},"architecture":{{json .Architecture}}}'))
    require(engine['cpus']==4 and 13*G<=engine['memory']<=14*G and engine['architecture']=='aarch64','Unexpected engine')
    admitted=admit(kept,engine['memory'],0)
    services={row['service']:cid for cid,row in kept.items()}
    for name,(memory,cpus) in LIMITS.items():
        row=before[services[name]]
        require(row['memory_bytes']==memory and row['nano_cpus']==cpus,'Service resource limits changed')
    stage('airflow_and_sources');r.airflow(values)
    code='from hm_recommender.api.runtime import source_digest;print(source_digest())'
    require(r.docker('exec',services['airflow-scheduler'],'/opt/hmrec/.venv/bin/python','-c',code)==OLD,'Scheduler source changed')
    live=request_json(int(values['API_PORT']),values['HM_APP_PASSWORD'],'/health/live')
    require(live['source_tree_sha256']==NEW,'API source changed')
    raw=json.loads(r.query('inspect',services['api']))[0]
    require(raw['HostConfig']['PortBindings']=={'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':str(values['API_PORT'])}]},'API binding changed')
    case_path=art/'benchmark-private'/baseline['run_id']/'cases.json'
    require(sha(case_path)==baseline['environment']['cases_sha256'],'Private cohort changed')
    cases=json.loads(case_path.read_text())['cases']
    require(len(cases)==96 and all(sum(c['group']==g for c in cases)==32 for g in ('history','known_fallback','unknown')),'Cohort shape changed')
    state=dict(protocol='h-cycle-v1',source_commit=commit,runner_tree_sha256=runner_digest(ROOT),
        before=before,kept=kept,aux=aux,services=services,engine=engine,admitted_bytes=admitted,
        ready=r.ready(values),worker_started=False,auxiliaries_restored=False,
        baseline_sha256=baseline['archive']['sha256'],historical_t8b_sha256=historical['archive']['sha256'],
        ui_report_sha256=UI_REPORT_SHA,cases_sha256=sha(case_path),
        protected={str(art/'.final-test-once.json'):sha(art/'.final-test-once.json'),
                   str(UI_SOURCE/'active-override.json'):sha(UI_SOURCE/'active-override.json'),
                   str(ROOT/'.env'):sha(ROOT/'.env'),str(ROOT/'compose.yaml'):sha(ROOT/'compose.yaml')})
    state['state_before_sha256']=signature(state)
    r.verify_stable(before,r.snapshot());stage('ready')
    return state,cases


def calibrate(cid):
    rows=[]
    for _ in range(3):
        before=time.monotonic()
        vm=float(r.docker('exec',cid,'/opt/hmrec/.venv/bin/python','-c','import time;print(time.monotonic())',timeout=10))
        rows.append(dict(before=before,vm=vm,after=time.monotonic()))
    return rows


def worker_resources_ok(worker):
    before,after=worker['before'],worker['after']
    def events(row):return {k:int(v) for k,v in (line.split() for line in row['memory.events'].splitlines())}
    a,b=events(before),events(after)
    return (all(int(row['memory.max'])==5*G and int(row['memory.swap.current'])==0 for row in (before,after))
        and int(before['memory.swap.peak'])==int(after['memory.swap.peak'])
        and all(b[k]>=v for k,v in a.items())
        and all(b[k]==a[k] for k in ('oom','oom_kill','oom_group_kill')))


def measure(state,values,cases,art,report,rows,trace,worker_public,guard):
    private=Path(state['worker_output']).parent;remote='/artifacts/h-cycle-private/'+state['run_id']
    selection=json.loads((ROOT/'configs/releases/history-v1.json').read_text())['selection']['run_id']
    config=dict(dataset_dir='/data/processed/hm-v1',output_dir=remote+'/release',data_as_of='2020-09-09',
        synthetic=False,evidence_archive='/artifacts/handoff/'+selection+'.zip',activate=False)
    write_json(private/'build.json',config);report['build_config_sha256']=sha(private/'build.json')
    containers={s:state['services'][s] for s in LIMITS};t0=time.monotonic();done=threading.Event();errors=[]
    rows.extend(sample(containers,t0))
    def observe():
        def collect():rows.extend(sample(containers,t0));guard.check()
        try:collect_periodically(done,collect,time.monotonic)
        except Exception as error:errors.append(type(error).__name__)
    observer=threading.Thread(target=observe);observer.start();worker=None;window={}
    try:
        request=lambda case:http_request(int(values['API_PORT']),case,2.)
        require(all(request(c) for c in cases),'First-contact prediction mismatch')
        r.emit('H-cycle: calentamiento 120 s a 20 solicitudes/s')
        report['warmup']=phase(cases,rate=20,seconds=120,workers=64,request=request,max_lag=.05)
        def build():
            window['start']=time.monotonic()
            try:
                report['calibration_before']=calibrate(containers['airflow-scheduler'])
                # Durable intent before Docker exec. A missing exit marker requires diagnosis.
                state['worker_started']=True;save(state)
                r.docker('exec','-e','HMREC_SOURCE_COMMIT=13af71c012cc8f0142e2b6eba111357160fb6f69',
                    containers['airflow-scheduler'],'/opt/hmrec/.venv/bin/python','-c',
                    (ROOT/'scripts/h_cycle_worker.py').read_text(),remote+'/build.json',remote+'/worker.json',
                    log=private/'exec.log',timeout=580)
                report['calibration_after']=calibrate(containers['airflow-scheduler'])
            except BaseException as error:
                window['error']=type(error).__name__; (private/'worker-failure.log').write_text(traceback.format_exc())
            finally:window['end']=time.monotonic()
        def launch():
            nonlocal worker
            worker=threading.Thread(target=build);worker.start()
        r.emit('H-cycle: medición 600 s y una construcción H sin activación')
        report['measurement']=phase(cases,rate=20,seconds=600,workers=64,request=request,max_lag=.05,trace=trace,on_start=launch)
        worker.join(timeout=600);require(not worker.is_alive(),'Worker still active')
        require('error' not in window,'Worker/calibration failed; inspect private log')
        output=Path(state['worker_output']);exit_record=json.loads(output.with_suffix('.exit.json').read_text())
        built=json.loads(output.read_text())
        require(exit_record['exit_code']==0 and not exit_record['timed_out'],'Build did not finish normally')
        result=built['result'];require(result['status']=='passed' and result['published'] is False,'Unexpected build result')
        worker_public.update({k:built[k] for k in ('start','end','wall_start','wall_end','before','after')})
        worker_public['result']={k:result[k] for k in ('status','published','release_id','duration_seconds')}
        worker_public['exit']=exit_record
        bounds=clock_bounds(report['calibration_before'],report['calibration_after'])
        report['cycle']=cycle_metrics(trace,cases,built,bounds)
        report['clock_bounds']=bounds;report['worker_seconds']=window['end']-window['start']
        report['worker_ok']=worker_resources_ok(built)
        for name in ('warmup','measurement'):
            validate_phase(report[name]);require(report[name]['clock_discrepancy_seconds']<=.1,'Host clock discontinuity')
    finally:
        # Defers further SSH signals while waiting for the known bounded child.
        with recovery_signals(state):
            if worker:worker.join(timeout=600)
            done.set();observer.join(timeout=180)
            require(not observer.is_alive(),'Resource observer still active')
            rows.extend(sample(containers,t0))
            report['resources_ok']=not errors and accepted(rows,LIMITS)


def preserve(state,values):
    for path,digest in state['protected'].items():require(sha(path)==digest,'Protected configuration/reservation changed')
    state['state_after_sha256']=signature(state)
    require(state['state_after_sha256']==state['state_before_sha256'],'Application aggregate state changed')
    require(r.ready(values)==state['ready'],'Active H changed')
    check_airflow(ROOT,values)


def execute(state,values,cases,art,guard):
    report={k:state[k] for k in ('protocol','run_id','source_commit','runner_tree_sha256','engine','admitted_bytes',
        'baseline_sha256','historical_t8b_sha256','ui_report_sha256','cases_sha256','state_before_sha256')}
    report.update(created_at=now(),error_type=None,resources_ok=False,source_preserved=False,
        auxiliaries_restored=False,worker_ok=False,measurement=None,cycle=None,
        historical_t8b_status='failed',sustained_concurrency_proven=False,training_performed=False,test_evaluated=False)
    rows=[];trace={};worker_public={}
    try:
        r.verify_stable(state['before'],r.snapshot());preserve(state,values);r.airflow(values);guard.check()
        r.emit('H-cycle: pausar sólo MLflow y API/PostgreSQL restaurados T7')
        for row in state['aux']:
            identity(row,r.inspect(row['container_id']))
            r.docker('stop','--time','30',row['container_id'],timeout=60)
        live=r.snapshot()
        require({cid for cid,row in live.items() if row['running']}==set(state['kept']),'Unexpected active workload')
        admit(live,state['engine']['memory'],0)
        measure(state,values,cases,art,report,rows,trace,worker_public,guard)
    except BaseException as error:
        report['error_type']=type(error).__name__; (STATE/'failure.log').write_text(traceback.format_exc())
    finally:
        with recovery_signals(state):
            try:preserve(state,values);report['source_preserved']=True
            except BaseException as error:
                report['error_type']=report['error_type'] or type(error).__name__
                (STATE/'preservation-failure.log').write_text(traceback.format_exc())
            try:
                recover(state,values);report['auxiliaries_restored']=True
                report['source_preserved']=False
                preserve(state,values);report['source_preserved']=True
            except BaseException as error:
                report['error_type']=report['error_type'] or type(error).__name__
                (STATE/'recovery-failure.log').write_text(traceback.format_exc())
                r.emit('Recuperación pendiente; conservar reserva y revisar logs. No repetir carga.')
    report.update(finished_at=now(),status=decision(report),restoration=state.get('restoration',[]),
                  state_after_sha256=state.get('state_after_sha256'))
    # Export only timing/segment outcomes, never recommendation payloads.
    if trace:
        origin=trace['started']
        for name in ('calibration_before','calibration_after'):
            for row in report.get(name,[]):
                row['before']-=origin;row['after']-=origin
        if report.get('clock_bounds'):
            for key in ('low','high'):report['clock_bounds'][key]-=origin
        if report['cycle']:
            for key in ('definite_start','definite_end','possible_start','possible_end'):report['cycle'][key]-=origin
    folder=art/'handoff'/state['run_id'];folder.mkdir()
    write_json(folder/'report.json',report);write_json(folder/'resources.json',rows)
    write_json(folder/'trace.json',public_trace(trace) if trace else {})
    write_json(folder/'worker.json',worker_public)
    archive=close_archive(folder)
    state.update(archive=str(archive),archive_sha256=sha(archive),status=report['status']);save(state)
    r.emit('Reporte cerrado: '+str(archive));r.emit('Estado: '+report['status']+'; requiere revisión principal. T8-B histórico permanece failed.')
    return report


def close_archive(folder):
    require({p.name for p in folder.iterdir()}==FILES,'Unexpected public archive file')
    require(all(p.is_file() and not p.is_symlink() and p.stat().st_size<8*2**20 for p in folder.iterdir()),'Invalid archive member')
    archive=folder.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(FILES):z.write(folder/name,name)
    archive.with_suffix('.zip.sha256').write_text(sha(archive)+'  '+archive.name+'\n')
    return archive


def publish(state):
    archive=Path(state['archive']);require(sha(archive)==state['archive_sha256'],'Closed archive changed')
    sidecar=archive.with_suffix('.zip.sha256')
    require(sidecar.read_text()==state['archive_sha256']+'  '+archive.name+'\n','Archive checksum sidecar changed')
    expected={p.name:p for p in (archive,sidecar)}
    repo=json.loads(r.run_command(['gh','repo','view',REPO,'--json','nameWithOwner,isPrivate']))
    require(repo.get('nameWithOwner')==REPO,'Unexpected repository')
    tag='handoff-'+state['run_id']
    # Retrying publication must neither repeat work nor overwrite a remote asset.
    result=subprocess.run(['gh','api','repos/'+REPO+'/releases/tags/'+tag],
                          cwd=ROOT,text=True,capture_output=True,timeout=60)
    if result.returncode==0:
        existing=json.loads(result.stdout)
        assets={a['name']:a for a in existing['assets']}
        require(set(assets)<=set(expected),'Unexpected asset in existing release')
        for name,asset in assets.items():
            require(asset.get('digest')=='sha256:'+sha(expected[name]) and asset['size']==expected[name].stat().st_size,
                    'Remote asset digest/size differs or unavailable')
        missing=[str(path) for name,path in expected.items() if name not in assets]
        if missing:r.run_command(['gh','release','upload',tag,*missing,'--repo',REPO],log=STATE/'publish.log',timeout=180)
    else:
        require('(HTTP 404)' in result.stderr,'Cannot verify release absence; inspect GitHub authentication/network')
        r.run_command(['gh','release','create',tag,str(archive),str(sidecar),
            '--repo',REPO,'--target',state['source_commit'],'--title',state['run_id'],
            '--notes','h-cycle-v1: una construcción real. Revisión principal pendiente; T8-B histórico conserva failed.'],
            log=STATE/'publish.log',timeout=180)
    r.emit('Reporte publicado: https://github.com/'+REPO+'/releases/tag/'+tag)


def publish_saved(expected_commit):
    """Publish an immutable older run using a descendant publication fix."""
    state=json.loads((STATE/'state.json').read_text())
    source=state['source_commit']
    require(isinstance(source,str) and re.fullmatch('[0-9a-f]{40}',source),'Invalid run source commit')
    ancestry=subprocess.run(['git','merge-base','--is-ancestor',source,expected_commit],
                            cwd=ROOT,capture_output=True,timeout=30)
    require(ancestry.returncode==0,'Publication checkout must include the run source commit')
    archive=Path(state['archive'])
    require(sha(archive)==state['archive_sha256'],'Closed archive changed')
    with zipfile.ZipFile(archive) as bundle:
        members=bundle.infolist()
        require(len(members)==len(FILES) and {m.filename for m in members}==FILES,'Unexpected report inventory')
        require(all(0<=m.file_size<8*2**20 for m in members),'Oversized report member')
        report=json.loads(bundle.read('report.json'))
    require(report.get('protocol')=='h-cycle-v1','Unexpected report protocol')
    require(report.get('status') in ('passed','failed','inconclusive'),'Report not closed')
    require(all(report.get(k)==state.get(k) for k in ('source_commit','run_id','status')),
            'Closed report does not match saved execution')
    publish(state)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--expected-commit',required=True)
    modes=parser.add_mutually_exclusive_group()
    for mode in ('preflight-only','restore-only','publish-only'):modes.add_argument('--'+mode,action='store_true')
    args=parser.parse_args();os.umask(0o077)
    os.environ.update(SSL_CERT_FILE='/etc/ssl/cert.pem',DOCKER_CONTEXT='colima-hm-project',GIT_TERMINAL_PROMPT='0')
    checkout_commit(ROOT,args.expected_commit)
    if args.publish_only:
        publish_saved(args.expected_commit);return 0
    values=read_configuration(ROOT/'.env');art=Path(values['ARTIFACTS_DIR'])
    if not art.is_absolute():art=ROOT/art
    def interrupted(*_):raise KeyboardInterrupt('SSH signal')
    for sig in (signal.SIGHUP,signal.SIGTERM):signal.signal(sig,interrupted)
    with (art/'.heavy-launch.lock').open('a') as lock,AwakeGuard() as guard:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.restore_only:
            state=json.loads((STATE/'state.json').read_text())
            require(state['source_commit']==args.expected_commit and not state['auxiliaries_restored'],'Recovery not pending for this commit')
            with recovery_signals(state):recover(state,values);preserve(state,values)
            write_json(STATE/'recovery.json',dict(recovered_at=now(),restoration=state['restoration'],source_preserved=True))
            r.emit('Auxiliares recuperados; el reporte del ensayo conserva su estado anterior.');return 0
        state,cases=preflight(args.expected_commit,values,art)
        r.run_command([sys.executable,'-m','unittest','discover','-s','tests/host','-p','test_h_cycle*.py','-q'],timeout=120)
        if args.preflight_only:r.emit('Preflight OK; sin reserva, pausas ni carga.');return 0
        state['run_id']='h-cycle-mac24-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8]
        private=art/'h-cycle-private'/state['run_id'];private.mkdir(parents=True)
        state['worker_output']=str(private/'worker.json');reserve(state)
        report=execute(state,values,cases,art,guard)
    publish(state)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        r.emit('DETENIDO: '+type(error).__name__+': '+str(error)+'. Conservar logs/reserva; no repetir carga.')
        sys.exit(1)
