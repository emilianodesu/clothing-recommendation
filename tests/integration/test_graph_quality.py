import json
from pathlib import Path
import sys
from unittest.mock import patch
from collections import namedtuple
import polars as pl
import pytest
from hm_recommender.data.io import atomic_json,sha256
from hm_recommender.quality.contracts import QualityConfig


def test_graph_worker_reuses_verified_Q1_and_reports_sample_separately(tmp_path):
    from hm_recommender.quality.worker import run
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    from quality_handoff import validate_quality_result
    from zipfile import ZipFile
    baseline=tmp_path/'baseline';baseline.mkdir()
    env={'source_commit':'a'*40,'quality':{'stage':'baselines','fold':'V3','size':'demo'}}
    atomic_json(baseline/'environment.json',env)
    config=tmp_path/'config.json';atomic_json(config,QualityConfig().model_dump())
    disk=namedtuple('disk_usage','total used free')(100*2**30,20*2**30,80*2**30)
    with patch('hm_recommender.quality.worker.shutil.disk_usage',return_value=disk):
        assert run(config,baseline,verify=False)==0
    original=json.loads((baseline/'result.json').read_text())
    archive=tmp_path/'q1.zip'
    with ZipFile(archive,'w') as z:z.write(baseline/'result.json','result.json')
    root=tmp_path/'graph';root.mkdir()
    env['quality']['stage']='graph';env['quality']['reference_run_id']='quality-reference';env['quality_reference']={'run_id':'quality-reference','archive_sha256':sha256(archive),'path':str(archive),'work_path':str(baseline/'work')}
    atomic_json(root/'environment.json',env)
    with patch('hm_recommender.quality.worker.shutil.disk_usage',return_value=disk):
        assert run(config,root,verify=False)==0
    result=json.loads((root/'result.json').read_text());validate_quality_result(result)
    assert result['stage']=='Q2' and set(result['methods'])=={'P','H','G'}
    assert result['methods']['H']==original['methods']['H'] and result['reference']['verified']
    control=result['pagerank'];assert control['scope']=='sample' and control['promotion_allowed'] is False
    assert control['cohort_users']==33 and control['seeded_users']==32 and control['no_seed_users']==1
    assert set(control['methods'])=={'H','G','PPR'} and len(control['permutations'])==10
    assert all(p['fixed_owners']==0 for p in control['permutations'])
    assert control['personalization']['users']==32 and control['personalization']['excluded_no_seeds']==1
    assert result['graph']['edges']>0 and result['graph']['max_degree']<=50
    assert sum(result['pool']['selected_memberships'].values())==33*24
    assert 'customer_id' not in json.dumps(result)
    assert not (root/'work/evaluation-H').exists()  # Q1 artifacts reused, not recomputed.
    from copy import deepcopy
    for keys,value in [(('graph','max_degree'),51),(('pagerank','scope'),'full'),
                       (('pagerank','seeded_users'),33),(('pagerank','cohort_users'),32),
                       (('pagerank','personalization','excluded_no_seeds'),0),
                       (('pagerank','personalization','delta','map_at_12'),float('nan')),
                       (('reference','private_artifacts_verified'),False),
                       (('costs','candidate_cost_seconds'),0), (('pool','selected_memberships'),{})]:
        broken=deepcopy(result);target=broken
        for key in keys[:-1]:target=target[key]
        target[keys[-1]]=value
        with pytest.raises(ValueError):validate_quality_result(broken)
    # Exercise the actual aggregate ZIP contract, preserving its operational envelope.
    from handoff import package_report,read_report
    limits={'memory_bytes':5*2**30,'cpus':3,'polars_threads':2,'omp_threads':2,'mkl_threads':2}
    effective=dict(limits,nano_cpus=3*10**9);effective.pop('cpus')
    env.update(run_id='quality-fixture',resources={'name':'base'},limits=limits,effective_limits=effective,
        start_free_bytes=80*2**30,quality_budget_seconds=7200,
        quality_budget={'demo':result['job_duration_seconds'],'full':0,'pilot':0,'summary':0},
        isolation={'network':'none','writable_mounts':['/artifacts/data-jobs/quality-fixture']})
    atomic_json(root/'environment.json',env)
    report={'schema_version':1,'kind':'quality-handoff','run_id':'quality-fixture','machine_id':'local','context':'colima',
        'source_commit':'a'*40,'source_clean':True,'status':'passed','steps':[],'result':result,
        'container_exit_code':0,'oom_killed':False,**{k:env[k] for k in ('resources','limits','effective_limits')}}
    closed=package_report(root,report)
    assert read_report(closed)['result']==result
    from quality_handoff import validate_quality_execution
    wrong=deepcopy(env);wrong['quality_reference']['archive_sha256']='f'*64
    with pytest.raises(ValueError):validate_quality_execution(result,wrong)

    # A self-consistent ZIP must still be refused if its required local data changed.
    (baseline/'work/ranking-H/ranking.parquet').write_bytes(b'corrupt')
    other=tmp_path/'bad';other.mkdir();atomic_json(other/'environment.json',env)
    with patch('hm_recommender.quality.worker.shutil.disk_usage',return_value=disk):
        assert run(config,other,verify=False)==1


def test_pagerank_without_two_seeded_buyers_excludes_personalization(tmp_path):
    from hm_recommender.model.demo import prepare_demo_dataset
    from hm_recommender.quality.temporal import build_snapshot
    from hm_recommender.quality.contracts import FoldSpec
    from hm_recommender.graph.build import build_graph
    from hm_recommender.graph.pagerank import pagerank_control
    ds=prepare_demo_dataset(tmp_path/'demo');config=QualityConfig()
    snapshot=build_snapshot(ds,FoldSpec.evaluation('V3'),tmp_path/'snapshot',config)
    graph=build_graph(ds,snapshot,config,tmp_path/'graph')
    result=pagerank_control(snapshot,graph,['demo-000','demo-032'],tmp_path/'ppr',config)
    assert result['seeded_users']==1 and result['no_seed_users']==1
    assert result['personalization_applicable'] is False and result['permutations']==[]
    assert (tmp_path/'ppr/iterations.parquet').exists()
    assert len(result['iteration_statistics_sha256'])==64
