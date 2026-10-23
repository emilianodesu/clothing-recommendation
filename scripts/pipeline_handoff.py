"""Strict aggregate-only transport for the one-shot T6 acceptance attempt."""
import math
import hashlib
import json
import re

PROTOCOL='pipeline-v1'
CHECKS=('build_completed','scheduled_refresh','restart_pending','idempotent','base_unchanged','postgres_preserved','dags_paused')
SERVICES={'api':(4*2**30,2_000_000_000),'ui':(512*2**20,500_000_000),'postgres':(512*2**20,0),
          'airflow-scheduler':(5*2**30,2_000_000_000),'airflow-api':(768*2**20,500_000_000),'airflow-dag-processor':(512*2**20,500_000_000)}


def require(value):
    if not value:raise ValueError('Invalid or incomplete aggregate pipeline report')


def empty_result(protocol=PROTOCOL):
    result=dict(stage='T6',protocol=protocol,status='failed',dataset='synthetic',source_run_id=None,source_archive_sha256=None,
        checks={k:False for k in CHECKS},duration_seconds=0.,freshness_seconds=[],processed_events=0,scheduled_runs=0,
        resources=[],build=None,installed_code={},dag_tree_sha256=None,error_type=None,training_performed=False,test_evaluated=False,t6_closed=False)

    if protocol=='pipeline-v2':result['postgres_transition']=None
    return result


def validate(report,result,environment):
    require(result['protocol'] in (PROTOCOL,'pipeline-v2'))
    require(set(result)==set(empty_result(result['protocol'])))
    if result.get('postgres_transition') is not None:
        from pipeline_recovery import validate_proof
        validate_proof(result['postgres_transition'])
        require(result['dataset']=='hm' and result['source_run_id']==result['postgres_transition']['source_run_id'] and result['source_archive_sha256']==result['postgres_transition']['source_archive_sha256'])
    require(result['stage']=='T6' and result['status']==report['status'])
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
    fields={'service','phase','container_id','started_at','image_id','memory_bytes','nano_cpus','peak_bytes','swap_peak_bytes','oom','oom_kill','max_events','restart_count','cgroup_inode'}
    require(isinstance(result['resources'],list) and len(result['resources'])<=24)
    for s in result['resources']:
        require(set(s)==fields and s['service'] in SERVICES and s['phase'] in ('before','pre_restart','post_restart','after'))
        require(re.fullmatch('[a-f0-9]{64}',s['container_id']) and re.fullmatch('sha256:[a-f0-9]{64}',s['image_id']))
        require(re.fullmatch('[0-9TZ:.+-]{20,40}',s['started_at']))
        for k in fields-{'service','phase','container_id','started_at','image_id'}:require(type(s[k]) is int and s[k]>=0)
    if result.get('postgres_transition') is not None:
        for sample in result['resources']:
            if sample['service']=='postgres':
                require(all(sample[k]==v for k,v in result['postgres_transition']['current_identity'].items()))
    validate_build_shape(result['build'])
    require(result['dag_tree_sha256'] is None or re.fullmatch('[a-f0-9]{64}',result['dag_tree_sha256']))
    require(type(result['installed_code']) is dict and set(result['installed_code'])<=set(AIRFLOW))
    for code in result['installed_code'].values():
        require(set(code)=={'source_tree_sha256','dag_tree_sha256'} and all(re.fullmatch('[a-f0-9]{64}',v) for v in code.values()))
    if report['status']=='passed':
        require(report['source_clean'] is True and result['error_type'] is None and all(result['checks'].values()))
        require(result['dataset']!='hm' or result['source_run_id'] is not None and result['source_archive_sha256'] is not None)
        require(len(result['freshness_seconds'])==2 and max(result['freshness_seconds'])<600 and result['processed_events']==2 and result['scheduled_runs']>=2)
        validate_resources(result['resources'])
        if result['protocol']=='pipeline-v2' and result['dataset']=='hm':
            from pipeline_recovery import APPROVED, IDENTITY
            if result['source_run_id']==APPROVED['source_run_id']:
                pg=next(s for s in result['resources'] if s['service']=='postgres' and s['phase']=='before')
                if any(pg[k]!=APPROVED['previous_identity'][k] for k in IDENTITY):
                    require(result['postgres_transition'] is not None)

        validate_code(result['installed_code'],environment['source_tree_sha256'],result['dag_tree_sha256'])
        require(result['build'] is not None)
        validate_build(result['build'],report['source_commit'],result['dataset']=='hm')


