"""Aggregate benchmark envelope: reject customer payloads and incomplete success."""
import math
import re
from events_memory import validate_snapshot

GROUPS={'history','known_fallback','unknown'}
METRICS={'scheduled','completed','successful','omitted','errors','error_fraction','success_rps','completed_rps','latency_seconds','service_seconds'}

def require(value):
    if not value:raise ValueError('Invalid benchmark evidence')


def metrics(v):
    require(set(v)==METRICS)
    for k in ('scheduled','completed','successful','omitted','errors'):require(type(v[k]) is int and v[k]>=0)
    require(v['completed']+v['omitted']==v['scheduled'] and v['successful']+v['errors']==v['completed'])
    for k in ('error_fraction','success_rps','completed_rps'):require(type(v[k]) in (int,float) and math.isfinite(v[k]) and v[k]>=0)
    require(0<=v['error_fraction']<=1)
    require(abs(v['error_fraction']-((v['scheduled']-v['successful'])/v['scheduled'] if v['scheduled'] else 1.))<1e-12)
    for k in ('latency_seconds','service_seconds'):
        require(set(v[k])=={'p50','p95','p99'})
        require(all(x is None or (type(x) in (int,float) and math.isfinite(x) and x>=0) for x in v[k].values()))


def phase(v):
    extra={'offered_rps','window_seconds','elapsed_seconds','clock_discrepancy_seconds','groups'}
    require(set(v)==METRICS|extra)
    metrics({k:v[k] for k in METRICS})
    for k in extra-{'groups'}:require(type(v[k]) in (int,float) and math.isfinite(v[k]) and v[k]>=0)
    require(set(v['groups'])==GROUPS)
    for g in v['groups'].values():metrics(g)
    for key in ('scheduled','completed','successful','omitted','errors'):
        require(sum(g[key] for g in v['groups'].values())==v[key])


def validate(report,r,env,resources):
    require(set(r)=={'protocol','status','warmup','measurement','first_contact','resources_ok','source_preserved','error_type','test_evaluated','training_performed','t8_closed','generator_cpu_seconds','generator_max_rss_bytes','population'})
    require(r['protocol']=='load-baseline-v1' and r['status']==report['status'])
    for k in ('test_evaluated','training_performed','t8_closed'):require(r[k] is False)
    for k in ('resources_ok','source_preserved'):require(type(r[k]) is bool)
    require(r['error_type'] is None or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',r['error_type']))
    for k in ('warmup','measurement'):
        if r[k] is not None:phase(r[k])
    if r['first_contact'] is not None:metrics(r['first_contact'])
    require(set(r['population']) in (set(),{'customers','catalog','history_customers'}))
    require(all(type(n) is int and n>=0 for n in r['population'].values()))
    for k in ('generator_cpu_seconds','generator_max_rss_bytes'):require(type(r[k]) in (int,float) and math.isfinite(r[k]) and r[k]>=0)
    require(set(env)=={'source_commit','runner_tree_sha256','config_sha256','cases_sha256','release_id','source_state_sha256','source_tree_sha256'})
    require(env['source_commit']==report['source_commit'] and re.fullmatch('[a-f0-9]{40}',env['source_commit']))
    for k in ('runner_tree_sha256','config_sha256','source_tree_sha256'):require(re.fullmatch('[a-f0-9]{64}',env[k]))
    for k in ('cases_sha256','source_state_sha256'):require(env[k] is None or re.fullmatch('[a-f0-9]{64}',env[k]))
    require(re.fullmatch('h-[a-f0-9]{64}',env['release_id']))
    require(type(resources) is list and len(resources)<=1000)
    for row in resources:
        require(set(row)=={'memory','cpu_usage_usec'} and type(row['cpu_usage_usec']) is int and row['cpu_usage_usec']>=0)
        validate_snapshot(row['memory'],services=('api','postgres','ui'))
    if report['status']=='passed':
        require(r['warmup'] and r['measurement'] and r['first_contact'] and r['resources_ok'] and r['source_preserved'] and r['error_type'] is None)
        require(r['first_contact']['scheduled']==r['first_contact']['successful']==96 and r['first_contact']['omitted']==0)
        for key,seconds,n in [('warmup',120,2400),('measurement',600,12000)]:
            v=r[key];require(v['offered_rps']==20 and v['window_seconds']==seconds and v['scheduled']==n and v['clock_discrepancy_seconds']<=2)
        v=r['measurement'];require(v['latency_seconds']['p95'] is not None and v['latency_seconds']['p95']<.2 and v['error_fraction']<.01)
        from benchmark_resources import accepted
        require(accepted(resources))
