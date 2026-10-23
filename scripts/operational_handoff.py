"""Independent operational T5 acceptance. Never rewrite historical resource readings."""
import copy
import json
import re
from datetime import datetime,timezone
from events_clock import require,number,utc
from events_memory import diagnostics,as_resource
from events_memory_handoff import LIMITS,validate_consistency
from operational_contract import validate_functional
from operational_timing import validate_temporal

PROTOCOL='events-operational-v1'
BASE={'stage','protocol','status','source_tree_sha256','source','stream_id','mode','checks','seed','resources','host',
      'restart_persistent','source_unchanged','test_evaluated','training_performed','load_benchmark','duration_seconds',
      'diagnostics','temporal','t5_closed','cleanup'}
PHASES={'preflight','demo-package','seed','host-publication','replay','restart','final-validation','cleanup',
        'memory-before_seed','memory-after_seed','memory-after_replay','memory-after_restart'}
NAMES={'manifest.json','result.json','environment.json','commit.txt','job.log'}


class OperationalFailure(ValueError):
    def __init__(self,component,criterion):
        self.failure=dict(component=component,criterion=criterion)
        super().__init__('Operational acceptance failed')


def gate(ok,component,criterion):
    if not ok:raise OperationalFailure(component,criterion)


def final_validation(report,result,env):
    validate_temporal(result['temporal']);validate_consistency(result)
    gate(result['cleanup']['attempted'] and result['cleanup']['exit_code']==0 and result['cleanup']['error_type'] is None,'cleanup','seed_removed')
    gate(result['temporal']['complete'],'temporal','continuity')
    gate(result['diagnostics']['complete'],'diagnostics','complete')
    for name,r in result['resources'].items():
        gate((r['memory_bytes'],r['nano_cpus'])==LIMITS[name],name,'effective_limits')
        gate(not r['oom_killed'],name,'oom_zero');gate(r['swap_peak_bytes']==0,name,'swap_zero')
    for s in result['diagnostics']['snapshots']:
        name=s['service']
        gate((s['memory_bytes'],s['nano_cpus'])==LIMITS[name] and s['cgroup_limit_bytes']==s['memory_bytes'],name,'effective_limits')
        gate(not s['oom_killed'] and not any(s['events'][k] for k in ('oom','oom_kill','oom_group_kill')),name,'oom_zero')
        gate(not s['swap_current_bytes'] and not s['swap_peak_bytes'],name,'swap_zero')
    for interval in result['diagnostics']['intervals']:
        gate('error' not in interval,interval['service'],'consistent_counters')
        restart=interval['service']=='api' and interval['to_phase']=='after_restart'
        gate(interval['comparable']!=restart,interval['service'],'expected_generation')
    api=[s for s in result['diagnostics']['snapshots'] if s['service']=='api']
    before,after=api[-2:]
    stable=('container_id','image_id','restart_count','memory_bytes','memory_swap_bytes',
            'nano_cpus','cgroup_limit_bytes','cgroup_swap_limit_bytes')
    gate(all(before[k]==after[k] for k in stable) and utc(after['started_at'])>utc(before['started_at']),
         'api','intentional_restart')
    temporal=result['temporal'];ops=temporal['operations'];marks=temporal['marks']
    labels=[o['label'] for o in ops]
    gate(labels.count('seed')==labels.count('restart')==1,'operations','phase_order')
    expected=['sample_'+p+'_'+s for p in ('before_seed','after_seed','after_replay','after_restart') for s in ('api','ui','postgres')]
    gate([x for x in labels if x.startswith('sample_')]==expected,'operations','phase_order')
    gate(labels.index(expected[2])<labels.index('seed')<labels.index(expected[3]) and
         labels.index(expected[8])<labels.index('restart')<labels.index(expected[9]),'operations','phase_order')
    for label in ('health_live','health_ready','http_customers','http_catalog','http_recommend','http_profile','http_events','http_refresh','ui_health'):
        gate(any(o['label']==label and o['status']=='passed' for o in ops),'operations','required_operation')
    start=marks[0]
    for s in result['diagnostics']['snapshots']:
        op=next(o for o in ops if o['label']=='sample_'+s['phase']+'_'+s['service'])
        a,b=marks[op['begin']],marks[op['end']]
        gate(a['monotonic_seconds']-start['monotonic_seconds']-1e-3<=s['elapsed_seconds']<=b['monotonic_seconds']-start['monotonic_seconds']+1e-3,'operations','capture_timing')
        stamp=utc(s['captured_at']).timestamp()
        gate(a['utc_seconds']-1e-3<=stamp<=b['utc_seconds']+1e-3,'operations','capture_timing')
    gate(abs(result['duration_seconds']-temporal['assessment']['monotonic_seconds'])<=1e-6,'temporal','duration')
    try:validate_functional(report,result,env,NAMES)
    except ValueError:raise OperationalFailure('functional','contract') from None
    gate(result['t5_closed'] is False,'functional','contract')