AIRFLOW=('airflow-scheduler','airflow-api','airflow-dag-processor')
PHASES=('before','pre_restart','post_restart','after')
FROZEN_SOURCE='e4695ac063fd621a29ba83523aa97be4041a91c45fba331162784f55771fbe9c'
FROZEN_DATASET='e04454c8ee54078fb495c4a0765d102b57d4438c3cd2839849b5603d303311a7'
FROZEN_SELECTION='e2da33963ca1c19b8315d043ff7c0d3ca184d33b8106f5f64f0c11a35b9b6aca'


def validate_code(installed,source,dag):
    require(set(installed)==set(AIRFLOW))
    require(all(c==dict(source_tree_sha256=source,dag_tree_sha256=dag) for c in installed.values()))


def validate_resources(samples):
    require(len(samples)==24 and {(s['service'],s['phase']) for s in samples}=={(n,p) for n in SERVICES for p in PHASES})
    for s in samples:
        require((s['memory_bytes'],s['nano_cpus'])==SERVICES[s['service']] and s['swap_peak_bytes']==s['oom']==s['oom_kill']==0)
    for name in SERVICES:
        observations=[next(s for s in samples if s['service']==name and s['phase']==p) for p in PHASES]
        require(all(s['container_id']==observations[0]['container_id'] and s['image_id']==observations[0]['image_id'] for s in observations))
        def generation(s):return tuple(s[k] for k in ('started_at','restart_count','cgroup_inode'))
        for a,b in zip(observations,observations[1:]):
            if name=='airflow-scheduler' and a['phase']=='pre_restart':
                require(a['started_at']!=b['started_at'] and generation(a)!=generation(b))
            else:
                require(generation(a)==generation(b))
                require(all(b[k]>=a[k] for k in ('peak_bytes','max_events','oom','oom_kill','swap_peak_bytes')))


def validate_build_shape(b):
    if b is None:return
    hashes=('config_sha256','manifest_sha256','source_sha256','dataset_manifest_sha256','selection_archive_sha256')
    require(set(b)==set(hashes)|{'run_id','started_at','finished_at','release_id','commit','data_as_of','synthetic','activate','selection_archive_verified'})
    for k in hashes:require(re.fullmatch('[a-f0-9]{64}',b[k]))
    require(re.fullmatch('[a-f0-9]{40}',b['commit']) and re.fullmatch('h-[a-f0-9]{64}',b['release_id']))
    require(re.fullmatch('[a-zA-Z0-9_.:+-]{1,250}',b['run_id']))
    for k in ('started_at','finished_at'):require(re.fullmatch('[0-9TZ :.+=-]{20,40}',b[k]))
    require(re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}',b['data_as_of']))
    for k in ('synthetic','activate','selection_archive_verified'):require(type(b[k]) is bool)


def validate_build(b,commit,hm):
    validate_build_shape(b)
    require(b['commit']==commit and b['data_as_of']=='2020-09-09')
    require(b['synthetic'] is (not hm) and b['activate'] is (not hm))
    require(b['selection_archive_sha256']==FROZEN_SELECTION)
    from datetime import datetime
    require(datetime.fromisoformat(b['finished_at'])>=datetime.fromisoformat(b['started_at']))
    if hm:require(b['selection_archive_verified'] and b['source_sha256']==FROZEN_SOURCE and b['dataset_manifest_sha256']==FROZEN_DATASET)


def build_proof(config_bytes,receipt,manifest,manifest_sha,run,commit,hm,frozen):
    cfg=json.loads(config_bytes)
    require(len(run)==4 and run[1]=='success' and run[0]==receipt.get('orchestrator_run_id'))
    require(receipt['config_sha256']==hashlib.sha256(config_bytes).hexdigest() and receipt['manifest_sha256']==manifest_sha)
    require(receipt['release_id']==manifest['release_id'] and cfg['data_as_of']==manifest['data_as_of'])
    b={k:manifest[k] for k in ('release_id','commit','data_as_of','source_sha256','dataset_manifest_sha256','selection_archive_sha256','selection_archive_verified')}
    b.update(run_id=run[0],started_at=run[2],finished_at=run[3],config_sha256=receipt['config_sha256'],manifest_sha256=manifest_sha,synthetic=cfg.get('synthetic',False),activate=cfg.get('activate',False))
    require(frozen['source_sha256']==FROZEN_SOURCE and frozen['dataset_manifest_sha256']==FROZEN_DATASET and frozen['selection']['archive_sha256']==FROZEN_SELECTION)
    validate_build(b,commit,hm)
    return b
