"""Strict aggregate capacity report; admission is not a performance measurement."""
import re

GIB=2**30
SERVICES={'api','ui','postgres','mlflow','airflow-scheduler','airflow-api','airflow-dag-processor','other'}
POLICY=dict(job_memory_bytes=4*GIB,job_nano_cpus=10**9,job_threads=2,vm_reserve_bytes=GIB)


def require(ok):
    if not ok:raise ValueError('Invalid capacity evidence')


def decision(engine,containers):
    total=sum(c['memory_bytes'] for c in containers)
    missing=any(c['memory_bytes']==0 or c['service']=='other' for c in containers)
    needed=total+POLICY['job_memory_bytes']+POLICY['vm_reserve_bytes']
    return dict(allocated_limits_bytes=total,required_with_job_bytes=needed,
        headroom_after_job_bytes=engine['memory_bytes']-needed,
        unlimited_memory_containers=sum(c['memory_bytes']==0 for c in containers),
        known_cpu_limits_nanos=sum(c['nano_cpus'] for c in containers),
        unlimited_cpu_containers=sum(c['nano_cpus']==0 for c in containers),
        admission='inspect_unknown_limits' if missing else 'fits_memory_budget' if needed<=engine['memory_bytes'] else 'sequential_policy_required')


def validate(report,r,env):
    require(set(r)=={'protocol','status','engine','containers','policy','decision','baseline_run_id','load_started','test_evaluated'})
    require(r['protocol']=='capacity-v1' and r['status']==report['status']=='passed')
    require(r['load_started'] is False and r['test_evaluated'] is False)
    require(r['policy']==POLICY)
    require(set(r['engine'])=={'cpus','memory_bytes'})
    require(all(type(n)is int and n>0 for n in r['engine'].values()))
    require(r['baseline_run_id']=='benchmark-mac24-20261023T213836Z-a7a170b5')
    require(type(r['containers'])is list and 0<len(r['containers'])<=100)
    ids=set()
    for c in r['containers']:
        require(set(c)=={'container_id','project_sha256','scope','service','memory_bytes','nano_cpus','image_id'})
        for key in ('container_id','project_sha256'):require(re.fullmatch('[a-f0-9]{64}',c[key]))
        require(c['container_id'] not in ids);ids.add(c['container_id'])
        require(c['scope'] in ('original','other') and c['service'] in SERVICES)
        require(re.fullmatch('sha256:[a-f0-9]{64}',c['image_id']))
        require(all(type(c[k])is int and c[k]>=0 for k in ('memory_bytes','nano_cpus')))
    require(r['decision']==decision(r['engine'],r['containers']))
    require(set(env)=={'source_commit','runner_tree_sha256','baseline_archive_sha256'})
    require(env['source_commit']==report['source_commit'] and re.fullmatch('[a-f0-9]{40}',env['source_commit']))
    for key in ('runner_tree_sha256','baseline_archive_sha256'):require(re.fullmatch('[a-f0-9]{64}',env[key]))
