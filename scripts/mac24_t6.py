#!/usr/bin/env python3
"""Operator-run preparation and one T6 attempt; requires existing approved T5."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from bootstrap import read_configuration
from clock_check import checkout_commit
from clock_guard import AwakeGuard
from handoff import read_report, write_json
from mac24_terminal import command, service, same_service, inventory, preserved, require, readiness
from project import compose

ROOT=Path(__file__).resolve().parents[1]
SOURCE='events-mac24-20261023T030009Z-3f97f2a0'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True);a=p.parse_args()
    os.umask(0o077);os.environ['SSL_CERT_FILE']='/etc/ssl/cert.pem';os.environ['DOCKER_CONTEXT']='colima-hm-project'
    checkout_commit(ROOT,a.expected_commit)
    require(command(['git','branch','--show-current'])=='main','Se requiere main limpio')
    require(command(['git','remote','get-url','origin']) in ('https://github.com/emilianodesu/clothing-recommendation.git','git@github.com:emilianodesu/clothing-recommendation.git'),'Remoto inesperado')
    require(command(['uname','-m'])=='arm64' and command(['sysctl','-n','hw.memsize'])==str(24*2**30),'Se requiere mac24')
    require('AC Power' in command(['pmset','-g','batt']),'Conecta AC y abre la tapa')
    v=read_configuration(ROOT/'.env');v['HMREC_SOURCE_COMMIT']=a.expected_commit
    def path(key):
        value=Path(v[key]);return value if value.is_absolute() else ROOT/value
    artifacts=path('ARTIFACTS_DIR');handoff=artifacts/'handoff'
    require(not list(handoff.glob('pipeline-mac24-*')) and not (handoff/'.mac24-t6-once.json').exists(),'Ya existe un intento/reserva T6; no repetir')
    archive=handoff/(SOURCE+'.zip');source=read_report(archive)
    require(source['status']=='passed' and source['result']['protocol']=='events-operational-v1','Falta T5 aprobado')
    pg=service(v,'postgres');old=next(s for s in reversed(source['result']['diagnostics']['snapshots']) if s['service']=='postgres');same_service(old,pg)
    readiness(v)
    engine=json.loads(command(['docker','info','--format','{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}']))
    require(engine['cpus']==4 and 13*2**30<=engine['memory']<=14*2**30,'VM incompatible con 14 GiB/4 CPU')
    active=command(['docker','ps','--format','{{.Label "com.docker.compose.service"}}']).splitlines()
    require(all(n in ('postgres','api','ui','mlflow','qdrant') for n in active),'Trabajo u orquestador ya activo; inspeccionar primero')
    access=json.loads(command(['gh','api','repos/emilianodesu/clothing-recommendation','--jq','{private:.private,push:.permissions.push}']))
    require(access==dict(private=True,push=True),'GitHub privado/escritura requeridos')
    config=artifacts/'pipeline/build.json';require(not config.exists(),'Configuración pipeline previa: inspeccionar')
    selection=json.loads((ROOT/'configs/releases/history-v1.json').read_text())['selection'];q4=handoff/(selection['run_id']+'.zip')
    from mac24_terminal import digest
    require(q4.is_file() and digest(q4)==selection['archive_sha256'],'Falta ZIP Q4 original')
    logs=ROOT/'.runtime'/('mac24-t6-'+uuid.uuid4().hex);logs.mkdir(parents=True)
    print('Logs privados: '+str(logs),flush=True)
    with AwakeGuard(), (artifacts/'.heavy-launch.lock').open('a') as launch_lock:
        fcntl.flock(launch_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        originals=inventory([path('DATA_DIR')/'processed',artifacts/'data-jobs',handoff,artifacts/'quality-budget'])
        write_json(logs/'originals.json',originals)
        with (handoff/'.mac24-t6-once.json').open('x') as f:json.dump({'commit':a.expected_commit},f)
        def logged(name,*args,timeout=1800):
            # All commands use explicit project identity; local logs are never uploaded.
            with (logs/(name+'.log')).open('w') as f:
                env=dict(os.environ,**v)
                for k in ('COMPOSE_FILE','COMPOSE_PROFILES'):env.pop(k,None)
                cmd=['docker','compose','--project-directory',str(ROOT),'--env-file',str(ROOT/'.env'),'--file',str(ROOT/'compose.yaml'),'--project-name',v['COMPOSE_PROJECT_NAME'],*args]
                subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout,check=True)
        print('T6: construir imágenes y comprobar suite pipeline',flush=True)
        logged('build-jobs','build','jobs','app-check');logged('build-airflow','build','airflow-scheduler')
        command([sys.executable,'scripts/project.py','pipeline','init','--skip-build'],log=logs/'init.log',timeout=600)
        command([sys.executable,'scripts/project.py','verify','--suite','pipeline','--existing-services'],log=logs/'verify.log',timeout=1800)
        same_service(pg,service(v,'postgres'))
        (artifacts/'pipeline').mkdir(exist_ok=True)
        write_json(config,dict(dataset_dir='/data/processed/hm-v1',output_dir='/artifacts/pipeline/release-'+a.expected_commit[:12],data_as_of='2020-09-09',synthetic=False,evidence_archive='/artifacts/handoff/'+q4.name,activate=False))
        # T6 adds API metrics; a controlled API replacement is deliberate. PostgreSQL/UI stay intact.
        logged('api-upgrade','up','-d','--no-deps','--wait','--wait-timeout','180','api')
        readiness(v);same_service(pg,service(v,'postgres'))
        command([sys.executable,'scripts/project.py','pipeline','up'],log=logs/'up.log',timeout=300)
        command([sys.executable,'scripts/project.py','pipeline','trigger','hm_build_release'],log=logs/'trigger.log',timeout=120)
        print('T6: esperando construcción H; activación deshabilitada para conservar H vigente',flush=True)
        deadline=time.monotonic()+2100
        query="from hm_recommender.database import connect\nwith connect('airflow') as c:print(c.execute(\"SELECT state FROM dag_run WHERE dag_id='hm_build_release' ORDER BY id DESC LIMIT 1\").fetchone()[0])"
        while True:
            state=compose(ROOT,v,'exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',query,capture=True,timeout=30).strip()
            if state=='success':break
            require(state!='failed' and time.monotonic()<deadline,'DAG fallido o timeout; conservar logs y no repetir')
            time.sleep(5)
        print('T6: ensayo programado único; mantén Terminal abierta',flush=True)
        fcntl.flock(launch_lock,fcntl.LOCK_UN)
        code=command([sys.executable,'scripts/project.py','pipeline-check','--machine-id','mac24','--expected-commit',a.expected_commit,'--source-run-id',SOURCE],log=logs/'check.log',timeout=4500,check=False)
        candidates=list(handoff.glob('pipeline-mac24-*.zip'));require(len(candidates)==1,'Falta ZIP cerrado: conservar logs, no repetir')
        report=read_report(candidates[0])
        preservation={'unchanged':False,'files':len(originals)}
        try:preservation=preserved(originals);same_service(pg,service(v,'postgres'))
        except (ValueError,OSError,subprocess.SubprocessError) as error:preservation.update(unchanged=False,error_type=type(error).__name__)
        command([sys.executable,'scripts/github-handoff.py','upload',str(candidates[0])],log=logs/'upload.log',timeout=600)
        checkout_commit(ROOT,a.expected_commit)
        result=dict(schema_version=1,run_id=report['run_id'],source_commit=a.expected_commit,status=report['status'],archive_bytes=candidates[0].stat().st_size,archive_sha256=digest(candidates[0]),release_url='https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-'+report['run_id'],result=report['result'],operator_claims=dict(verification_suite='pipeline',verification_exit_code=0,preservation=preservation,engine=engine),review_status='pending_primary_review')
        write_json(ROOT/'evidence/pipeline-mac24.json',result)
        command(['git','add','evidence/pipeline-mac24.json']);command(['git','commit','-m','docs: publish mac24 T6 pipeline evidence'])
        command(['git','push','origin','main'],log=logs/'push.log',timeout=120)
        print('Reporte publicado: '+result['release_url'],flush=True)
        print('Estado: '+report['status']+'; T6 requiere revisión principal',flush=True)
        require(preservation['unchanged'],'Investigar preservación; ZIP publicado')
        return code


if __name__=='__main__':
    try:raise SystemExit(main())
    except (Exception,KeyboardInterrupt) as error:
        print('DETENIDO: '+type(error).__name__+'. Conserva logs y no repitas T6.',file=sys.stderr)
        if isinstance(error,ValueError):print(str(error),file=sys.stderr)
        raise SystemExit(1)
