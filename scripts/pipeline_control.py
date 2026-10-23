"""Explicit local Airflow lifecycle. Never starts or recreates PostgreSQL/API/UI."""
import argparse
from pathlib import Path
import subprocess
from bootstrap import read_configuration
from project import compose

SERVICES=('airflow-api','airflow-scheduler','airflow-dag-processor')
DAGS=('hm_build_release','hm_refresh_profiles')


def require_postgres(root,values):
    cid=compose(root,values,'ps','--status','running','-q','postgres',capture=True,timeout=30).strip()
    if not cid:raise ValueError('Existing PostgreSQL required; initialize infrastructure separately')
    return cid


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    init=sub.add_parser('init');init.add_argument('--skip-build',action='store_true')
    for name in ('up','status','pause'):sub.add_parser(name)
    trigger=sub.add_parser('trigger');trigger.add_argument('dag',choices=('hm_build_release',))
    enable=sub.add_parser('enable-refresh')
    register=sub.add_parser('register');register.add_argument('--stream-id',required=True)
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parents[1];values=read_configuration(root/'.env')
    values['HMREC_SOURCE_COMMIT']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    before=require_postgres(root,values)
    artifacts=Path(values['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else root/artifacts
    for name in ('airflow','pipeline/reports'):(artifacts/name).mkdir(parents=True,exist_ok=True)
    def airflow(*cmd):return compose(root,values,'run','--rm','--no-deps','-T','airflow-scheduler',*cmd)
    if args.action=='init':
        if not args.skip_build:
            compose(root,values,'build','jobs');compose(root,values,'build','airflow-scheduler')
        compose(root,values,'run','--rm','--no-deps','-T','jobs','hmrec','db','migrate')
        airflow('db','migrate');airflow('pools','set','default_pool','1','One local pipeline task')
    elif args.action=='up':compose(root,values,'up','-d','--no-deps','--wait','--wait-timeout','240',*SERVICES)
    elif args.action=='status':compose(root,values,'ps',*SERVICES)
    elif args.action=='pause':
        for dag in DAGS:airflow('dags','pause',dag)
    elif args.action=='trigger':
        airflow('dags','unpause',args.dag);airflow('dags','trigger',args.dag)
    elif args.action=='enable-refresh':airflow('dags','unpause','hm_refresh_profiles')
    elif args.action=='register':
        compose(root,values,'run','--rm','--no-deps','-T','jobs','hmrec','pipeline','register','--stream-id',args.stream_id)
    if require_postgres(root,values)!=before:raise ValueError('PostgreSQL identity changed; stop and inspect')
    return 0
