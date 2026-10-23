"""Strict aggregate-only final evaluation envelope; passed is operational only."""
import math
import re
from quality_handoff import require,number,count,validate_methods,METRICS,SEGMENTS
from events_memory import validate_snapshot,timestamp

WINDOW=dict(fold_id='TEST',cutoff='2020-09-16',label_end='2020-09-23')


def empty_result():
    return dict(protocol='final-hp-v1',status='failed',evaluation=None,exit_code=None,duration_seconds=0.,clock_discrepancy_seconds=0.,error_type=None,
        resources_ok=False,source_preserved=False,auxiliaries_restored=False,transitions=[],test_started=False,training_performed=False,t8_closed=False)


def validate_evaluation(e):
    require(set(e)=={'protocol','selected_method','window','snapshot_counts','evaluations','metric_deltas_h_minus_p'})
    require(e['protocol']=='final-hp-v1' and e['selected_method']=='H' and e['window']==WINDOW)
    require(set(e['snapshot_counts'])=={'catalog','profiles','histories','popularity'} and all(count(n,1) for n in e['snapshot_counts'].values()))
    require(e['snapshot_counts']['catalog']==e['snapshot_counts']['popularity'])
    methods=e['evaluations'];require(set(methods)=={'H','P'})
    for name,v in methods.items():
        require(set(v)=={'schema_version','fold','method','population','metrics','segments','denominators','exposure','targets'})
        require(v['schema_version']==1 and v['fold']==WINDOW and v['method']==name)
        require(set(v['population'])=={'users','catalog_size'})
        require(set(v['denominators'])=={'relevant_items','out_of_catalog_relevant_items','mean_available_recall_ceiling'})
        for seg in v['segments'].values():require(set(seg)=={'users','metrics'})
        for ex in v['exposure'].values():require(set(ex)=={'k','distinct_items','coverage','unique_ordered_lists','unique_sets','top_exposure_share','effective_items'})
        for t in v['targets'].values():require(set(t)=={'users','relevant_items','metrics'})
    pop=methods['H']['population'];require(count(pop['users'],1) and pop['catalog_size']==e['snapshot_counts']['catalog'])
    validate_methods(methods,pop['users'],pop['catalog_size'])
    require(methods['H']['denominators']==methods['P']['denominators'])
    for s in SEGMENTS:
        require(methods['H']['segments'][s]['users']==methods['P']['segments'][s]['users'])
        if s!='recent28d':require(methods['H']['segments'][s]==methods['P']['segments'][s])
    delta=e['metric_deltas_h_minus_p'];require(set(delta)==METRICS)
    for m in METRICS:require(number(delta[m],-1,1) and math.isclose(delta[m],methods['H']['metrics'][m]-methods['P']['metrics'][m],abs_tol=1e-12))


def resources_ok(rows):
    if len(rows)<2:return False
    first,last=rows[0],rows[-1]
    for s in rows:
        validate_snapshot(s,services=('final',))
        if s['memory_bytes']!=5*2**30 or s['cgroup_limit_bytes']!=5*2**30 or s['nano_cpus']!=2*10**9:return False
        if s['memory_swap_bytes']!=5*2**30 or s['cgroup_swap_limit_bytes']!=0:return False
        if s['swap_current_bytes'] or s['swap_peak_bytes'] or s['oom_killed']:return False
        if any(s[k]!=first[k] for k in ('container_id','started_at','restart_count','cgroup_inode','image_id')):return False
    for a,b in zip(rows,rows[1:]):
        if not 0<b['elapsed_seconds']-a['elapsed_seconds']<=35:return False
        if any(b['events'][k]<a['events'][k] for k in a['events']):return False
    return all(first['events'][k]==last['events'][k] for k in ('oom','oom_kill','oom_group_kill'))


def validate(report,r,env,rows):
    require(set(r)==set(empty_result()) and r['protocol']=='final-hp-v1' and r['status']==report['status'])
    require(r['status'] in ('passed','failed'))
    for k in ('test_started','resources_ok','source_preserved','auxiliaries_restored'):require(type(r[k]) is bool)
    require(r['training_performed'] is False and r['t8_closed'] is False)
    require(r['exit_code'] is None or type(r['exit_code']) is int)
    require(number(r['duration_seconds'],0,100000) and number(r['clock_discrepancy_seconds'],0,100000))
    require(r['error_type'] is None or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',r['error_type']))
    if r['evaluation'] is not None:validate_evaluation(r['evaluation'])
    require(set(env)=={'source_commit','runner_tree_sha256','worker_source_sha256','image_sha256','dataset_manifest_sha256','source_sha256','selection_archive_sha256','baseline_archive_sha256','concurrency_archive_sha256','state_before_sha256','state_after_sha256'})
    require(env['source_commit']==report['source_commit'] and re.fullmatch('[a-f0-9]{40}',env['source_commit']))
    for k,v in env.items():
        if k!='source_commit':require(re.fullmatch('[a-f0-9]{64}',v))
    require(type(rows) is list and len(rows)<=3000)
    for row in rows:validate_snapshot(row,services=('final',))
    require(type(r['transitions']) is list and len(r['transitions'])<=6)
    for t in r['transitions']:
        require(set(t)=={'scope','service','container_id','image_id','running','started_at','restart_count','health','memory_bytes','nano_cpus'})
        require(t['scope'] in ('original','other') and t['service'] in ('postgres','api','mlflow','airflow-api','airflow-dag-processor','airflow-scheduler'))
        require(re.fullmatch('[a-f0-9]{64}',t['container_id']) and re.fullmatch('sha256:[a-f0-9]{64}',t['image_id']))
        timestamp(t['started_at']);require(t['running'] is True and t['health'] in ('healthy','none'))
        for k in ('restart_count','memory_bytes','nano_cpus'):require(count(t[k]))
    if r['status']=='passed':
        require(r['test_started'] and r['resources_ok'] and r['source_preserved'] and r['auxiliaries_restored'])
        require(r['error_type'] is None and r['exit_code']==0 and 0<r['duration_seconds']<=5400 and r['clock_discrepancy_seconds']<=2)
        require(r['evaluation'] is not None and resources_ok(rows))
        require(rows[-1]['elapsed_seconds']-rows[0]['elapsed_seconds']>=r['duration_seconds'])
        require(rows[0]['image_id']=='sha256:'+env['image_sha256'])
        require(env['state_before_sha256']==env['state_after_sha256']!='0'*64)
        require(len(r['transitions'])==6 and len({t['container_id'] for t in r['transitions']})==6)
        require({(t['scope'],t['service']) for t in r['transitions']}=={('other','api'),('other','postgres'),('original','mlflow'),('original','airflow-api'),('original','airflow-scheduler'),('original','airflow-dag-processor')})
