"""Standard-library validation of closed Q1 aggregate evidence, never raw buyer rows."""
import math
import re

# Canonical SHA-256 of the complete fixed quality-v1 configuration, including future stages.
QUALITY_V1_SHA256='05caf396db1e427b982d3809960109f6f219ce435b69c40b4925323acfec76e7'
CUTOFFS={'V1':('2020-08-26','2020-09-02'),'V2':('2020-09-02','2020-09-09'),'V3':('2020-09-09','2020-09-16')}
METRICS={'map_at_12','ndcg_at_12','recall_at_200'}
SEGMENTS={'recent28d','history29_90d','older_history','no_history'}
ALLOWED={'summary.md','environment.json','commit.txt','preflight.log','job.log','build.log','result.json','config.resolved.json'}


def require(condition):
    if not condition: raise ValueError('Incomplete or inconsistent quality report')


def number(value,low=0,high=float('inf')):
    return type(value) in (int,float) and math.isfinite(value) and low<=value<=high


def count(value,low=0): return type(value) is int and value>=low


def metrics(value):
    require(isinstance(value,dict) and set(value)==METRICS and all(number(v,0,1) for v in value.values()))


def no_private_rows(value):
    if isinstance(value,dict):
        require(not {'customer_id','article_id','customers','rankings','predictions','weights'}.intersection(value))
        for nested in value.values(): no_private_rows(nested)
    elif isinstance(value,list):
        for nested in value: no_private_rows(nested)


def validate_quality_result(result):
    try:
        no_private_rows(result)
        require(result['stage']=='Q1' and result['protocol']=='quality-v1' and result['status']=='passed')
        require(result['test_evaluated'] is False and result['quality_accepted'] is False and result['verification_exit_code']==0)
        require(result['size'] in ('demo','pilot','full'))
        fold=result['fold']; require(fold['fold_id'] in CUTOFFS and fold['role']=='evaluation' and fold['parent_fold'] is None)
        require((fold['cutoff'],fold['label_end'])==CUTOFFS[fold['fold_id']])
        pop=result['population']; users=pop['users']; catalog=pop['catalog_size']
        require(count(users,1) and count(catalog,1) and count(pop['validation_buyers'],users))
        require(pop['sampled_out_users']==pop['validation_buyers']-users)
        require(result['size']=='pilot' or pop['sampled_out_users']==0)
        require(result['size']!='pilot' or users<=2000)
        require(set(result['methods'])=={'P','H'})
        for name,method in result['methods'].items():
            require(method['population']=={'users':users,'catalog_size':catalog})
            metrics(method['metrics']); segments=method['segments']; require(set(segments)==SEGMENTS)
            require(sum(s['users'] for s in segments.values())==users)
            for segment in segments.values():
                require(count(segment['users']))
                if segment['users']:metrics(segment['metrics'])
                else:require(segment['metrics'] is None)
            for metric in METRICS:
                mean=sum(s['users']*s['metrics'][metric] for s in segments.values() if s['users'])/users
                require(math.isclose(mean,method['metrics'][metric],abs_tol=1e-10,rel_tol=0))
            denom=method['denominators']
            require(count(denom['relevant_items'],users) and count(denom['out_of_catalog_relevant_items']))
            require(denom['out_of_catalog_relevant_items']<=denom['relevant_items'] and number(denom['mean_available_recall_ceiling'],0,1))
            require(set(method['exposure'])=={'12','200'})
            for key,exposure in method['exposure'].items():
                require(exposure['k']==min(int(key),catalog) and count(exposure['distinct_items'],1) and exposure['distinct_items']<=catalog)
                require(math.isclose(exposure['coverage'],exposure['distinct_items']/catalog,abs_tol=1e-10))
                require(count(exposure['unique_ordered_lists'],1) and exposure['unique_ordered_lists']<=users)
                require(count(exposure['unique_sets'],1) and exposure['unique_sets']<=users)
                require(number(exposure['effective_items'],1,catalog+1e-8))
                require(set(exposure['top_exposure_share'])=={'1','5','10','50'})
                shares=[exposure['top_exposure_share'][str(n)] for n in (1,5,10,50)]
                require(all(number(v,0,1) for v in shares) and shares==sorted(shares))
            require(set(method['targets'])=={'repeat','discovery'})
            require(sum(t['relevant_items'] for t in method['targets'].values())==denom['relevant_items'])
            for target in method['targets'].values():
                require(count(target['users']) and target['users']<=users and count(target['relevant_items'],target['users']))
                if target['users']:metrics(target['metrics'])
                else:require(target['metrics'] is None and target['relevant_items']==0)
        p,h=result['methods']['P'],result['methods']['H']
        require(p['denominators']==h['denominators'])
        for segment in SEGMENTS:
            require(p['segments'][segment]['users']==h['segments'][segment]['users'])
            if segment!='recent28d': require(p['segments'][segment]==h['segments'][segment])
        from datetime import date
        require(date.fromisoformat(result['inputs']['max_feature_date'])<date.fromisoformat(fold['cutoff']))
        require(result['inputs']['protocol_sha256']==QUALITY_V1_SHA256)
        for key in ('source_sha256','dataset_manifest_sha256','protocol_sha256','snapshot_sha256'):
            require(isinstance(result['inputs'][key],str) and re.fullmatch('[a-f0-9]{64}',result['inputs'][key]))
        require(set(result['artifacts'])=={'P','H'})
        for item in result['artifacts'].values():
            require(set(item)=={'ranking_sha256','metrics_sha256'})
            require(all(re.fullmatch('[a-f0-9]{64}',v) for v in item.values()))
        if fold['fold_id']=='V3':require(result['parity']['legacy_ranking_verified'] is True)
        if fold['fold_id']=='V3' and result['size']=='full':
            require(result['reference']['verified'] is True and re.fullmatch('[a-f0-9]{64}',result['reference']['archive_sha256']))
        require(number(result['job_duration_seconds'],0,7200) and count(result['job_resources']['peak_rss_bytes'],1))
        disk=result['disk']; require(set(disk)=={'start_free_bytes','min_free_bytes','end_free_bytes'})
        require(all(count(v,5*2**30) for v in disk.values()))
        require(disk['min_free_bytes']<=min(disk['start_free_bytes'],disk['end_free_bytes']))
    except (KeyError,TypeError,AttributeError,OverflowError) as error:
        raise ValueError('Malformed quality report') from error



