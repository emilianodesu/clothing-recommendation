"""Standard-library validation of Q4 evidence and its frozen decision rule."""
import hashlib
import json
import math
from pathlib import Path
import sys

from quality_handoff import require,number,count,is_sha,validate_methods,QUALITY_V1_SHA256,SEGMENTS

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hm_recommender.quality.selection import FOLDS,CANDIDATES,METRICS,mean_metrics,select_candidate


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_declaration(value):
    require(set(value)=={'schema_version','protocol','size','folds'})
    require(value['schema_version']==1 and value['protocol']=='quality-v1' and value['size'] in ('demo','full'))
    require(set(value['folds'])==set(FOLDS))
    seen=set()
    for roles in value['folds'].values():
        require(set(roles)=={'Q1','Q2','RH','RG'} and roles['Q1'] is not None)
        require(roles['Q2'] is not None or (roles['RH'] is None and roles['RG'] is None))
        for rid in roles.values():
            if rid is None:continue
            require(isinstance(rid,str) and rid.startswith('quality-') and rid not in seen)
            seen.add(rid)


def validate_summary(result):
    require(result['stage']=='Q4' and result['protocol']=='quality-v1' and result['size']=='summary' and result['status']=='passed')
    require(result['test_evaluated'] is False and result['quality_accepted'] is False and result['verification_exit_code']==0)
    require(result['scope'] in ('demo','full'))
    inputs=result['inputs']
    require(set(inputs)=={'protocol_sha256','source_sha256','dataset_manifest_sha256','source_reports_sha256','campaign_declaration_sha256'})
    require(all(is_sha(v) for v in inputs.values()) and inputs['protocol_sha256']==QUALITY_V1_SHA256)
    sources=result['sources'];indexed={}
    for entry in sources:
        require(set(entry)=={'fold','role','run_id','archive_sha256'})
        require(entry['fold'] in FOLDS and entry['role'] in ('Q1','Q2','RH','RG'))
        require(isinstance(entry['run_id'],str) and entry['run_id'].startswith('quality-') and is_sha(entry['archive_sha256']))
        key=(entry['fold'],entry['role']);require(key not in indexed);indexed[key]=entry
    require(len({e['run_id'] for e in sources})==len(sources))
    require(sources==sorted(sources,key=lambda e:(e['fold'],e['role'])) and inputs['source_reports_sha256']==digest(sources))
    declaration={'schema_version':1,'protocol':'quality-v1','size':result['scope'],
        'folds':{f:{r:indexed.get((f,r),{}).get('run_id') for r in ('Q1','Q2','RH','RG')} for f in FOLDS}}
    validate_declaration(declaration);require(inputs['campaign_declaration_sha256']==digest(declaration))
    methods=result['methods'];expected={('P' if r=='Q1' else 'G' if r=='Q2' else r,f) for f,r in indexed}
    expected|={('H',f) for f in FOLDS}
    require({(m,f) for m,v in methods.items() for f in v['folds']}==expected)
    for method,item in methods.items():
        require(set(item)=={'folds','cost_seconds','peak_memory_bytes'})
        require(count(item['peak_memory_bytes'],1))
        require(item['cost_seconds'] is None if method in ('P','H') else number(item['cost_seconds']))
    for fold in FOLDS:
        baseline=methods['H']['folds'][fold];pop=baseline['population']
        require(count(pop['users'],1) and count(pop['catalog_size'],1))
        values={m:v['folds'][fold] for m,v in methods.items() if fold in v['folds']}
        validate_methods(values,pop['users'],pop['catalog_size'])
        for item in values.values():
            require(item['denominators']==baseline['denominators'])
            require(all(item['segments'][s]['users']==baseline['segments'][s]['users'] for s in SEGMENTS))
        for reranked,pool in (('RH','H'),('RG','G')):
            if reranked in values:
                require(values[reranked]['metrics']['recall_at_200']==values[pool]['metrics']['recall_at_200'])
    identities=result['evaluations'];require(len(identities)==len(expected))
    require({(e['method'],e['fold']) for e in identities}==expected)
    for item in identities:
        require(set(item)=={'fold','method','run_id','manifest_sha256','metrics_sha256','ranking_sha256','labels_sha256','buyers','metrics_bytes_verified','ranking_bytes_rehashed'})
        method=item['method'];fold=item['fold'];role='Q1' if method in ('P','H') else 'Q2' if method=='G' else method
        require(item['run_id']==indexed[fold,role]['run_id'] and item['buyers']==methods[method]['folds'][fold]['population']['users'])
        require(all(is_sha(item[k]) for k in ('manifest_sha256','metrics_sha256','ranking_sha256','labels_sha256')))
        require(item['metrics_bytes_verified'] is True and item['ranking_bytes_rehashed'] is False)
    for fold in FOLDS:require(len({e['labels_sha256'] for e in identities if e['fold']==fold})==1)
    complete=[m for m in CANDIDATES if m in methods and set(methods[m]['folds'])==set(FOLDS)]
    bootstrap=result['bootstrap'];artifact=result['bootstrap_artifact']
    if complete:
        require(set(bootstrap)=={'resamples','seed','cluster','fold_weighting','family_contrasts','family_confidence','exploratory_confidence','unique_buyers','observations','fold_buyers','discarded_empty_fold_draws','contrasts'})
        require(bootstrap['resamples']==2000 and bootstrap['seed']==42 and bootstrap['cluster']=='customer' and bootstrap['fold_weighting']=='equal')
        require(bootstrap['family_contrasts']==3 and bootstrap['family_confidence']==1-.05/3 and bootstrap['exploratory_confidence']==.95)
        require(bootstrap['fold_buyers']=={f:methods['H']['folds'][f]['population']['users'] for f in FOLDS})
        counts=list(bootstrap['fold_buyers'].values())
        require(bootstrap['observations']==sum(counts) and count(bootstrap['unique_buyers'],max(counts)) and bootstrap['unique_buyers']<=sum(counts))
        require(count(bootstrap['discarded_empty_fold_draws']) and bootstrap['discarded_empty_fold_draws']<=198000)
        require(set(bootstrap['contrasts'])==set(complete))
        base=mean_metrics(methods['H'])
        for method,contrasts in bootstrap['contrasts'].items():
            require(set(contrasts)==set(METRICS));means=mean_metrics(methods[method])
            for metric,value in contrasts.items():
                require(set(value)==({'delta','family_interval','exploratory_interval'} if metric=='map_at_12' else {'delta','exploratory_interval'}))
                require(number(value['delta'],-1,1) and math.isclose(value['delta'],means[metric]-base[metric],abs_tol=1e-10,rel_tol=0))
                for key in set(value)-{'delta'}:
                    interval=value[key];require(isinstance(interval,list) and len(interval)==2 and all(number(v,-1,1) for v in interval) and interval[0]<=interval[1])
                if metric=='map_at_12':require(value['family_interval'][0]<=value['exploratory_interval'][0]<=value['exploratory_interval'][1]<=value['family_interval'][1])
        require(set(artifact)=={'sha256','rows','bytes'} and is_sha(artifact['sha256']) and artifact['rows']==2000 and count(artifact['bytes'],1))
    else:require(bootstrap is None and artifact is None)
    decision=select_candidate(methods,bootstrap['contrasts'] if bootstrap else {})
    require(result['decision']==decision and result['comparison_complete']==(len(complete)==3))
    require(result['selection_finalized']==(len(complete)==3 and result['scope']=='full'))
    selected=decision['selected_method'];ranker=selected in ('RH','RG')
    require(result['procedure']=={'method':selected,'protocol':'quality-v1','config_sha256':QUALITY_V1_SHA256,
        'cutoff_parameterized':True,'training_weeks':4 if ranker else 0,
        'refit_rule':'three_weeks_then_fourth_early_stopping_then_four_week_refit' if ranker else 'past_only_state',
        'test_policy':'T8_once_selected_and_P_H_no_retuning'})
    require(set(result['costs'])=={'verification_seconds','bootstrap_and_selection_seconds'} and all(number(v) for v in result['costs'].values()))
    require(result['cost_scope']=={'P_H':'not_separately_measured_not_used_in_tie_break','G_RH_RG':'preparation_fit_ranking_excludes_controls_and_evaluation','memory':'source_job_peak_upper_bound_including_controls'})
    require(number(result['job_duration_seconds'],0,1800) and sum(result['costs'].values())<=result['job_duration_seconds'])
    require(count(result['job_resources']['peak_rss_bytes'],1))
    disk=result['disk'];require(set(disk)=={'start_free_bytes','min_free_bytes','end_free_bytes'} and all(count(v,5*2**30) for v in disk.values()))
    require(disk['min_free_bytes']<=min(disk['start_free_bytes'],disk['end_free_bytes']))


def validate_summary_execution(result,environment):
    campaign=environment['quality_campaign'];declaration=campaign['declaration'];validate_declaration(declaration)
    require(digest(declaration)==result['inputs']['campaign_declaration_sha256'])
    entries=campaign['entries']
    expected=sorted([{k:e[k] for k in ('fold','role','run_id','archive_sha256')} for e in entries],key=lambda e:(e['fold'],e['role']))
    require(expected==result['sources'])
    for entry in entries:
        rid=entry['run_id'];require(entry['work_path']=='/artifacts/data-jobs/'+rid+'/work')
        require(entry['path'] in ('/artifacts/handoff/'+rid+'.zip','/artifacts/incoming/'+rid+'.zip'))
    require(0<environment['quality_budget_seconds']<=1800)
