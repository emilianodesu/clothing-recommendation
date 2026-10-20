"""Standard-library validation of closed Q1–Q3 aggregate evidence, never raw buyer rows."""
import math
import re

# Canonical SHA-256 of the complete fixed quality-v1 configuration, including future stages.
QUALITY_V1_SHA256='05caf396db1e427b982d3809960109f6f219ce435b69c40b4925323acfec76e7'
CUTOFFS={'V1':('2020-08-26','2020-09-02'),'V2':('2020-09-02','2020-09-09'),'V3':('2020-09-09','2020-09-16')}
METRICS={'map_at_12','ndcg_at_12','recall_at_200'}
SEGMENTS={'recent28d','history29_90d','older_history','no_history'}
ALLOWED={'summary.md','environment.json','commit.txt','preflight.log','job.log','build.log','result.json','config.resolved.json'}
STAGES={'Q1':'baselines','Q2':'graph','Q3':'ranker'}


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
        require(result['stage'] in STAGES and result['protocol']=='quality-v1' and result['status']=='passed')
        require(result['test_evaluated'] is False and result['quality_accepted'] is False and result['verification_exit_code']==0)
        require(result['size'] in ('demo','pilot','full'))
        fold=result['fold']; require(fold['fold_id'] in CUTOFFS and fold['role']=='evaluation' and fold['parent_fold'] is None)
        require((fold['cutoff'],fold['label_end'])==CUTOFFS[fold['fold_id']])
        pop=result['population']; users=pop['users']; catalog=pop['catalog_size']
        require(count(users,1) and count(catalog,1) and count(pop['validation_buyers'],users))
        require(pop['sampled_out_users']==pop['validation_buyers']-users)
        require(result['size']=='pilot' or pop['sampled_out_users']==0)
        require(result['size']!='pilot' or users<=2000)
        expected={'P','H','G'} if result['stage'] in ('Q2','Q3') else {'P','H'}
        if result['stage']=='Q3':
            require(result['ranker']['pool'] in ('H','G'))
            expected.add('R'+result['ranker']['pool'])
        require(set(result['methods'])==expected)
        validate_methods(result['methods'],users,catalog)
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
        require(set(result['artifacts'])==expected)
        for item in result['artifacts'].values():
            require(set(item)=={'ranking_sha256','metrics_sha256'})
            require(all(re.fullmatch('[a-f0-9]{64}',v) for v in item.values()))
        if fold['fold_id']=='V3':require(result['parity']['legacy_ranking_verified'] is True)
        if fold['fold_id']=='V3' and result['size']=='full':
            require(result['reference']['verified'] is True and re.fullmatch('[a-f0-9]{64}',result['reference']['archive_sha256']))
        if result['stage']=='Q2':validate_graph_result(result)
        if result['stage']=='Q3':validate_ranker_result(result)
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
        require(environment['quality']['stage']==STAGES[result['stage']] and environment['quality']['size']==result['size'])
        require(environment['quality']['fold']==result['fold']['fold_id'])
        if result['stage'] in ('Q2','Q3'):
            require(environment['quality']['reference_run_id']==result['reference']['run_id'])
            require(all(environment['quality_reference'][k]==result['reference'][k] for k in ('run_id','archive_sha256')))
        if result['stage']=='Q3':
            require(environment['quality']['pool']==result['ranker']['pool'])
            require(all(environment['quality_reference']['baseline_reference'][k]==result['reference']['baseline'][k]
                        for k in ('run_id','archive_sha256')))
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
    require(environment['quality']['stage']==STAGES[result['stage']] and environment['quality']['size']==result['size'])
    require(environment['quality']['fold']==result['fold']['fold_id'])
    require(report.get('oom_killed') is False and report.get('container_exit_code')==0)
    for key in ('resources','limits','effective_limits'):require(report[key]==environment[key])
    limits=report['limits'];effective=report['effective_limits']
    require(effective['memory_bytes']==limits['memory_bytes'] and effective['nano_cpus']==limits['cpus']*10**9)
    require(all(effective[k]==limits[k] for k in ('polars_threads','omp_threads','mkl_threads')))
    require(environment['isolation']=={'network':'none','writable_mounts':['/artifacts/data-jobs/'+report['run_id']]})
    validate_quality_execution(result,environment)


def validate_methods(methods,users,catalog):
    for name,method in methods.items():
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


def is_sha(value):return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None


