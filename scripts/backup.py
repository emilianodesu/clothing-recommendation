#!/usr/bin/env python3
"""Create a private application backup; pause and restore the original DAG schedule."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid
from bootstrap import read_configuration
from project import compose


def create(root):
    root=Path(root);v=read_configuration(root/'.env')
    def call(*args):return compose(root,v,*args,capture=True,timeout=2100).strip()
    if not call('ps','--status','running','-q','postgres'):raise ValueError('PostgreSQL must already be running')
    artifacts=Path(v['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else root/artifacts
    folder=artifacts/'backups';folder.mkdir(exist_ok=True,mode=0o700)
    name='backup-'+uuid.uuid4().hex+'.zip';original={}
    scheduler=call('ps','--status','running','-q','airflow-scheduler')
    # docker exec does not inherit environment configured by PID 1's entrypoint.
    # Reuse the configured CLI used by T6; never pass connection secrets as args.
    def airflow(*args):return call('exec','-T','airflow-scheduler','/opt/airflow-env/bin/python','/opt/airflow/entrypoint.py',*args)
    try:
        if scheduler:
            # CLI list emits warnings and string booleans in Airflow 3.3; read typed DB state.
            program="from hm_recommender.database import connect\nimport json\nwith connect('airflow') as c: print(json.dumps(dict(c.execute(\"SELECT dag_id,is_paused FROM dag WHERE dag_id IN ('hm_build_release','hm_refresh_profiles')\").fetchall())))"
            original=json.loads(call('exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',program))
            if not all(type(v) is bool for v in original.values()):raise ValueError('Unrecognized DAG pause state')
            if len(original)!=2:raise ValueError('Expected two installed DAGs')
            for dag in original:airflow('dags','pause',dag)
            # Pause prevents new schedules, but wait for tasks already queued/running.
            program="from hm_recommender.database import connect\nwith connect('airflow') as c: print(c.execute(\"SELECT count(*) FROM dag_run WHERE state IN ('queued','running')\").fetchone()[0])"
            end=time.monotonic()+1800
            while int(call('exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',program)):
                if time.monotonic()>end:raise TimeoutError('Active DAG did not finish; no backup started')
                time.sleep(2)
        raw=call('run','--rm','--no-deps','-T','jobs','hmrec','portability','backup','/artifacts/backups/'+name,'--registry','/artifacts/serving')
        result=json.loads(raw);path=folder/name
        from restore import checksum_file
        if checksum_file(path)!=result['archive_sha256']:raise ValueError('Backup transfer checksum differs')
        print(json.dumps(dict(result,archive=str(path))))
        return path
    finally:
        # Restore only states actually observed; never unpause a previously paused DAG.
        for dag,paused in original.items():
            if not paused:airflow('dags','unpause',dag)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.parse_args(argv)
    create(Path(__file__).resolve().parents[1])
    return 0
if __name__=='__main__':raise SystemExit(main())