def validate_quality_execution(result,environment):
    validate_quality_result(result)
    try:
        require(result['source_commit']==environment['source_commit'])
        require(environment['quality']['stage']=='baselines' and environment['quality']['size']==result['size'])
        require(environment['quality']['fold']==result['fold']['fold_id'])
        require(count(environment['start_free_bytes'],20*2**30))
        allowance=environment['quality_budget_seconds'];require(number(allowance,0,7200) and allowance>0)
        require(result['job_duration_seconds']<=allowance)
        budget=environment['quality_budget'];require(set(budget)=={'demo','pilot','full','summary'})
        require(all(number(v) for v in budget.values()))
        require(budget['pilot']<=7200 and budget['full']<=84600 and budget['summary']<=1800)
        # Docker timestamps and the VM monotonic clock may differ by fractions of
        # a second. This tolerance only reconciles charged time, never the deadline.
        require(budget[result['size']]>=result['job_duration_seconds']-1)
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError('Missing or malformed quality operational evidence') from error


def validate_quality_envelope(report,result,environment,names):
    require(set(names)<={'manifest.json',*ALLOWED} and {'result.json','environment.json','config.resolved.json'}<=set(names))
    require(report['result']==result and report['source_commit']==environment['source_commit']==result['source_commit'])
    require(report['run_id']==environment['run_id'] and report['run_id'].startswith('quality-'))
    require(environment['quality']['stage']=='baselines' and environment['quality']['size']==result['size'])
    require(environment['quality']['fold']==result['fold']['fold_id'])
    require(report.get('oom_killed') is False and report.get('container_exit_code')==0)
    for key in ('resources','limits','effective_limits'):require(report[key]==environment[key])
    limits=report['limits'];effective=report['effective_limits']
    require(effective['memory_bytes']==limits['memory_bytes'] and effective['nano_cpus']==limits['cpus']*10**9)
    require(all(effective[k]==limits[k] for k in ('polars_threads','omp_threads','mkl_threads')))
    require(environment['isolation']=={'network':'none','writable_mounts':['/artifacts/data-jobs/'+report['run_id']]})
    validate_quality_execution(result,environment)