def validate_operational(report,result,env,names):
    try:
        require(set(names) in (NAMES,NAMES|{'summary.md'}))
        require(set(report)=={'schema_version','kind','run_id','machine_id','context','source_commit','source_clean',
                'started_at','finished_at','status','warnings','steps','result','files','package_id'})
        require(type(report['schema_version']) is int and report['schema_version']==1 and report['kind']=='events-handoff')
        for field in ('started_at','finished_at'):
            value=report[field]
            require(isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})',value))
            utc(value) # Validate calendar/offset without erasing genuine regressions.
        require(report['result']==result and result['stage']=='T5' and result['protocol']==PROTOCOL and report['status']==result['status'])
        require(set(result)==(BASE if result['status']=='passed' else BASE|{'phase','error_type','failure'}))
        require(set(env)=={'run_id','source_commit','source_tree_sha256','uv_lock_sha256','runner_tree_sha256'})
        require(report['run_id']==env['run_id'] and report['source_commit']==env['source_commit'])
        require(re.fullmatch('[a-z][a-z0-9_-]{0,31}',report['machine_id']))
        require(re.fullmatch('events-'+re.escape(report['machine_id'])+r'-\d{8}T\d{6}Z-[a-f0-9]{8}',report['run_id']))
        for key in ('source_tree_sha256','uv_lock_sha256','runner_tree_sha256'):
            require((env[key] is None and result['status']!='passed') or re.fullmatch('[a-f0-9]{64}',env[key]))
        require(result['source_tree_sha256']==env['source_tree_sha256'])
        for k in ('test_evaluated','training_performed','load_benchmark','t5_closed'):require(result[k] is False)
        for k in ('restart_persistent','source_unchanged'):require(type(result[k]) is bool)
        require(number(result['duration_seconds']))
        require(report['warnings'] in ([],['seed_cleanup_failed'],['guard_release_failed'],['seed_cleanup_failed','guard_release_failed']))
        expected=[dict(name='events-operational',log='job.log',status=result['status'],exit_code={'passed':0,'failed':1,'interrupted':130}[result['status']])]
        require(json.dumps(report['steps'],sort_keys=True)==json.dumps(expected,sort_keys=True))
        for f in report['files']:require(set(f)=={'path','bytes','sha256'})
        validate_temporal(result['temporal']);validate_consistency(result)
        validate_partial(result)
        if result['status']=='passed':
            require(not report['warnings']);final_validation(report,result,env)
        else:
            require(result['phase'] in PHASES and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',result['error_type']))
            require(set(result['failure'])=={'component','criterion'})
            if result['error_type']=='OperationalFailure':
                require(result['phase']=='final-validation')
                probe=copy.deepcopy(result)
                for k in ('phase','error_type','failure'):probe.pop(k)
                probe['status']='passed'
                try:final_validation(dict(report,result=probe,status='passed'),probe,env)
                except OperationalFailure as error:require(error.failure==result['failure'])
                else:raise ValueError('Unsupported operational failure')
            else:require(result['failure']=={'component':'execution','criterion':'exception'})
    except (KeyError,TypeError,AttributeError,StopIteration) as error:
        raise ValueError('Malformed operational T5 evidence') from error


def validate_partial(r):
    """Closed failures allow missing stages, not arbitrary private payloads."""
    from uuid import UUID
    from datetime import date
    require(str(UUID(r['stream_id']))==r['stream_id'] and r['mode'] in (None,'simulation','synthetic'))
    if r['source'] is not None:
        s=r['source'];require(set(s)=={'run_id','archive_sha256','release_id','manifest_sha256','dataset'})
        require(re.fullmatch('h-[a-f0-9]{64}',s['release_id']) and re.fullmatch('[a-f0-9]{64}',s['manifest_sha256']))
        require(s['dataset'] in ('hm','synthetic'))
        require((s['run_id'] is None and s['archive_sha256'] is None and s['dataset']=='synthetic') or
                (re.fullmatch('release-[A-Za-z0-9_-]{1,100}',s['run_id']) and re.fullmatch('[a-f0-9]{64}',s['archive_sha256'])))
    c=r['cleanup'];require(set(c)=={'attempted','exit_code','error_type','utc_seconds','monotonic_seconds'})
    require(type(c['attempted']) is bool and (c['exit_code'] is None or type(c['exit_code']) is int))
    require(c['error_type'] is None or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',c['error_type']))
    import math
    require(all(type(c[k]) in (int,float) and math.isfinite(c[k]) for k in ('utc_seconds','monotonic_seconds')))
    checks=r['checks'];require(isinstance(checks,dict))
    if checks:
        booleans={'purchase_changes_profile','views_clicks_not_purchases','late_event_order','refresh_idempotent','base_unchanged'}
        counts={'initial_parity_requests','new_events','duplicate_status','conflict_status','revision','processed_events'}
        require(set(checks)==booleans|counts|{'profile_sha256','data_as_of'})
        require(all(type(checks[k]) is bool for k in booleans))
        require(all(type(checks[k]) is int and checks[k]>=0 for k in counts))
        require(re.fullmatch('[a-f0-9]{64}',checks['profile_sha256']));date.fromisoformat(checks['data_as_of'])
    seed=r['seed'];require(isinstance(seed,dict))
    if seed:
        require(set(seed)=={'daily_rows','purchases','first_day','last_day','data_as_of'})
        require(all(type(seed[k]) is int and seed[k]>=0 for k in ('daily_rows','purchases')))
        for k in ('first_day','last_day','data_as_of'):date.fromisoformat(seed[k])
    host=r['host'];require(isinstance(host,dict))
    if host:
        require(set(host)=={'host','api_port','ui_port','bindings_verified','ui_healthy'} and host['host']=='127.0.0.1')
        require(all(type(host[k]) is bool for k in ('bindings_verified','ui_healthy')))
        require(all(type(host[k]) is int and 1024<=host[k]<=65535 for k in ('api_port','ui_port')))


def validate_operational_files(report,bundle):
    from handoff import summary
    for name,text in {'job.log':'operational T5\nstatus: '+report['status']+'\n',
                      'commit.txt':(report['source_commit'] or 'unavailable')+'\n','summary.md':summary(report)}.items():
        require(bundle.read(name)==text.encode())
