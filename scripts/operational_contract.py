"""Frozen functional gates for operational-v1; raw lifetime peaks remain diagnostics."""
from datetime import date, timedelta
import re
from uuid import UUID
from quality_handoff import ALLOWED,require,no_private_rows,count,number
from serving_handoff import HASH,RELEASE


def validate_functional(report,result,env,names):
    try:
        require(set(names)<={'manifest.json',*ALLOWED} and {'environment.json','result.json','commit.txt','job.log'}<=set(names))
        require(report['result']==result and report['source_clean'] is True)
        require(report['source_commit']==env['source_commit'] and report['run_id']==env['run_id'])
        require(result['source_tree_sha256']==env['source_tree_sha256'] and HASH.fullmatch(env['source_tree_sha256']))
        require(HASH.fullmatch(env['uv_lock_sha256']))
        no_private_rows(result)
        require(set(result)=={'stage','protocol','status','source_tree_sha256','source','stream_id','mode','checks','seed',
            'resources','host','restart_persistent','source_unchanged','test_evaluated','training_performed','load_benchmark','duration_seconds','diagnostics','temporal','t5_closed','cleanup'})
        require(result['stage']=='T5' and result['protocol']=='events-operational-v1' and result['status']=='passed')
        require(str(UUID(result['stream_id']))==result['stream_id'])
        require(all(result[k] is True for k in ('restart_persistent','source_unchanged')))
        require(all(result[k] is False for k in ('test_evaluated','training_performed','load_benchmark')))
        source=result['source'];require(set(source)=={'run_id','archive_sha256','release_id','manifest_sha256','dataset'})
        require(RELEASE.fullmatch(source['release_id']) and HASH.fullmatch(source['manifest_sha256']))
        require((source['dataset'],result['mode']) in (('synthetic','synthetic'),('hm','simulation')))
        if source['run_id'] is None:require(source['dataset']=='synthetic' and source['archive_sha256'] is None)
        else:require(source['run_id'].startswith('release-') and HASH.fullmatch(source['archive_sha256']))
        checks=result['checks']
        require(set(checks)=={'initial_parity_requests','new_events','duplicate_status','conflict_status','purchase_changes_profile',
            'views_clicks_not_purchases','late_event_order','refresh_idempotent','base_unchanged','profile_sha256','revision','data_as_of','processed_events'})
        require(count(checks['initial_parity_requests'],2) and checks['initial_parity_requests']<=4)
        require(checks['new_events']==checks['processed_events']==4 and checks['revision']==3)
        require(checks['duplicate_status']==200 and checks['conflict_status']==409 and HASH.fullmatch(checks['profile_sha256']))
        require(all(checks[k] is True for k in ('purchase_changes_profile','views_clicks_not_purchases','late_event_order','refresh_idempotent','base_unchanged')))
        seed=result['seed'];require(set(seed)=={'daily_rows','purchases','first_day','last_day','data_as_of'})
        require(count(seed['daily_rows'],1) and count(seed['purchases'],seed['daily_rows']))
        cut=date.fromisoformat(seed['data_as_of']);require(checks['data_as_of']==seed['data_as_of'])
        require(cut-timedelta(days=28)<=date.fromisoformat(seed['first_day'])<=date.fromisoformat(seed['last_day'])<cut)
        limits={'api':(4*2**30,2*10**9),'ui':(2**29,500000000),'postgres':(2**29,0),'seed':(3*2**30,10**9)}
        require(set(result['resources'])==set(limits))
        for name,r in result['resources'].items():
            require(set(r)=={'image_id','memory_bytes','nano_cpus','cgroup_peak_bytes','swap_peak_bytes','oom_killed'})
            require(re.fullmatch(r'sha256:[a-f0-9]{64}',r['image_id']))
            require((r['memory_bytes'],r['nano_cpus'])==limits[name] and r['oom_killed'] is False and r['swap_peak_bytes']==0)
            require(count(r['cgroup_peak_bytes'],1)) # Lifetime peak is diagnostic in this version only.
        host=result['host'];require(set(host)=={'host','api_port','ui_port','bindings_verified','ui_healthy'})
        require(result['resources']['api']['image_id']==result['resources']['ui']['image_id'])
        require(host['host']=='127.0.0.1' and host['bindings_verified'] is True and host['ui_healthy'] is True)
        require(all(count(host[k],1024) and host[k]<=65535 for k in ('api_port','ui_port')))
        require(host['api_port']!=host['ui_port'] and number(result['duration_seconds'],0,7200))
    except (KeyError,TypeError,AttributeError) as error:raise ValueError('Malformed T5 report') from error
