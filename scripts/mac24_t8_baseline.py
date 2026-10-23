#!/usr/bin/env python3
"""One T8-A baseline load run; no activation, training, test labels or service restart."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import threading
import time
import uuid
from benchmark import phase,http_request,summarize
from benchmark_resources import sample,accepted
from bootstrap import read_configuration
from clock_check import checkout_commit,runner_digest
from clock_guard import AwakeGuard
from handoff import write_json,now,package_report,read_report
from mac24_terminal import service,same_service,command,RELEASE
from mac24_t7 import check_airflow
from pipeline_check import request_json
from portability_check import state_hash
from project import compose
from restore import checksum_file

ROOT=Path(__file__).resolve().parents[1]


@contextmanager
def check_stage(name):
    print('T8-A preflight: '+name,flush=True)
    try:
        yield
    except Exception as error:
        print('FALLO: '+name+' ('+type(error).__name__+')',flush=True)
        raise
    print('OK: '+name,flush=True)


def preflight(commit):
    os.umask(0o077);os.environ['SSL_CERT_FILE']='/etc/ssl/cert.pem';os.environ['DOCKER_CONTEXT']='colima-hm-project'
    with check_stage('checkout'):
        checkout_commit(ROOT,commit)
    with check_stage('hardware'):
        if command(['uname','-m'])!='arm64' or command(['sysctl','-n','hw.memsize'])!=str(24*2**30):raise ValueError('Require mac24')
    with check_stage('power'):
        if 'AC Power' not in command(['pmset','-g','batt']):raise ValueError('Connect AC and keep lid open')
    with check_stage('t7_review_and_configuration'):
        review=json.loads((ROOT/'evidence/portability-review-mac24.json').read_text())
        if not review['t7_closed']:raise ValueError('T7 review required')
        v=read_configuration(ROOT/'.env');art=Path(v['ARTIFACTS_DIR']);art=art if art.is_absolute() else ROOT/art
    with check_stage('t7_archive'):
        prior=art/'handoff'/(review['run_id']+'.zip')
        if checksum_file(prior)!=review['archive']['sha256'] or read_report(prior)['status']!='passed':raise ValueError('T7 archive mismatch')
    marker=art/'handoff/.mac24-t8-baseline-once.json'
    with check_stage('no_prior_load_attempt'):
        if marker.exists() or list((art/'handoff').glob('benchmark-mac24-*')):raise ValueError('Existing load attempt; do not repeat')
    with check_stage('engine_capacity'):
        engine=json.loads(command(['docker','info','--format','{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}']))
        if engine['cpus']!=4 or not 13*2**30<=engine['memory']<=14*2**30:raise ValueError('Keep VM14GiB/4CPU')
    with check_stage('host_tests'):
        logs=ROOT/'.runtime'/('t8-preflight-'+uuid.uuid4().hex);logs.mkdir(parents=True)
        print('Logs privados: '+str(logs),flush=True)
        try:
            command([sys.executable,'-m','unittest','discover','-s','tests/host','-q'],log=logs/'host-tests.log',timeout=180)
        except Exception:
            import re
            for test,module in re.findall(r'^(?:FAIL|ERROR): (test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\)$',(logs/'host-tests.log').read_text(),re.M)[:10]:
                print('Prueba fallida: '+module+'.'+test,flush=True)
            raise
    with check_stage('running_workloads'):
        names=command(['docker','ps','--format','{{.Names}}']).splitlines()
        allowed=('-api-1','-ui-1','-postgres-1','-mlflow-1','-airflow-scheduler-1','-airflow-api-1','-airflow-dag-processor-1')
        if any(not n.endswith(allowed) for n in names):raise ValueError('Unexpected running workload; inspect first')
    with check_stage('airflow_paused'):
        check_airflow(ROOT,v)
    with check_stage('source_services'):
        identities={s:service(v,s) for s in ('api','postgres','ui')}
        same_service(review['result']['source_postgres'],identities['postgres'])
    sys.path.insert(0,str(ROOT/'src'))
    from hm_recommender.api.runtime import source_digest
    expected_code=source_digest(ROOT/'src/hm_recommender');port=int(v['API_PORT'])
    with check_stage('api_binding'):
        bindings=json.loads(command(['docker','inspect',identities['api']['container_id'],'--format','{{json .NetworkSettings.Ports}}']))
        if bindings.get('8000/tcp')!=[{'HostIp':'127.0.0.1','HostPort':str(port)}]:raise ValueError('Require loopback binding')
    with check_stage('api_code'):
        if request_json(port,v['HM_APP_PASSWORD'],'/health/live')['source_tree_sha256']!=expected_code:raise ValueError('API source differs')
    with check_stage('active_release'):
        if request_json(port,v['HM_APP_PASSWORD'],'/health/ready')['release_id']!=RELEASE:raise ValueError('Active release differs')
    with check_stage('awake_guard'):
        with AwakeGuard():pass
    with check_stage('configuration'):
        config=json.loads((ROOT/'configs/benchmark.yaml').read_text())
    return dict(commit=commit,v=v,art=art,marker=marker,identities=identities,expected_code=expected_code,port=port,config=config)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True)
    p.add_argument('--preflight-only',action='store_true',help='Diagnóstico previo: sin reserva, cohorte ni carga')
    a=p.parse_args();context=preflight(a.expected_commit)
    if a.preflight_only:
        print('Preflight OK; no se reservó ni inició la carga.',flush=True)
        return 0
    return execute_load(context)


def execute_load(context):
    commit=context['commit'];v=context['v'];art=context['art'];marker=context['marker']
    identities=context['identities'];expected_code=context['expected_code'];port=context['port'];config=context['config']
    rid='benchmark-mac24-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    private=art/'benchmark-private'/rid;private.mkdir(parents=True)
    folder=art/'handoff'/rid
    result=dict(protocol='load-baseline-v1',status='failed',warmup=None,measurement=None,first_contact=None,
        resources_ok=False,source_preserved=False,error_type=None,test_evaluated=False,training_performed=False,t8_closed=False,
        generator_cpu_seconds=0.,generator_max_rss_bytes=0,population={})
    env=dict(source_commit=commit,runner_tree_sha256=runner_digest(ROOT),config_sha256=checksum_file(ROOT/'configs/benchmark.yaml'),
        cases_sha256=None,release_id=RELEASE,source_state_sha256=None,source_tree_sha256=expected_code)
    rows=[];errors=[];stop=threading.Event();thread=None;start=time.monotonic();cpu=time.process_time();code=1
    report=dict(schema_version=1,kind='benchmark-handoff',run_id=rid,machine_id='mac24',context=os.environ['DOCKER_CONTEXT'],source_commit=commit,
        source_clean=True,started_at=now(),finished_at=None,status='failed',warnings=[],steps=[])
    with AwakeGuard(),(art/'.heavy-launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with marker.open('x') as f:json.dump({'commit':commit,'run_id':rid},f)
        folder.mkdir()
        try:
            env['source_state_sha256']=state_hash(ROOT,v)
            program=(ROOT/'scripts/benchmark_cases.py').read_text()
            target='/artifacts/benchmark-private/'+rid+'/cases.json'
            compose(ROOT,v,'run','--rm','--no-deps','-T','jobs','python','-c',program,'/artifacts/serving/'+RELEASE,target,expected_code,capture=True,timeout=300)
            env['cases_sha256']=checksum_file(private/'cases.json')
            payload=json.loads((private/'cases.json').read_text());cases=payload['cases'];result['population']=payload['population']
            if payload['release_id']!=RELEASE or len(cases)!=96:raise ValueError('Private cohort mismatch')
            containers={s:d['container_id'] for s,d in identities.items()}
            rows.extend(sample(containers,start))
            def observe():
                due=time.monotonic()+config['resource_interval_seconds']
                while not stop.wait(max(0,due-time.monotonic())):
                    try:rows.extend(sample(containers,start))
                    except Exception as error:errors.append(type(error).__name__);return
                    due+=config['resource_interval_seconds']
            thread=threading.Thread(target=observe);thread.start()
            request=lambda case:http_request(port,case,config['timeout_seconds'])
            first=[];begin=time.monotonic()
            for case in cases:
                t=time.monotonic();ok=request(case);elapsed=time.monotonic()-t
                first.append(dict(group=case['group'],ok=ok,latency=elapsed,service=elapsed))
            result['first_contact']=summarize(first,len(cases),time.monotonic()-begin)
            if not all(s['ok'] for s in first):raise ValueError('Pre-load parity failed')
            for label,duration in [('warmup',config['warmup_seconds']),('measurement',config['measurement_seconds'])]:
                print('T8-A: '+label+' '+str(duration)+' s a 20 solicitudes/s',flush=True)
                result[label]=phase(cases,rate=config['rate'],seconds=duration,workers=config['workers'],request=request,max_lag=config['max_dispatch_lag_seconds'])
            stop.set();thread.join();rows.extend(sample(containers,start))
            result['resources_ok']=not errors and accepted(rows)
            for s,before in identities.items():same_service(before,service(v,s))
            check_airflow(ROOT,v)
            result['source_preserved']=state_hash(ROOT,v)==env['source_state_sha256']
            checkout_commit(ROOT,commit)
            measured=result['measurement']
            if not (result['resources_ok'] and result['source_preserved'] and measured['latency_seconds']['p95'] is not None
                and measured['latency_seconds']['p95']<config['p95_seconds_strict_max'] and measured['error_fraction']<config['error_fraction_strict_max']
                and all(result[k]['clock_discrepancy_seconds']<=2 for k in ('warmup','measurement'))):raise ValueError('Baseline acceptance failed')
            code=0
        except Exception as error:
            result['error_type']=type(error).__name__
            import traceback
            (private/'failure.log').write_text(traceback.format_exc())
        finally:
            stop.set()
            if thread:thread.join()
    result['generator_cpu_seconds']=time.process_time()-cpu;result['generator_max_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report['status']=result['status']='passed' if code==0 else 'failed';report.update(finished_at=now(),result=result)
    report['steps']=[dict(name='benchmark',log='job.log',status=report['status'],exit_code=code),dict(name='resources',log='resources.log',status='passed' if rows and not errors else 'failed',exit_code=0 if rows and not errors else 1)]
    write_json(folder/'result.json',result);write_json(folder/'environment.json',env);write_json(folder/'resources.log',rows)
    (folder/'commit.txt').write_text(commit+'\n');(folder/'job.log').write_text('benchmark T8-A\nstatus: '+report['status']+'\n')
    archive=package_report(folder,report);read_report(archive)
    print('Reporte cerrado: '+str(archive),flush=True)
    command([sys.executable,'scripts/github-handoff.py','upload',str(archive)],log=private/'upload.log',timeout=300)
    print('Estado: '+report['status']+'; requiere revisión principal. Test sigue reservado.',flush=True)
    return code


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('DETENIDO: '+type(error).__name__+'. Conservar reserva y logs; no repetir.',file=sys.stderr)
        raise SystemExit(1)
