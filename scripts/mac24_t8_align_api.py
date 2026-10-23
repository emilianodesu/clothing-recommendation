#!/usr/bin/env python3
"""One explicit API-only code alignment before T8; never starts benchmark load."""
import argparse
import fcntl
import json
from http.client import HTTPConnection
import os
from pathlib import Path
import sys
import time
import traceback
from bootstrap import read_configuration
from clock_check import checkout_commit
from clock_guard import AwakeGuard
from handoff import write_json
from mac24_terminal import command, service, same_service, RELEASE
from mac24_t7 import check_airflow
from pipeline_check import request_json
from portability_check import state_hash
from benchmark_resources import sample
from project import compose

ROOT=Path(__file__).resolve().parents[1]
OLD='5e0ba69395ee17158d4b823d63776dadc072a5559204b3101df00b348765d442'
NEW='b1f69269be9960d1df0f2693e0f13b53ce0cbaabcd98ea1f6ad68703f5a3a7b3'


def require(ok):
    if not ok:raise ValueError('Alignment prerequisite/preservation failed; inspect private report')


def replace_api(values, image):
    require(command(['docker','image','inspect','hmrec-app:0.1.0','--format','{{.Id}}'])==image)
    # Explicit service, no dependency recreation, image build, pull or migration.
    compose(ROOT,values,'up','-d','--no-deps','--no-build','--pull','never','api',capture=True,timeout=180)


def verify_preserved(before,after,state_before,state_after):
    for name in ('postgres','ui'):same_service(before[name],after[name])
    require(state_before==state_after)


def run(commit):
    os.umask(0o077)
    os.environ.update(SSL_CERT_FILE='/etc/ssl/cert.pem',DOCKER_CONTEXT='colima-hm-project')
    checkout_commit(ROOT,commit)
    require('AC Power' in command(['pmset','-g','batt']))
    v=read_configuration(ROOT/'.env');art=Path(v['ARTIFACTS_DIR'])
    if not art.is_absolute():art=ROOT/art
    require(not (art/'handoff/.mac24-t8-baseline-once.json').exists())
    require(not list((art/'handoff').glob('benchmark-mac24-*')))
    folder=ROOT/'.runtime/t8-api-alignment'
    require(not folder.exists())
    folder.mkdir(parents=True)
    print('Logs privados: '+str(folder),flush=True)
    report=dict(status='failed',source_commit=commit,old_code=OLD,new_code=NEW,load_started=False)
    try:
        with AwakeGuard(),(art/'.heavy-launch.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            check_airflow(ROOT,v)
            review=json.loads((ROOT/'evidence/portability-review-mac24.json').read_text())
            require(review['t7_closed'])
            images=review['result']['images']
            for tag,image in images.items():
                require(command(['docker','image','inspect',tag,'--format','{{.Id}}'])==image)
            sys.path.insert(0,str(ROOT/'src'))
            from hm_recommender.api.runtime import source_digest
            require(source_digest(ROOT/'src/hm_recommender')==NEW)
            program='from hm_recommender.api.runtime import source_digest;print(source_digest())'
            code=command(['docker','run','--rm','--network','none','--read-only','--memory','256m','--cpus','1',images['hmrec-app:0.1.0'],'python','-c',program])
            require(code==NEW)
            def http(path,body=None):return request_json(int(v['API_PORT']),v['HM_APP_PASSWORD'],path,body)
            before={s:service(v,s) for s in ('api','postgres','ui')}
            same_service(review['result']['source_postgres'],before['postgres'])
            require(http('/health/live')['source_tree_sha256']==OLD)
            ready=http('/health/ready');require(ready['release_id']==RELEASE)
            state=state_hash(ROOT,v)
            probe={'customer_id':'unknown-t8-api-alignment','k':12}
            response=http('/recommend',probe);response.pop('request_id',None)
            report.update(before=before,state_before=state,ready_before=ready,
                resources_before=sample({s:d['container_id'] for s,d in before.items()},time.monotonic()))
            c=HTTPConnection('127.0.0.1',int(v['API_PORT']),timeout=30)
            try:
                c.request('GET','/metrics');r=c.getresponse();raw=r.read(1024*1024+1)
                require(r.status==200 and len(raw)<=1024*1024)
                (folder/'api-metrics-before.txt').write_bytes(raw)
            finally:c.close()
            write_json(folder/'report.json',report)
            print('Actualizando únicamente API a la imagen T7 verificada...',flush=True)
            replace_api(v,images['hmrec-app:0.1.0'])
            deadline=time.monotonic()+180
            while True:
                try:
                    require(http('/health/live')['source_tree_sha256']==NEW)
                    require(http('/health/ready')==ready)
                    break
                except Exception:
                    if time.monotonic()>=deadline:raise
                    time.sleep(2)
            after={s:service(v,s) for s in before}
            require(after['api']['image_id']==images['hmrec-app:0.1.0'])
            state_after=state_hash(ROOT,v)
            verify_preserved(before,after,state,state_after)
            actual=http('/recommend',probe);actual.pop('request_id',None);require(actual==response)
            check_airflow(ROOT,v)
            report.update(status='passed',after=after,state_after=state_after,unknown_customer_parity=True,
                resources_after=sample({s:d['container_id'] for s,d in after.items()},time.monotonic()))
    except Exception:
        (folder/'failure.log').write_text(traceback.format_exc())
        raise
    finally:write_json(folder/'report.json',report)
    print('Alineación API: passed; PostgreSQL/UI y estado agregado conservados. No se inició T8.',flush=True)
    print('Ejecutar ahora sólo --preflight-only con el mismo commit.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True)
    try:run(p.parse_args().expected_commit)
    except Exception as error:
        print('DETENIDO: '+type(error).__name__+'. Conservar logs; no repetir ni borrar reservas.',file=sys.stderr)
        sys.exit(1)
