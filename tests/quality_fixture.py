"""Synthetic closed Q1–Q3 evidence for Q4 integration tests and local demos.

These child reports are component fixtures, not remote Docker execution evidence.
"""
import json
from pathlib import Path
import sys
from unittest.mock import patch
from collections import namedtuple

from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.quality.contracts import QualityConfig
from hm_recommender.quality.worker import run

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, '/opt/hmrec/scripts')
from handoff import package_report


def campaign_fixture(artifacts, commit='a'*40):
    artifacts=Path(artifacts); artifacts.mkdir(parents=True,exist_ok=True)
    config=artifacts/'fixture-config.json';atomic_json(config,QualityConfig().model_dump())
    disk=namedtuple('disk_usage','total used free')(100*2**30,20*2**30,80*2**30)
    campaign={'schema_version':1,'protocol':'quality-v1','size':'demo','folds':{}}
    entries=[]
    for fold in ('V1','V2','V3'):
        references={};campaign['folds'][fold]={}
        for stage,role,pool in [('baselines','Q1',None),('graph','Q2',None),('ranker','RH','H'),('ranker','RG','G')]:
            rid='quality-fixture-'+fold.lower()+'-'+role.lower()
            root=artifacts/'data-jobs'/rid;root.mkdir(parents=True)
            env={'source_commit':commit,'source_clean':True,'quality':{'stage':stage,'fold':fold,'size':'demo'}}
            if stage!='baselines':
                ref=references['Q1' if stage=='graph' else 'Q2']
                env['quality_reference']=ref;env['quality']['reference_run_id']=ref['run_id']
            if pool:env['quality']['pool']=pool
            atomic_json(root/'environment.json',env)
            with patch('hm_recommender.quality.worker.shutil.disk_usage',return_value=disk):
                assert run(config,root,verify=False)==0
            result=json.loads((root/'result.json').read_text())
            limits={'memory_bytes':5*2**30,'cpus':3,'polars_threads':2,'omp_threads':2,'mkl_threads':2}
            effective=dict(limits,nano_cpus=3*10**9);effective.pop('cpus')
            env.update(run_id=rid,resources={'name':'base'},limits=limits,effective_limits=effective,
                start_free_bytes=80*2**30,quality_budget_seconds=7200,
                quality_budget={'demo':result['job_duration_seconds'],'full':0,'pilot':0,'summary':0},
                isolation={'network':'none','writable_mounts':['/artifacts/data-jobs/'+rid]})
            atomic_json(root/'environment.json',env)
            report={'schema_version':1,'kind':'quality-handoff','run_id':rid,'machine_id':'fixture','context':'fixture',
                'source_commit':commit,'source_clean':True,'status':'passed','steps':[],'result':result,
                'container_exit_code':0,'oom_killed':False,**{k:env[k] for k in ('resources','limits','effective_limits')}}
            archive=package_report(root,report)
            reference={'run_id':rid,'archive_sha256':sha256(archive),'path':str(archive),'work_path':str(root/'work')}
            if role=='Q2':reference['baseline_reference']=references['Q1']
            references[role]=reference;campaign['folds'][fold][role]=rid
            entries.append(dict(reference,role=role,fold=fold))
    return campaign,entries
