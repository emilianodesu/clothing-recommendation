"""Strict aggregate-only transport for the one-shot T6 acceptance attempt."""
import math
import re

PROTOCOL='pipeline-v1'
CHECKS=('build_completed','scheduled_refresh','restart_pending','idempotent','base_unchanged','postgres_preserved','dags_paused')
SERVICES={'api':(4*2**30,2_000_000_000),'ui':(512*2**20,500_000_000),'postgres':(512*2**20,0),
          'airflow-scheduler':(5*2**30,2_000_000_000),'airflow-api':(768*2**20,500_000_000),'airflow-dag-processor':(512*2**20,500_000_000)}


def require(value):
    if not value:raise ValueError('Invalid or incomplete aggregate pipeline report')


def empty_result():
    return dict(stage='T6',protocol=PROTOCOL,status='failed',dataset='synthetic',source_run_id=None,source_archive_sha256=None,
        checks={k:False for k in CHECKS},duration_seconds=0.,freshness_seconds=[],processed_events=0,scheduled_runs=0,
        resources=[],error_type=None,training_performed=False,test_evaluated=False,t6_closed=False)


def validate(report,result,environment):
    require(set(result)==set(empty_result()))
    require(result['stage']=='T6' and result['protocol']==PROTOCOL and result['status']==report['status'])
    require(report['run_id'].startswith('pipeline-') and re.fullmatch('[a-zA-Z0-9_-]{1,100}',report['run_id']))
    require(result['dataset'] in ('hm','synthetic'))
    require(result['source_run_id'] is None or re.fullmatch('events-[a-zA-Z0-9_-]{1,90}',result['source_run_id']))
    require(result['source_archive_sha256'] is None or re.fullmatch('[a-f0-9]{64}',result['source_archive_sha256']))
    require(set(environment)=={'source_commit','source_tree_sha256','runner_tree_sha256'})
    require(environment['source_commit']==report['source_commit'] and re.fullmatch('[a-f0-9]{40}',report['source_commit']))
    for k in ('source_tree_sha256','runner_tree_sha256'):require(re.fullmatch('[a-f0-9]{64}',environment[k]))
    require(set(result['checks'])==set(CHECKS) and all(type(v) is bool for v in result['checks'].values()))
    for k in ('training_performed','test_evaluated','t6_closed'):require(result[k] is False)
    require(result['error_type'] is None or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',result['error_type']))
    def number(v):return type(v) in (int,float) and math.isfinite(v) and v>=0
    require(number(result['duration_seconds']))
    require(isinstance(result['freshness_seconds'],list) and len(result['freshness_seconds'])<=2 and all(number(v) for v in result['freshness_seconds']))
    for k in ('processed_events','scheduled_runs'):require(type(result[k]) is int and result[k]>=0)
    fields={'service','phase','container_id','started_at','image_id','memory_bytes','nano_cpus','peak_bytes','swap_peak_bytes','oom','oom_kill','max_events'}
    require(isinstance(result['resources'],list) and len(result['resources'])<=12)
    for s in result['resources']:
        require(set(s)==fields and s['service'] in SERVICES and s['phase'] in ('before','after'))
        require(re.fullmatch('[a-f0-9]{64}',s['container_id']) and re.fullmatch('sha256:[a-f0-9]{64}',s['image_id']))
        require(re.fullmatch('[0-9TZ:.+-]{20,40}',s['started_at']))
        for k in fields-{'service','phase','container_id','started_at','image_id'}:require(type(s[k]) is int and s[k]>=0)
    if report['status']=='passed':
        require(report['source_clean'] is True and result['error_type'] is None and all(result['checks'].values()))
        require(result['dataset']!='hm' or result['source_run_id'] is not None and result['source_archive_sha256'] is not None)
        require(len(result['freshness_seconds'])==2 and max(result['freshness_seconds'])<600 and result['processed_events']==2 and result['scheduled_runs']>=2)
        require({(s['service'],s['phase']) for s in result['resources']}=={(n,p) for n in SERVICES for p in ('before','after')})
        for s in result['resources']:
            require((s['memory_bytes'],s['nano_cpus'])==SERVICES[s['service']] and s['swap_peak_bytes']==s['oom']==s['oom_kill']==0)
        pg=[s for s in result['resources'] if s['service']=='postgres']
        require(all(pg[0][k]==pg[1][k] for k in ('container_id','started_at','image_id')))
