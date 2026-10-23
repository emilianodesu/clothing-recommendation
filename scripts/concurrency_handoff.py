"""Closed aggregate T8-B report, separate from historical baseline."""
import re
from benchmark import summarize
from benchmark_handoff import phase,metrics
from benchmark_resources import accepted
from events_memory import validate_snapshot

LIMITS={'api':(4*2**30,2*10**9),'postgres':(512*2**20,0),'ui':(512*2**20,500000000),'airflow-scheduler':(5*2**30,2*10**9)}


def require(ok):
    if not ok:raise ValueError('Invalid concurrency evidence')


def overlap(trace,cases,start,end):
    # Arrival-window cohort: count omitted arrivals even when no sample exists.
    due=[(trace['started']+i/trace['rate'],cases[i%len(cases)]['group']) for i in range(trace['scheduled'])]
    scheduled=[(t,g) for t,g in due if start<=t<end]
    samples=[s for s in trace['samples'] if start<=s['due']<end]
    result=summarize(samples,len(scheduled),end-start)
    result['groups']={g:summarize([s for s in samples if s['group']==g],sum(group==g for _,group in scheduled),end-start) for g in ('history','known_fallback','unknown')}
    return result


def validate(report,r,env,rows):
    require(set(r)=={'protocol','status','warmup','measurement','overlap','worker_seconds','build_seconds','build_release_id','build_exit_code','resources_ok','source_preserved','auxiliaries_restored','auxiliary_transitions','generator_cpu_seconds','generator_max_rss_bytes','error_type','training_performed','test_evaluated','t8_closed'})
    require(r['protocol']=='scheduler-concurrency-v1' and r['status']==report['status'])
    for k in ('training_performed','test_evaluated','t8_closed'):require(r[k] is False)
    for k in ('resources_ok','source_preserved','auxiliaries_restored'):require(type(r[k]) is bool)
    require(r['error_type'] is None or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',r['error_type']))
    for k in ('warmup','measurement'):
        if r[k] is not None:phase(r[k])
    if r['overlap'] is not None:
        v=r['overlap'];require(set(v)=={'groups','scheduled','completed','successful','omitted','errors','error_fraction','success_rps','completed_rps','latency_seconds','service_seconds'})
        metrics({k:x for k,x in v.items() if k!='groups'})
        require(set(v['groups'])=={'history','known_fallback','unknown'})
        for g in v['groups'].values():metrics(g)
        for k in ('scheduled','completed','successful','omitted','errors'):require(sum(g[k] for g in v['groups'].values())==v[k])
    for k in ('generator_cpu_seconds','generator_max_rss_bytes'):require(type(r[k]) in (int,float) and 0<=r[k]<10**15)
    require(type(r['auxiliary_transitions']) is list and len(r['auxiliary_transitions'])<=3)
    for transition in r['auxiliary_transitions']:
        require(set(transition)=={'service','before','after'} and transition['service'] in ('postgres','api','mlflow'))
        for side in ('before','after'):
            item=transition[side]
            require(set(item)=={'container_id','image_id','running','started_at','restart_count','health','memory_bytes','nano_cpus'})
            require(re.fullmatch('[a-f0-9]{64}',item['container_id']) and re.fullmatch('sha256:[a-f0-9]{64}',item['image_id']))
            from events_memory import timestamp
            timestamp(item['started_at'])
            require(item['running'] is True and item['health'] in ('healthy','none'))
            require(all(type(item[k]) is int and item[k]>=0 for k in ('restart_count','memory_bytes','nano_cpus')))
        require(all(transition['before'][k]==transition['after'][k] for k in ('container_id','image_id','memory_bytes','nano_cpus')))
    for k in ('worker_seconds','build_seconds'):require(type(r[k]) in (float,int) and 0<=r[k]<3600)
    require(r['build_exit_code'] is None or type(r['build_exit_code'])is int)
    require(r['build_release_id'] is None or re.fullmatch('h-[a-f0-9]{64}',r['build_release_id']))
    require(set(env)=={'source_commit','runner_tree_sha256','baseline_archive_sha256','capacity_archive_sha256','cases_sha256','api_code_sha256','scheduler_code_sha256','state_before_sha256','state_after_sha256'})
    require(env['source_commit']==report['source_commit'] and re.fullmatch('[a-f0-9]{40}',env['source_commit']))
    for k,v in env.items():
        if k!='source_commit':require(re.fullmatch('[a-f0-9]{64}',v))
    require(type(rows)is list and len(rows)<=500)
    for row in rows:
        require(set(row)=={'memory','cpu_usage_usec'} and type(row['cpu_usage_usec']) is int and row['cpu_usage_usec']>=0)
        validate_snapshot(row['memory'],services=tuple(LIMITS))
    if report['status']=='passed':
        require(all(r[k] for k in ('resources_ok','source_preserved','auxiliaries_restored')) and r['error_type'] is None)
        require(env['state_before_sha256']==env['state_after_sha256'])
        require(len(r['auxiliary_transitions'])==3 and {t['service'] for t in r['auxiliary_transitions']}=={'api','postgres','mlflow'})
        require(accepted(rows,LIMITS))
        require(r['build_exit_code']==0 and r['build_release_id'] is not None and 10<=r['worker_seconds']<580 and r['build_seconds']>=.8*r['worker_seconds'])
        for k,seconds,n in [('warmup',120,2400),('measurement',600,12000)]:
            v=r[k];require(v is not None and v['window_seconds']==seconds and v['scheduled']==n and v['offered_rps']==20 and v['clock_discrepancy_seconds']<=2)
        require(r['overlap'] is not None and r['overlap']['scheduled']>=200)
        for v in (r['measurement'],r['overlap']):require(v['latency_seconds']['p95'] is not None and v['latency_seconds']['p95']<.2 and v['error_fraction']<.01)
