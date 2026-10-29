#!/usr/bin/env python3
"""Operator-run T5 workflow. Does not open SSH or repeat an operational attempt."""
import argparse
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

from bootstrap import read_configuration
from clock_check import checkout_commit
from clock_guard import AwakeGuard
from handoff import read_report, write_json, now
from project import compose
from serving_check import source

ROOT=Path(__file__).resolve().parents[1]
SOURCE='release-mac24-20261021T190717Z-f0a2d073'
RELEASE='h-d12e30cb4d8c44cc22468e86c634db566e6f126ddc704e36723fe09d9a8a7b0a'
HISTORICAL={'events-mac24-20261022T043907Z-ddcd2e42',
            'events-mac24-20261022T184956Z-e167c9d2','events-mac24-20261023T012714Z-659b1686'}
MARKER='.mac24-operational-once.json'


def require(value,message):
    if not value:raise ValueError(message)


def check_attempts(handoff):
    require(not (handoff/MARKER).exists(),'Ya hay una reserva T5; inspeccionarla, no repetir.')
    for path in handoff.glob('events-mac24-*'):
        name=path.name.removesuffix('.partial').removesuffix('.zip')
        require(name in HISTORICAL,'Existe otro intento mac24, incluso posiblemente incompleto; no repetir.')


def once(handoff,commit,job):
    check_attempts(handoff)
    with (handoff/MARKER).open('x') as stream:
        os.chmod(handoff/MARKER,0o600)
        json.dump(dict(commit=commit,reserved_at=now()),stream)
        stream.flush();os.fsync(stream.fileno())
    return job() # Deliberately never remove the reservation, including on interruption.


def sequence(handoff,commit,verify,job,publish):
    check_attempts(handoff);verify()
    code=once(handoff,commit,job)
    publish() # A closed failed run is still evidence.
    return code


def same_service(before,after):
    require(all(before[k]==after[k] for k in ('container_id','started_at','restart_count','image_id')),
            'Cambió identidad/reinicio/imagen del servicio: detener sin recrearlo.')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def inventory(roots):
    result={}
    for root in roots:
        require(root.is_dir() and not root.is_symlink(),'Falta un directorio original requerido.')
        for path in sorted(root.rglob('*')):
            require(not path.is_symlink(),'Inventario privado contiene un enlace simbólico.')
            if path.is_file():result[str(path)]=dict(bytes=path.stat().st_size,sha256=digest(path))
    return result


def preserved(before):
    for name,entry in before.items():
        p=Path(name)
        require(p.is_file() and not p.is_symlink() and p.stat().st_size==entry['bytes'] and digest(p)==entry['sha256'],
                'Un original cambió o desapareció; conservar inventarios privados e investigar.')
    return dict(files=len(before),inventory_sha256=hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),unchanged=True)


def check_preservation(before, values, initial):
    result=dict(unchanged=False,files=len(before))
    try:
        result=preserved(before)
        for name in ('postgres','ui'):same_service(initial[name],service(values,name))
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as error:
        result['unchanged']=False
        result['error_type']=type(error).__name__
    return result


