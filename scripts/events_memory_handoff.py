"""Opt-in T5 diagnostics; preserve events-v1 acceptance without rewriting history."""
import copy
import json
import re
from events_memory import validate_diagnostics, require, as_resource, count
from events_handoff import validate_events
from quality_handoff import no_private_rows

PROTOCOL='events-memory-v1'
NAMES={'manifest.json','result.json','environment.json','commit.txt','job.log'}
LIMITS={'api':(4*2**30,2*10**9),'ui':(2**29,500000000),'postgres':(2**29,0),'seed':(3*2**30,10**9)}


class FinalValidationError(ValueError):
    def __init__(self,component,criterion,observed,expected):
        self.failure=dict(component=component,criterion=criterion,observed=observed,expected=expected)
        super().__init__('T5 final validation failed: '+component+'/'+criterion)


def validate_consistency(result):
    """Evidence coherence is required even when its measured criteria fail."""
    validate_diagnostics(result['diagnostics'])
    resources=result.get('resources',{})
    require(isinstance(resources,dict) and set(resources)<=set(LIMITS))
    selected={s['service']:s for s in result['diagnostics']['snapshots'] if
              (s['service']=='api' and s['phase']=='after_replay') or (s['service']!='api' and s['phase']=='after_restart')}
    for name,r in resources.items():
        require(isinstance(r,dict) and set(r)=={'image_id','memory_bytes','nano_cpus','cgroup_peak_bytes','swap_peak_bytes','oom_killed'})
        require(isinstance(r['image_id'],str) and re.fullmatch('sha256:[a-f0-9]{64}',r['image_id']))
        require(all(count(r[k]) for k in ('memory_bytes','nano_cpus','cgroup_peak_bytes','swap_peak_bytes')))
        require(type(r['oom_killed']) is bool)
        if name!='seed':require(name in selected and r==as_resource(selected[name]))


def final_validation(report,result,env):
    """Keep original gates; provide a safe, explicit resource failure before v1 validation."""
    validate_consistency(result)
    for name,r in result['resources'].items():
        if name not in LIMITS:raise FinalValidationError('report','resource_set',False,True)
        for field,want in zip(('memory_bytes','nano_cpus'),LIMITS[name]):
            if r[field]!=want:raise FinalValidationError(name,field,r[field],want)
        if r['oom_killed'] is not False:raise FinalValidationError(name,'oom_killed',r['oom_killed'],False)
        if r['swap_peak_bytes']!=0:raise FinalValidationError(name,'swap_peak_zero',r['swap_peak_bytes'],0)
        if r['cgroup_peak_bytes']>=r['memory_bytes']:
            raise FinalValidationError(name,'peak_below_limit',r['cgroup_peak_bytes'],r['memory_bytes'])
    d=result['diagnostics'];validate_diagnostics(d)
    if not d['complete']:raise FinalValidationError('diagnostics','complete',False,True)
    for s in d['snapshots']:
        name=s['service']
        if (s['memory_bytes'],s['nano_cpus'])!=LIMITS[name] or s['cgroup_limit_bytes']!=s['memory_bytes']:
            raise FinalValidationError(name,'effective_limits',False,True)
        if s['peak_bytes']>=s['memory_bytes']:
            raise FinalValidationError(name,'peak_below_limit',s['peak_bytes'],s['memory_bytes'])
        if s['oom_killed'] or any(s['events'][k] for k in ('oom','oom_kill','oom_group_kill')):
            raise FinalValidationError(name,'oom_counters_zero',False,True)
        if s['swap_current_bytes'] or s['swap_peak_bytes']:
            raise FinalValidationError(name,'swap_zero',False,True)
    for i in d['intervals']:
        if 'error' in i:raise FinalValidationError(i['service'],'consistent_counters',False,True)
        expected_restart=i['service']=='api' and i['to_phase']=='after_restart'
        if i['comparable']==expected_restart:
            raise FinalValidationError(i['service'],'expected_generation',False,True)
    original=copy.deepcopy(result);original.pop('diagnostics');original['protocol']='events-v1'
    envelope=dict(report,result=original)
    validate_events(envelope,original,env,NAMES)


def validate_memory_envelope(report,result,env,names):
    try:
        require(result['protocol']==PROTOCOL and report['result']==result and report['status']==result['status'])
        require(result['stage']=='T5' and {'environment.json','result.json','commit.txt','job.log'}<=set(names))
        require(report['source_commit']==env['source_commit'] and report['run_id']==env['run_id'])
        require(set(env)=={'run_id','source_commit','source_tree_sha256','uv_lock_sha256','runner_tree_sha256'})
        for key in ('source_tree_sha256','uv_lock_sha256','runner_tree_sha256'):
            require((env[key] is None and result['status']!='passed') or
                    (isinstance(env[key],str) and re.fullmatch('[a-f0-9]{64}',env[key])))
        no_private_rows(result);validate_consistency(result)
        if result['status']=='passed':final_validation(report,result,env)
        else:
            require(result['status'] in ('failed','interrupted'))
            require(result['phase'] in ('preflight','demo-package','seed','host-publication','replay','restart','final-validation',
                    'memory-before_seed','memory-after_seed','memory-after_replay','memory-after_restart'))
            require(isinstance(result['error_type'],str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',result['error_type']))
            require(set(result)<={'stage','protocol','status','source_tree_sha256','source','stream_id','mode','checks','seed',
                'resources','host','restart_persistent','source_unchanged','test_evaluated','training_performed','load_benchmark',
                'duration_seconds','diagnostics','phase','error_type','failure'})
            require(('failure' in result)==(result['error_type']=='FinalValidationError'))
            if 'failure' in result:
                f=result['failure'];require(result['phase']=='final-validation' and result['error_type']=='FinalValidationError')
                require(set(f)=={'component','criterion','observed','expected'})
                require(f['component'] in (*LIMITS,'report','diagnostics'))
                require(f['criterion'] in ('resource_set','memory_bytes','nano_cpus','oom_killed','swap_peak_zero','peak_below_limit',
                    'complete','effective_limits','oom_counters_zero','swap_zero','consistent_counters','expected_generation'))
                require(all(type(f[k]) in (bool,int) for k in ('observed','expected')))
                probe=copy.deepcopy(result)
                for key in ('phase','error_type','failure'):probe.pop(key)
                probe['status']='passed'
                try:final_validation(dict(report,result=probe,status='passed'),probe,env)
                except FinalValidationError as error:
                    require(json.dumps(f,sort_keys=True)==json.dumps(error.failure,sort_keys=True))
                else:raise ValueError('Reported T5 failure is not supported by observations')
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError('Malformed T5 memory report') from error
