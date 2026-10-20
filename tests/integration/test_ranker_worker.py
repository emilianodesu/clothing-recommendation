from collections import namedtuple
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.quality.contracts import QualityConfig
from hm_recommender.quality.worker import run


def test_Q3_worker_reference_chain_closed_zip_and_rejections(tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    from quality_handoff import validate_quality_result, validate_quality_execution
    from handoff import package_report, read_report
    config=tmp_path/'config.json';atomic_json(config,QualityConfig().model_dump())
    disk=namedtuple('disk_usage','total used free')(100*2**30,20*2**30,80*2**30)
    def execute(stage, root, reference=None, pool=None):
        root.mkdir()
        env={'source_commit':'a'*40,'quality':{'stage':stage,'fold':'V3','size':'demo'}}
        if reference:
            env['quality']['reference_run_id']=reference['run_id'];env['quality_reference']=reference
        if pool: env['quality']['pool']=pool
        atomic_json(root/'environment.json',env)
        with patch('hm_recommender.quality.worker.shutil.disk_usage',return_value=disk):
            assert run(config,root,verify=False)==0
        result=json.loads((root/'result.json').read_text())
        validate_quality_result(result)
        return env,result
    q1=tmp_path/'q1';_,original=execute('baselines',q1)
    q1zip=tmp_path/'q1.zip'
    with ZipFile(q1zip,'w') as z:z.write(q1/'result.json','result.json')
    baseline={'run_id':'quality-q1','archive_sha256':sha256(q1zip),'path':str(q1zip),'work_path':str(q1/'work')}
    q2=tmp_path/'q2';_,graph=execute('graph',q2,baseline)
    q2zip=tmp_path/'q2.zip'
    with ZipFile(q2zip,'w') as z:z.write(q2/'result.json','result.json')
    reference={'run_id':'quality-q2','archive_sha256':sha256(q2zip),'path':str(q2zip),
               'work_path':str(q2/'work'),'baseline_reference':baseline}
    for pool in ('H','G'):
        root=tmp_path/('quality-fixture-'+pool)
        env,result=execute('ranker',root,reference,pool)
        method='R'+pool
        assert result['stage']=='Q3' and result['ranker']['pool']==pool
        assert result['methods']['P']==original['methods']['P']
        assert result['methods']['G']==graph['methods']['G']
        assert result['methods'][method]['metrics']['recall_at_200']==result['methods'][pool]['metrics']['recall_at_200']
        assert result['ranker']['candidate_set_verified'] and result['ranker']['pool_replay_verified']
        assert len(result['ranker']['weeks'])==4
        assert 'customer_id' not in json.dumps(result)
        for keys,value in [(('ranker','best_iteration'),301),(('ranker','feature_names'),[]),
                (('ranker','candidate_set_verified'),False),(('ranker','statistics','outer_labels_used'),True),
                (('ranker','statistics','inner_evaluation_groups'),0),
                (('ranker','weeks',0,'cutoff'),'2020-09-09'),
                (('ranker','weeks',0,'max_feature_date'),'2020-09-09'),
                (('ranker','weeks',0,'counts','sampled_rows'),0),
                (('costs','candidate_cost_seconds'),0),
                (('reference','private_artifacts_verified'),False),
                (('ranker','parameters','metric'),'ndcg')]:
            broken=deepcopy(result);target=broken
            for key in keys[:-1]:target=target[key]
            target[keys[-1]]=value
            with pytest.raises(ValueError):validate_quality_result(broken)
        limits={'memory_bytes':5*2**30,'cpus':3,'polars_threads':2,'omp_threads':2,'mkl_threads':2}
        effective=dict(limits,nano_cpus=3*10**9);effective.pop('cpus')
        env.update(run_id=root.name,resources={'name':'base'},limits=limits,effective_limits=effective,
            start_free_bytes=80*2**30,quality_budget_seconds=7200,
            quality_budget={'demo':result['job_duration_seconds'],'full':0,'pilot':0,'summary':0},
            isolation={'network':'none','writable_mounts':['/artifacts/data-jobs/'+root.name]})
        atomic_json(root/'environment.json',env)
        report={'schema_version':1,'kind':'quality-handoff','run_id':root.name,'machine_id':'local','context':'colima',
            'source_commit':'a'*40,'source_clean':True,'status':'passed','steps':[],'result':result,
            'container_exit_code':0,'oom_killed':False,**{k:env[k] for k in ('resources','limits','effective_limits')}}
        assert read_report(package_report(root,report))['result']==result
        wrong=deepcopy(env);wrong['quality']['pool']='G' if pool=='H' else 'H'
        with pytest.raises(ValueError):validate_quality_execution(result,wrong)
    # Closed aggregate evidence is insufficient after a private source changes.
    manifest=json.loads((q2/'work/graph/manifest.json').read_text())
    (q2/'work/graph'/manifest['files']['csr']['path']).write_bytes(b'corrupt')
    bad=tmp_path/'bad';bad.mkdir()
    atomic_json(bad/'environment.json',env)
    with patch('hm_recommender.quality.worker.shutil.disk_usage',return_value=disk):
        assert run(config,bad,verify=False)==1