def command(args,*,log=None,timeout=60,check=True):
    if log:
        with log.open('w') as stream:
            os.chmod(log,0o600)
            result=subprocess.run(args,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
    else:
        result=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
    if check:require(result.returncode==0,'Falló un requisito/comando; revisar el log privado sin reintentar T5.')
    return result.returncode if log else result.stdout.strip()


def service(values,name):
    cid=compose(ROOT,values,'ps','-q',name,capture=True,timeout=30).strip()
    require(bool(cid),'Falta un servicio activo: '+name)
    template='{"container_id":{{json .Id}},"started_at":{{json .State.StartedAt}},"restart_count":{{.RestartCount}},"image_id":{{json .Image}},"running":{{.State.Running}}}'
    value=json.loads(command(['docker','inspect','--format',template,cid]))
    require(value.pop('running') is True,'Servicio detenido: '+name)
    return value


def readiness(values):
    connection=http.client.HTTPConnection('127.0.0.1',int(values['API_PORT']),timeout=10)
    try:
        connection.request('GET','/health/ready',headers={'Authorization':'Bearer '+values['HM_APP_PASSWORD']})
        response=connection.getresponse();require(response.status==200,'API no está ready.')
        raw=response.read(65537);require(len(raw)<=65536,'Respuesta de health inesperada.')
        require(json.loads(raw)['release_id']==RELEASE,'La versión activa no es H full esperado.')
    finally:connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-commit',required=True)
    args=parser.parse_args()
    os.environ['SSL_CERT_FILE']='/etc/ssl/cert.pem';os.environ['DOCKER_CONTEXT']='colima-hm-project'
    os.umask(0o077)
    checkout_commit(ROOT,args.expected_commit)
    require(command(['git','branch','--show-current'])=='main','Se requiere main limpio.')
    require(command(['git','remote','get-url','origin']) in ('https://github.com/emilianodesu/clothing-recommendation.git','git@github.com:emilianodesu/clothing-recommendation.git'), 'Remoto inesperado.')
    require(sys.platform=='darwin' and command(['uname','-m'])=='arm64','Se requiere mac24 Apple Silicon.')
    require(command(['sysctl','-n','hw.memsize'])==str(24*2**30),'Esta no es la Mac de 24 GiB.')
    require('AC Power' in command(['pmset','-g','batt']),'Conecta mac24 a alimentación AC y abre la tapa.')
    values=read_configuration(ROOT/'.env')
    def directory(key):
        p=Path(values[key]);return p if p.is_absolute() else ROOT/p
    artifacts=directory('ARTIFACTS_DIR');handoff=artifacts/'handoff';data=directory('DATA_DIR')
    check_attempts(handoff)
    require(not (ROOT/'evidence/events-operational-mac24.json').exists(),'Ya existe evidencia operativa; revisarla antes de ejecutar.')
    work=ROOT/'.runtime'/('mac24-terminal-'+uuid.uuid4().hex);work.mkdir(parents=True)
    print('Logs privados: '+str(work),flush=True)
    with (artifacts/'.heavy-launch.lock').open('a') as lock,AwakeGuard():
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        engine=json.loads(command(['docker','info','--format','{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}']))
        require(engine['cpus']==4 and 13*2**30<=engine['memory']<=14*2**30,'Capacidad Docker incompatible con Colima 14 GiB/4 CPU.')
        active=command(['docker','ps','--format','{{.Label "com.docker.compose.service"}}']).splitlines()
        require(all(s in ('api','ui','postgres','mlflow','qdrant') for s in active),'Hay contenedores no reconocidos o trabajos activos; inspeccionar sin detenerlos.')
        access=json.loads(command(['gh','api','repos/emilianodesu/clothing-recommendation','--jq','{private:.private,push:.permissions.push}']))
        require(access.get('push') is True,'GitHub requiere permiso de escritura en el repositorio.')
        require((data/'processed/hm-v1/manifest.json').is_file(),'Faltan datos T2 H&M; no se descargan ni reconstruyen automáticamente.')
        require(source(artifacts,SOURCE)['release_id']==RELEASE,'Fuente T4-A distinta.')
        historical=json.loads((ROOT/'evidence/events-memory-mac24.json').read_text())
        archive=next((artifacts/p/(historical['run_id']+'.zip') for p in ('handoff','incoming') if (artifacts/p/(historical['run_id']+'.zip')).is_file()),None)
        require(archive is not None and digest(archive)==historical['archive_sha256'],'Falta el diagnóstico original o su huella difiere.')
        snapshots=read_report(archive)['result']['diagnostics']['snapshots']
        pg=next(s for s in reversed(snapshots) if s['service']=='postgres')
        initial={name:service(values,name) for name in ('postgres','api','ui')}
        same_service(pg,initial['postgres']);readiness(values)
        roots=[data/'processed',artifacts/'data-jobs',handoff,artifacts/'quality-budget']
        print('Verificando inventario de originales; no incluye imágenes/ZIP crudos de Kaggle.',flush=True)
        before=inventory(roots);write_json(work/'inventory-before.json',before)
        write_json(work/'preflight.json',dict(engine=engine,services=initial))
        def verify():
            print('Construyendo sólo jobs y ejecutando suite events...',flush=True)
            # Compose receives the project's explicit identity; never recreate live services.
            with (work/'build.log').open('w') as log:
                environment=dict(os.environ);environment.update(values)
                for key in ('COMPOSE_FILE','COMPOSE_PROFILES'):environment.pop(key,None)
                subprocess.run(['docker','compose','--project-directory',str(ROOT),'--env-file',str(ROOT/'.env'),'--file',str(ROOT/'compose.yaml'),'--project-name',values['COMPOSE_PROJECT_NAME'],'build','jobs'],env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
            command([sys.executable,'scripts/project.py','verify','--suite','events','--existing-services'],log=work/'verify.log',timeout=1800)
            checkout_commit(ROOT,args.expected_commit)
            for name in initial:same_service(initial[name],service(values,name))
            readiness(values)
        def job():
            print('Iniciando la única corrida T5. Mantén esta terminal abierta.',flush=True)
            return command([sys.executable,'scripts/project.py','events-check','--machine-id','mac24','--expected-commit',args.expected_commit,'--source-run-id',SOURCE,'--operational'],log=work/'job.log',timeout=7500,check=False)
        def publish():
            candidates=[p for p in handoff.glob('events-mac24-*.zip') if p.stem not in HISTORICAL]
            require(len(candidates)==1,'No hay exactamente un ZIP nuevo cerrado; conservar carpeta/log y no repetir.')
            p=candidates[0];report=read_report(p)
            require(report['source_commit']==args.expected_commit and report['machine_id']=='mac24' and report['result']['protocol']=='events-operational-v1','Identidad del reporte inesperada.')
            preservation=check_preservation(before,values,initial)
            write_json(work/'preservation.json',preservation)
            command([sys.executable,'scripts/github-handoff.py','upload',str(p)],log=work/'upload.log',timeout=600)
            url='https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-'+report['run_id']
            print('Reporte publicado: '+url,flush=True)
            checkout_commit(ROOT,args.expected_commit)
            with zipfile.ZipFile(p) as bundle:environment=json.loads(bundle.read('environment.json'))
            evidence=dict(schema_version=1,run_id=report['run_id'],source_commit=report['source_commit'],status=report['status'],protocol='events-operational-v1',archive_bytes=p.stat().st_size,archive_sha256=digest(p),release_url=url,result=report['result'],environment=environment,review_status='pending_primary_review',operator_workflow=dict(suite='events',verification_exit_code=0,preservation=preservation,postgres_historical_identity_verified=True,engine=engine))
            with (ROOT/'evidence/events-operational-mac24.json').open('x') as stream:json.dump(evidence,stream,indent=2);stream.write('\n')
            with (ROOT/'docs/events-operational.md').open('a') as stream:stream.write('\nMac24 publicó `'+report['run_id']+'`, estado `'+report['status']+'`. Revisión principal pendiente; no cierra T5/E4.\n')
            command(['git','add','evidence/events-operational-mac24.json','docs/events-operational.md'])
            command(['git','commit','-m','docs: publish mac24 operational evidence'])
            command(['git','push','origin','main'],log=work/'push.log',timeout=120)
            print('Estado: '+report['status']+'; T5/E4 requieren revisión principal.',flush=True)
            require(preservation['unchanged'],'Reporte publicado, pero preservación requiere investigación.')
        return sequence(handoff,args.expected_commit,verify,job,publish)


if __name__=='__main__':
    try:raise SystemExit(main())
    except (Exception,KeyboardInterrupt) as error:
        print('DETENIDO: '+type(error).__name__+'. Conserva logs privados y no repitas T5.',file=sys.stderr)
        if isinstance(error,ValueError):print(str(error),file=sys.stderr)
        raise SystemExit(1)
