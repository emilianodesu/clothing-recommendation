#!/usr/bin/env python3
"""Prepare and execute one T7 recovery rehearsal; preserve the original installation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from bootstrap import read_configuration
from clock_check import checkout_commit
from clock_guard import AwakeGuard
from handoff import read_report,write_json
from mac24_terminal import command,service,same_service,inventory,preserved,require
from portability_check import run,check_attempts,FAILED_T7,FAILED_T7_SHA
from project import compose

ROOT=Path(__file__).resolve().parents[1]
SOURCE='pipeline-mac24-20261023T193510Z-532c902f'


def check_airflow(root,values):
    compose(root,values,'exec','-T','airflow-scheduler','/opt/airflow-env/bin/python',
            '/opt/airflow/entrypoint.py','db','check',capture=True,timeout=60)
    program="""from hm_recommender.database import connect
import json
with connect('airflow') as c:
 c.execute('SET TRANSACTION READ ONLY')
 print(json.dumps(dict(c.execute("SELECT dag_id,is_paused FROM dag WHERE dag_id IN ('hm_build_release','hm_refresh_profiles')").fetchall())))
"""
    states=json.loads(compose(root,values,'exec','-T','airflow-scheduler',
        '/opt/hmrec/.venv/bin/python','-c',program,capture=True,timeout=60))
    require(set(states)=={'hm_build_release','hm_refresh_profiles'} and all(v is True for v in states.values()),'Conservar ambos DAG pausados; inspeccionar antes de continuar')
    return states


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True)
    p.add_argument('--airflow-followup',action='store_true');a=p.parse_args()
    os.umask(0o077);os.environ['SSL_CERT_FILE']='/etc/ssl/cert.pem';os.environ['DOCKER_CONTEXT']='colima-hm-project'
    checkout_commit(ROOT,a.expected_commit)
    require(command(['git','branch','--show-current'])=='main','Se requiere main limpio')
    require(command(['uname','-m'])=='arm64' and command(['sysctl','-n','hw.memsize'])==str(24*2**30),'Se requiere mac24')
    require('AC Power' in command(['pmset','-g','batt']),'Conecta AC y abre la tapa')
    review=json.loads((ROOT/'evidence/pipeline-review-mac24.json').read_text())
    require(review['t6_closed'] is True and review['run_id']==SOURCE,'Falta auditoría T6')
    v=read_configuration(ROOT/'.env')
    def local(key):
        p=Path(v[key]);return p if p.is_absolute() else ROOT/p
    artifacts=local('ARTIFACTS_DIR');handoff=artifacts/'handoff'
    marker=handoff/('.mac24-t7-airflow-followup-once.json' if a.airflow_followup else '.mac24-t7-once.json')
    require(not marker.exists(),'Existe reserva de este intento T7; no repetir')
    check_attempts(handoff,'mac24',a.airflow_followup)
    if a.airflow_followup:
        original_marker=handoff/'.mac24-t7-once.json'
        require(json.loads(original_marker.read_text())=={'commit':'f6bf4741a4951cc3b1fa9d00a985693c01f20e37'},'Conservar reserva original T7')
    source=handoff/(SOURCE+'.zip')
    from restore import checksum_file
    require(checksum_file(source)==review['archive']['sha256'],'Fuente T6 distinta')
    require(read_report(source)['status']=='passed','Fuente T6 inválida')
    pg=service(v,'postgres');api=service(v,'api')
    engine=json.loads(command(['docker','info','--format','{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}']))
    require(engine['cpus']==4 and 13*2**30<=engine['memory']<=14*2**30,'Conservar VM14GiB/4CPU')
    logs=ROOT/'.runtime'/('mac24-t7-'+uuid.uuid4().hex);logs.mkdir(parents=True)
    print('Logs privados: '+str(logs),flush=True)
    originals=inventory([local('DATA_DIR')/'processed',artifacts/'data-jobs',artifacts/'quality-budget',artifacts/'serving',handoff])
    write_json(logs/'originals.json',originals)
    with AwakeGuard():
        check_airflow(ROOT,v)
        print('T7: construir jobs/app y verificar suite portability; no recrear el original',flush=True)
        cmd=['docker','compose','--project-directory',str(ROOT),'--env-file',str(ROOT/'.env'),'--file',str(ROOT/'compose.yaml'),'--project-name',v['COMPOSE_PROJECT_NAME'],'build','jobs','app-check']
        command(cmd,log=logs/'build.log',timeout=1800)
        command([sys.executable,'scripts/project.py','verify','--suite','portability','--existing-services'],log=logs/'verify.log',timeout=1800)
        same_service(pg,service(v,'postgres'));same_service(api,service(v,'api'))
        check_airflow(ROOT,v)
        reservation={'commit':a.expected_commit}
        if a.airflow_followup:reservation.update(previous_run_id=FAILED_T7,previous_archive_sha256=FAILED_T7_SHA)
        with marker.open('x') as f:json.dump(reservation,f)
        destination='T7 restored installation airflow followup' if a.airflow_followup else 'T7 restored installation'
        code,archive=run(ROOT,'mac24',a.expected_commit,ROOT/'.runtime'/destination,18024,SOURCE,airflow_followup=a.airflow_followup)
    try:
        claims=preserved(originals);same_service(pg,service(v,'postgres'));same_service(api,service(v,'api'));check_airflow(ROOT,v)
    except Exception as error:
        claims=dict(unchanged=False,error_type=type(error).__name__);code=1
    result=read_report(archive)
    command([sys.executable,'scripts/github-handoff.py','upload',str(archive)],log=logs/'upload.log',timeout=300)
    url='https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-'+result['run_id']
    evidence='evidence/portability-airflow-followup-mac24.json' if a.airflow_followup else 'evidence/portability-mac24.json'
    write_json(ROOT/evidence,dict(schema_version=1,run_id=result['run_id'],source_commit=a.expected_commit,status=result['status'],archive_bytes=archive.stat().st_size,archive_sha256=checksum_file(archive),release_url=url,result=result['result'],previous_attempt=dict(run_id=FAILED_T7,archive_sha256=FAILED_T7_SHA) if a.airflow_followup else None,operator_claims=dict(verification_suite='portability',verification_exit_code=0,preservation=claims,engine=engine),review_status='pending_primary_review'))
    command(['git','add',evidence])
    command(['git','commit','-m','docs: publish mac24 T7 recovery evidence'],log=logs/'commit.log')
    command(['git','push','origin','HEAD:main'],log=logs/'push.log',timeout=120)
    print('Reporte publicado: '+url,flush=True);print('Estado: '+result['status']+'; T7 requiere revisión principal. No iniciar T8.',flush=True)
    return code
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('DETENIDO: '+type(error).__name__+'. Conserva logs, respaldo, destino y reserva; no reintentes automáticamente.',file=sys.stderr)
        raise SystemExit(1)
