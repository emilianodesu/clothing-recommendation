"""Strict aggregate-only T7 report; private backup archives never enter this channel."""
import math
import re

CHECKS=('source_preserved','source_parity','restored_parity','offline','fresh_credentials')

def empty_result():
    return dict(stage='T7',protocol='portability-v1',status='failed',checks={k:False for k in CHECKS},
        duration_seconds=0.,archive_bytes=0,archive_sha256=None,parity_queries=0,offline_queries=0,
        source_state_sha256=None,source_run_id=None,source_archive_sha256=None,
        source_postgres=None,destination_postgres=None,images={},error_type=None,training_performed=False,test_evaluated=False,t7_closed=False)


def validate(report,r,env):
    def require(x):
        if not x:raise ValueError('Invalid aggregate portability report')
    require(set(r)==set(empty_result()) and r['stage']=='T7' and r['protocol']=='portability-v1' and r['status']==report['status'])
    require(set(env)=={'source_commit','source_tree_sha256','runner_tree_sha256'} and env['source_commit']==report['source_commit'])
    require(re.fullmatch('[a-f0-9]{40}',env['source_commit']))
    for k in ('source_tree_sha256','runner_tree_sha256'):require(re.fullmatch('[a-f0-9]{64}',env[k]))
    require(set(r['checks'])==set(CHECKS) and all(type(x) is bool for x in r['checks'].values()))
    for k in ('training_performed','test_evaluated','t7_closed'):require(r[k] is False)
    require(type(r['duration_seconds']) in (float,int) and math.isfinite(r['duration_seconds']) and r['duration_seconds']>=0)
    for k in ('archive_bytes','parity_queries','offline_queries'):require(type(r[k]) is int and r[k]>=0)
    for k in ('archive_sha256','source_state_sha256','source_archive_sha256'):require(r[k] is None or re.fullmatch('[a-f0-9]{64}',r[k]))
    require(r['source_run_id'] is None or re.fullmatch('pipeline-[A-Za-z0-9_-]{1,100}',r['source_run_id']))
    require((r['source_run_id'] is None)==(r['source_archive_sha256'] is None))
    for k in ('source_postgres','destination_postgres'):
        p=r[k]
        if p is not None:
            require(set(p)=={'container_id','started_at','restart_count','image_id'})
            require(re.fullmatch('[a-f0-9]{64}',p['container_id']) and re.fullmatch('sha256:[a-f0-9]{64}',p['image_id']))
            require(re.fullmatch('[0-9TZ:.+-]{20,40}',p['started_at']) and type(p['restart_count']) is int and p['restart_count']>=0)
    require(type(r['images']) is dict and set(r['images'])<={'hmrec-jobs:0.1.0','hmrec-app:0.1.0'})
    require(all(re.fullmatch('sha256:[a-f0-9]{64}',v) for v in r['images'].values()))
    require(r['error_type'] is None or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',r['error_type']))
    if report['status']=='passed':
        if report.get('machine_id')=='mac24':require(r['source_run_id'] and r['source_archive_sha256'])
        require(report['source_clean'] is True and all(r['checks'].values()) and r['error_type'] is None)
        require(0<r['archive_bytes']<=20*2**30 and r['archive_sha256'] and r['source_state_sha256'])
        require(r['parity_queries']==r['offline_queries']==6 and len(r['images'])==2)
        require(r['source_postgres'] and r['destination_postgres'] and r['source_postgres']['container_id']!=r['destination_postgres']['container_id'])
