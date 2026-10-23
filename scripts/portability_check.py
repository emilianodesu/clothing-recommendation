"""One bounded T7 rehearsal; only aggregate evidence is eligible for GitHub."""
import argparse
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
import zipfile
from bootstrap import read_configuration
from clock_check import checkout_commit,runner_digest
from clock_guard import AwakeGuard
from handoff import now,write_json,package_report,read_report
from portability_handoff import empty_result,validate
from project import compose
from backup import create
from restore import restore_into,offline_check,checksum_file
from pipeline_check import request_json

FAILED_T7='portability-mac24-20261023T203910Z-ed135689'
FAILED_T7_SHA='9c62660615e4f98a9b6b3f2cf25822b5ad27131969e8be3edf90fd036cfe78be'


def check_attempts(handoff,machine,followup=False):
    existing={p.name for p in handoff.glob('portability-'+machine+'-*')}
    if not followup:
        if existing:raise ValueError('Existing T7 attempt; do not repeat')
        return
    archive=handoff/(FAILED_T7+'.zip')
    if (machine!='mac24' or not archive.is_file() or archive.is_symlink()
            or existing-{FAILED_T7,FAILED_T7+'.zip'}
            or checksum_file(archive)!=FAILED_T7_SHA):
        raise ValueError('Follow-up requires only the unchanged audited failed T7')
    old=read_report(archive)
    if old['status']!='failed' or old['run_id']!=FAILED_T7:
        raise ValueError('Invalid failed T7 source')


def identity(root,v):
    cid=compose(root,v,'ps','--status','running','-q','postgres',capture=True,timeout=30).strip()
    if not cid:raise ValueError('PostgreSQL is not running')
    d=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]
    return dict(container_id=d['Id'],started_at=d['State']['StartedAt'],restart_count=d['RestartCount'],image_id=d['Image'])


def state_hash(root,v):
    program="""import hashlib,json
from hm_recommender.database import connect
from hm_recommender.portability import state,TABLES
from psycopg import sql
with connect() as c:
 c.execute('SET TRANSACTION READ ONLY')
 value=dict(state=state(c),counts={t:c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in TABLES})
 print(hashlib.sha256(json.dumps(value,default=str,sort_keys=True).encode()).hexdigest())
"""
    return compose(root,v,'run','--rm','--no-deps','-T','jobs','python','-c',program,capture=True,timeout=300).strip()


def run(root,machine,commit,destination,port,source_run=None,*,airflow_followup=False):
    root=Path(root);checkout_commit(root,commit);os.umask(0o077)
    v=read_configuration(root/'.env');art=Path(v['ARTIFACTS_DIR']);art=art if art.is_absolute() else root/art
    handoff=art/'handoff';handoff.mkdir(exist_ok=True)
    if source_run or airflow_followup:check_attempts(handoff,machine,airflow_followup)
    rid='portability-'+machine+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    folder=handoff/rid;folder.mkdir(mode=0o700)
    report=dict(schema_version=1,kind='portability-handoff',run_id=rid,machine_id=machine,context=os.environ.get('DOCKER_CONTEXT','default'),source_commit=commit,source_clean=True,started_at=now(),finished_at=None,status='failed',warnings=[],steps=[])
    result=empty_result();environment=dict(source_commit=commit,runner_tree_sha256=runner_digest(root))
    h=hashlib.sha256();base=root/'src/hm_recommender'
    for p in sorted(base.rglob('*.py')):h.update(str(p.relative_to(base)).encode()+b'\0'+p.read_bytes()+b'\0')
    environment['source_tree_sha256']=h.hexdigest();start=time.monotonic();code=1
    try:
        with AwakeGuard(),(art/'.heavy-launch.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result['source_postgres']=identity(root,v)
            if source_run:
                source=handoff/(source_run+'.zip');old=read_report(source)
                if old['status']!='passed' or old['result']['protocol']!='pipeline-v2':raise ValueError('Expected accepted T6 source')
                expected=next(s for s in old['result']['resources'] if s['service']=='postgres' and s['phase']=='after')
                if any(expected[k]!=value for k,value in result['source_postgres'].items()):raise ValueError('PostgreSQL changed since T6; inspect first')
                result.update(source_run_id=source_run,source_archive_sha256=checksum_file(source))
            result['source_state_sha256']=state_hash(root,v)
            print('T7: respaldo lógico privado',flush=True)
            archive=create(root);result.update(archive_bytes=archive.stat().st_size,archive_sha256=checksum_file(archive))
            with zipfile.ZipFile(archive) as z:manifest=json.loads(z.read('backup.json'))
            for case in manifest['parity']:
                response=request_json(int(v['API_PORT']),v['HM_APP_PASSWORD'],'/recommend',case['request']);response.pop('request_id',None)
                if response!=case['response']:raise ValueError('Source HTTP parity differs')
            result['checks']['source_parity']=True
            print('T7: restauración en instalación nueva; el original permanece activo',flush=True)
            restored=restore_into(root,archive,Path(destination),result['archive_sha256'],port)
            target=read_configuration(Path(destination)/'.env')
            result['destination_postgres']=identity(destination,target)
            result['images']=restored['images'];result['parity_queries']=restored['parity_queries']
            result['checks']['restored_parity']=result['parity_queries']==6
            result['checks']['fresh_credentials']=all(v[k]!=target[k] for k in ('POSTGRES_PASSWORD','HM_APP_PASSWORD','HM_AIRFLOW_PASSWORD','HM_MLFLOW_PASSWORD'))
            print('T7: comprobación sin salida a Internet en el destino',flush=True)
            result['offline_queries']=offline_check(destination);result['checks']['offline']=result['offline_queries']==6
            result['checks']['source_preserved']=(identity(root,v)==result['source_postgres'] and state_hash(root,v)==result['source_state_sha256'])
            if not all(result['checks'].values()):raise ValueError('Acceptance control failed')
            code=0
    except BaseException as error:
        result['error_type']=type(error).__name__;print('T7 detenido: '+type(error).__name__+'; conservar logs y no repetir.',flush=True)
        # Trace stays private; only the error class is exported.
        import traceback
        private=root/'.runtime'/('portability-error-'+rid+'.log');private.parent.mkdir(exist_ok=True)
        private.write_text(traceback.format_exc())
    result['duration_seconds']=time.monotonic()-start;report['status']=result['status']='passed' if code==0 else 'failed'
    validate(report,result,environment)
    report.update(result=result,finished_at=now(),steps=[dict(name='portability',log='job.log',status=report['status'],exit_code=code)])
    write_json(folder/'result.json',result);write_json(folder/'environment.json',environment)
    (folder/'commit.txt').write_text(commit+'\n');(folder/'job.log').write_text('portability T7\nstatus: '+report['status']+'\n')
    archive=package_report(folder,report)
    print('Reporte cerrado: '+str(archive),flush=True)
    return code,archive


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--machine-id',required=True);p.add_argument('--expected-commit',required=True)
    p.add_argument('--destination',type=Path,required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--source-run-id')
    a=p.parse_args()
    import re
    if not re.fullmatch('[a-z][a-z0-9_-]{0,31}',a.machine_id):raise ValueError('Invalid machine ID')
    return run(Path(__file__).resolve().parents[1],a.machine_id,a.expected_commit,a.destination,a.port,a.source_run_id)[0]
if __name__=='__main__':raise SystemExit(main())