def validate_graph_result(result):
    users=result['population']['users'];catalog=result['population']['catalog_size']
    for method in result['methods'].values():
        require(method['denominators']==result['methods']['H']['denominators'])
        require(all(method['segments'][s]['users']==result['methods']['H']['segments'][s]['users'] for s in SEGMENTS))
    reference=result['reference']
    require(reference['verified'] is True and reference['private_artifacts_verified'] is True and reference['stage']=='Q1')
    require(reference['run_id'].startswith('quality-') and is_sha(reference['archive_sha256']))
    graph=result['graph']
    require(graph['nodes']==catalog and graph['window_days']==90 and graph['basket_days']==7 and graph['basket_semantics']=='weekly_proxy')
    for field in ('transactions_window','unique_memberships','seeded_profiles','seed_memberships','baskets','capped_baskets','discarded_memberships','retained_memberships','edges','isolated_nodes','max_degree','unpruned_directed_edges','temporary_peak_bytes','final_bytes'):
        require(count(graph[field]))
    require(graph['unique_memberships']==graph['retained_memberships']+graph['discarded_memberships'] and graph['transactions_window']>=graph['unique_memberships'])
    require(graph['seeded_profiles']<=graph['seed_memberships']<=50*graph['seeded_profiles'])
    require(graph['capped_baskets']<=graph['baskets'] and graph['discarded_memberships']>=graph['capped_baskets'])
    require(graph['retained_memberships']<=graph['baskets']*50 and graph['retained_memberships']>=graph['baskets'])
    require(graph['max_degree']<=50 and graph['edges']<=catalog*50 and graph['isolated_nodes']<=catalog)
    require(graph['unpruned_directed_edges']>=graph['edges'] and number(graph['build_seconds']))
    require(graph['block_nodes']==256 and len(graph['partitions'])==(catalog+255)//256)
    for i,part in enumerate(graph['partitions']):
        require(part['start']==256*i and part['end']==min(catalog,256*(i+1)) and count(part['rows']) and count(part['size_bytes']) and is_sha(part['sha256']))
    require(sum(p['rows'] for p in graph['partitions'])==graph['unpruned_directed_edges'])
    require(set(result['graph_artifacts'])=={'csr','nodes','seeds'} and all(is_sha(v) for v in result['graph_artifacts'].values()))
    retrieval=result['retrieval'];require(retrieval['users']==users and count(retrieval['no_seeds']) and count(retrieval['no_neighbors']))
    require(retrieval['no_seeds']<=retrieval['no_neighbors']<=users)
    pool=result['pool'];require(pool['scope']=='selected_G_pool_at_200' and pool['causal_attribution'] is False)
    allowed={'history','graph','popular','history|graph','history|popular','graph|popular','history|graph|popular'}
    require(set(pool['selected_memberships'])<=allowed and all(count(v,1) for v in pool['selected_memberships'].values()))
    require(sum(pool['selected_memberships'].values())==users*min(200,catalog))
    require(set(pool['relevant_memberships'])<=set(pool['selected_memberships']))
    require(all(count(v) and v<=pool['selected_memberships'][k] for k,v in pool['relevant_memberships'].items()))
    require(pool['source_exclusive_hits']=={k:pool['relevant_memberships'].get(k,0) for k in ('history','graph','popular')})
    costs=result['costs'];require(set(costs)=={'reference_verification_seconds','graph_build_seconds','retrieval_seconds','evaluation_seconds','pagerank_seconds','candidate_cost_seconds'})
    require(all(number(v) for v in costs.values()) and costs['graph_build_seconds']==graph['build_seconds'])
    require(math.isclose(costs['candidate_cost_seconds'],costs['graph_build_seconds']+costs['retrieval_seconds'],abs_tol=1e-10))
    ppr=result['pagerank'];sample=min(5000,users);seeded=ppr['seeded_users']
    require(is_sha(ppr['normal_ranking_sha256']) and is_sha(ppr['iteration_statistics_sha256']))
    require(ppr['scope']=='sample' and ppr['promotion_allowed'] is False and ppr['cohort_users']==sample and is_sha(ppr['cohort_sha256']))
    require(count(seeded) and seeded<=sample and ppr['no_seed_users']==sample-seeded and ppr['no_seed_fallback_H']==sample-seeded)
    require(count(ppr['converged_users']) and count(ppr['nonconverged_fallback_G']) and ppr['converged_users']+ppr['nonconverged_fallback_G']==seeded)
    require(count(ppr['max_nodes']) and ppr['max_nodes']<=512 and count(ppr['max_edges']) and ppr['max_edges']<=512*50)
    require(count(ppr['max_iterations']) and ppr['max_iterations']<=100 and number(ppr['mean_nodes'],0,512))
    require(number(ppr['max_residual_l1'],0,2) and number(ppr['max_converged_residual_l1'],0,1e-6))
    require(count(ppr['truncated_users']) and ppr['truncated_users']<=seeded and count(ppr['truncated_nodes'],ppr['truncated_users']))
    require(set(ppr['methods'])=={'H','G','PPR'});validate_methods(ppr['methods'],sample,catalog)
    for method in ppr['methods'].values():
        require(method['denominators']==ppr['methods']['H']['denominators'])
        require(all(method['segments'][s]['users']==ppr['methods']['H']['segments'][s]['users'] for s in SEGMENTS))
    require(ppr['personalization_applicable']==(seeded>=2))
    require(len(ppr['permutations'])==(10 if seeded>=2 else 0))
    for seed,permutation in zip(range(42,52),ppr['permutations']):
        require(permutation['seed']==seed and permutation['fixed_owners']==0 and is_sha(permutation['ranking_sha256']))
        metrics(permutation['metrics_all']);metrics(permutation['metrics_seeded'])
    personal=ppr['personalization'];require(personal['users']==seeded and personal['excluded_no_seeds']==sample-seeded)
    if seeded:metrics(personal['normal'])
    else:require(personal['normal'] is None)
    if seeded>=2:
        metrics(personal['permuted_mean']);require(set(personal['delta'])==METRICS)
        for metric in METRICS:
            mean=sum(p['metrics_seeded'][metric] for p in ppr['permutations'])/10
            require(math.isclose(mean,personal['permuted_mean'][metric],abs_tol=1e-10,rel_tol=0))
            require(number(personal['delta'][metric],-1,1) and math.isclose(personal['normal'][metric]-mean,personal['delta'][metric],abs_tol=1e-10,rel_tol=0))
    else:require(personal['permuted_mean'] is None and personal['delta'] is None)


RANKER_FEATURES=['pair_count28','pair_count90','pair_recency90','user_count28','user_count90','user_recency90',
    'item_count7','item_count28','item_count90','item_trend','affinity_type90','affinity_group90','affinity_color90',
    'graph_score','history_rank','neighbor_rank','popular_rank','in_history','in_neighbors','in_popular']
RANKER_PARAMETERS=dict(objective='lambdarank',label_gain=[0,1],metric='None',lambdarank_truncation_level=15,
    learning_rate=.05,num_leaves=31,min_data_in_leaf=100,max_bin=127,lambda_l2=1,feature_fraction=1,
    bagging_fraction=1,bagging_freq=0,seed=42,num_threads=2,deterministic=True,force_col_wise=True,
    device_type='cpu',data_random_seed=42,feature_fraction_seed=42,bagging_seed=42,drop_seed=42,
    extra_seed=42,objective_seed=42,verbosity=-1)
GROUP_COUNTS=('groups_total','groups_trainable','groups_without_positives','groups_without_negatives',
    'candidate_rows','sampled_rows','relevant_items','positives_recovered','positives_not_recovered')


def validate_ranker_result(result):
    from datetime import date,timedelta
    ranker=result['ranker'];pool=ranker['pool'];method='R'+pool
    require(ranker['feature_version']==1 and ranker['feature_names']==RANKER_FEATURES)
    require(ranker['parameters']==RANKER_PARAMETERS and ranker['lightgbm_version']=='4.7.0')
    require(count(ranker['best_iteration'],1) and ranker['best_iteration']<=300)
    require(ranker['candidate_set_verified'] is True and ranker['pool_replay_verified'] is True)
    require(is_sha(ranker['training_table_sha256']) and is_sha(ranker['model_manifest_sha256']))
    require(set(ranker['model_artifacts'])=={'model','inner_model','inner_training','inner_validation','refit'})
    require(all(is_sha(v) for v in ranker['model_artifacts'].values()))
    require(set(ranker['outer_graph_artifacts'])=={'csr','nodes','seeds'} and all(is_sha(v) for v in ranker['outer_graph_artifacts'].values()))
    require(ranker['reference_pool_ranking_sha256']==result['artifacts'][pool]['ranking_sha256'])
    reference=result['reference']
    require(reference['stage']=='Q2' and reference['verified'] is True and reference['private_artifacts_verified'] is True)
    for entry in (reference,reference['baseline']):
        require(entry['run_id'].startswith('quality-') and is_sha(entry['archive_sha256']))
    base=result['methods'][pool];reranked=result['methods'][method]
    for value in result['methods'].values():
        require(value['denominators']==base['denominators'])
        require(all(value['segments'][s]['users']==base['segments'][s]['users'] for s in SEGMENTS))
    def recall_equal(a,b):
        require(math.isclose(a['recall_at_200'],b['recall_at_200'],abs_tol=1e-12,rel_tol=0))
    recall_equal(base['metrics'],reranked['metrics'])
    for name in SEGMENTS:
        if base['segments'][name]['users']:recall_equal(base['segments'][name]['metrics'],reranked['segments'][name]['metrics'])
    for name in ('repeat','discovery'):
        require(base['targets'][name]['users']==reranked['targets'][name]['users'])
        if base['targets'][name]['users']:recall_equal(base['targets'][name]['metrics'],reranked['targets'][name]['metrics'])
    require(base['exposure']['200']['unique_sets']==reranked['exposure']['200']['unique_sets'])
    weeks=ranker['weeks'];require(len(weeks)==4)
    cutoff=date.fromisoformat(result['fold']['cutoff'])
    sums={key:0 for key in GROUP_COUNTS}
    for index,week in enumerate(weeks):
        start=cutoff-timedelta(days=7*(4-index))
        require(week['cutoff']==str(start) and week['label_end']==str(start+timedelta(days=7)))
        require(date.fromisoformat(week['max_feature_date'])<start)
        require(start<=date.fromisoformat(week['max_label_date'])<start+timedelta(days=7))
        require(week['role']==('early_stopping' if index==3 else 'inner_training'))
        require(count(week['buyers'],1) and count(week['buyers_before_sampling'],week['buyers']))
        require(week['sampled_out_buyers']==week['buyers_before_sampling']-week['buyers'])
        require(result['size']=='pilot' or week['sampled_out_buyers']==0)
        require(result['size']!='pilot' or week['buyers']<=2000)
        require(count(week['catalog_size'],1) and count(week['label_transactions_all_buyers'],week['buyers_before_sampling']))
        require(count(week['history_transactions90']) and number(week['preparation_seconds']))
        require(all(is_sha(week[k]) for k in ('snapshot_sha256','graph_sha256','ranking_sha256')))
        for name in ('sample','groups'):
            require(week[name+'_file']==f'week-{index}-{name}')
        require(week.get('full_file')==f'week-{index}-full' if index==3 else 'full_file' not in week)
        counts=week['counts'];require(set(counts)==set(GROUP_COUNTS) and all(count(v) for v in counts.values()))
        require(counts['groups_total']==week['buyers'])
        require(counts['groups_trainable']+counts['groups_without_positives']+counts['groups_without_negatives']==counts['groups_total'])
        require(counts['candidate_rows']==week['buyers']*min(200,week['catalog_size']))
        require(counts['relevant_items']>=week['buyers'] and counts['positives_recovered']+counts['positives_not_recovered']==counts['relevant_items'])
        require(counts['groups_trainable']<=counts['positives_recovered']<=counts['candidate_rows'])
        require(2*counts['groups_trainable']<=counts['sampled_rows']<=min(counts['candidate_rows'],counts['positives_recovered']+20*counts['groups_trainable']))
        for key in sums:sums[key]+=counts[key]
    totals=ranker['training_counts']
    require(all(totals[key]==value for key,value in sums.items()))
    require(number(totals['preparation_seconds']) and count(totals['private_table_bytes'],1))
    require(totals['preparation_seconds']>=sum(w['preparation_seconds'] for w in weeks))
    stats=ranker['statistics']
    require(stats['outer_labels_used'] is False and stats['inner_training_weeks']==3 and stats['refit_training_weeks']==4)
    require(stats['early_stopping_cutoff']==weeks[-1]['cutoff'])
    require(stats['inner_training_groups']==sum(w['counts']['groups_trainable'] for w in weeks[:3]) and count(stats['inner_training_groups'],1))
    require(stats['inner_training_rows']==sum(w['counts']['sampled_rows'] for w in weeks[:3]))
    require(stats['inner_evaluation_groups']==weeks[-1]['buyers'] and stats['inner_evaluation_rows']==weeks[-1]['counts']['candidate_rows'])
    require(stats['refit_groups']==sums['groups_trainable'] and stats['refit_rows']==sums['sampled_rows'])
    require(stats['refit_requested_iterations']==ranker['best_iteration'])
    require(count(stats['actual_trees'],1) and stats['actual_trees']<=ranker['best_iteration'])
    history=stats['inner_ndcg_history']
    require(len(history)==stats['evaluated_iterations'] and ranker['best_iteration']<=len(history)<=300)
    require(all(number(v,0,1) for v in history) and number(stats['inner_best_ndcg'],0,1))
    require(math.isclose(history[ranker['best_iteration']-1],stats['inner_best_ndcg'],rel_tol=0,abs_tol=1e-12))
    require(math.isclose(max(history),stats['inner_best_ndcg'],rel_tol=0,abs_tol=1e-12))
    costs=result['costs']
    fields={'reference_verification_seconds','training_preparation_seconds','binary_construction_seconds',
        'fit_seconds','refit_seconds','external_graph_build_seconds','external_retrieval_seconds',
        'prediction_seconds','evaluation_seconds','candidate_cost_seconds'}
    require(set(costs)==fields and all(number(v) for v in costs.values()))
    require(costs['training_preparation_seconds']==totals['preparation_seconds'])
    require(all(costs[k]==stats[k] for k in ('binary_construction_seconds','fit_seconds','refit_seconds')))
    require(math.isclose(costs['candidate_cost_seconds'],sum(v for k,v in costs.items() if k not in
        ('reference_verification_seconds','evaluation_seconds','candidate_cost_seconds')),rel_tol=0,abs_tol=1e-8))
