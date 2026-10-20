from copy import deepcopy
import json
from pathlib import Path
import sys

import polars as pl
import pytest

from hm_recommender.quality.contracts import QualityConfig


@pytest.fixture(scope='module')
def source_campaign(tmp_path_factory):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from quality_fixture import campaign_fixture
    root=tmp_path_factory.mktemp('campaign')
    return root,*campaign_fixture(root)


def test_summary_reconciles_private_evidence_and_never_retrains(source_campaign,tmp_path,monkeypatch):
    from hm_recommender.quality import compare
    assert hasattr(compare,'compare_campaign'), 'Q4 artifact comparison is not implemented'
    root,campaign,entries=source_campaign
    # Any attempt to rebuild source data or fit a model must fail this summary.
    def forbidden(*args,**kwargs):raise AssertionError('Summary must read existing evaluations only')
    monkeypatch.setattr('hm_recommender.quality.worker.load_dataset',forbidden)
    monkeypatch.setattr('hm_recommender.reranking.train.fit_ranker',forbidden)
    result=compare.compare_campaign(entries,QualityConfig(),tmp_path,size='demo')
    assert result['comparison_complete'] and not result['selection_finalized']
    assert result['bootstrap']['unique_buyers']==33
    assert result['bootstrap']['observations']==97
    assert result['bootstrap']['fold_buyers']=={'V1':32,'V2':32,'V3':33}
    assert set(result['bootstrap']['contrasts'])=={'G','RH','RG'}
    assert len(result['evaluations'])==15
    assert 'customer_id' not in json.dumps(result)
    assert not result['test_evaluated'] and not result['quality_accepted']
    assert result['sources'][0]['archive_sha256']==entries[0]['archive_sha256']
    # Omitted candidate stays incomplete and cannot be promoted.
    partial=[e for e in entries if not (e['role']=='RG' and e['fold']=='V2')]
    out=compare.compare_campaign(partial,QualityConfig(),tmp_path/'partial',size='demo')
    assert not out['comparison_complete']
    assert not out['decision']['candidates']['RG']['eligible']
    assert 'RG' not in out['bootstrap']['contrasts']


def test_summary_rejects_changed_archive_private_metric_and_wrong_ancestry(source_campaign,tmp_path):
    from hm_recommender.quality import compare
    assert hasattr(compare,'compare_campaign'), 'Q4 artifact verification is not implemented'
    _,_,entries=source_campaign
    bad=deepcopy(entries);bad[0]['archive_sha256']='0'*64
    with pytest.raises(ValueError):compare.compare_campaign(bad,QualityConfig(),tmp_path/'archive',size='demo')
    bad=deepcopy(entries);bad[1]['run_id']=entries[5]['run_id']
    with pytest.raises(ValueError):compare.compare_campaign(bad,QualityConfig(),tmp_path/'ancestry',size='demo')
    target=Path(entries[-1]['work_path'])/'evaluation-RG'
    manifest=json.loads((target/'manifest.json').read_text());p=target/manifest['files']['metrics']['path']
    original=p.read_bytes()
    try:
        pl.read_parquet(p).with_columns(pl.lit(0.).alias('map_at_12')).write_parquet(p)
        with pytest.raises(ValueError):compare.compare_campaign(entries,QualityConfig(),tmp_path/'corrupt',size='demo')
    finally:p.write_bytes(original)


def test_summary_worker_and_report_contract(source_campaign,tmp_path):
    from unittest.mock import patch
    from collections import namedtuple
    from hm_recommender.quality.worker import run
    from hm_recommender.data.io import atomic_json
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    from quality_handoff import validate_quality_result,validate_quality_execution
    from handoff import package_report,read_report
    root,campaign,entries=source_campaign
    # The summary dispatcher must not attempt a fold or dataset lookup.
    env={'source_commit':'a'*40,'quality':{'stage':'summary','size':'summary'},
        'quality_campaign':{'declaration':campaign,'entries':entries}}
    atomic_json(tmp_path/'environment.json',env)
    config=tmp_path/'config.json';atomic_json(config,QualityConfig().model_dump())
    # Component fixture writes to Compose tmpfs, not the production artifact mount.
    disk=namedtuple('disk_usage','total used free')(100*2**30,20*2**30,80*2**30)
    with patch('hm_recommender.quality.summary_worker.shutil.disk_usage',return_value=disk):
        assert run(config,tmp_path,verify=False)==0
    result=json.loads((tmp_path/'result.json').read_text())
    validate_quality_result(result)
    assert result['stage']=='Q4' and result['size']=='summary'
    assert not result['selection_finalized']
    for keys,value in [(('bootstrap','cluster'),'row'),(('bootstrap','family_confidence'),.95),
            (('bootstrap','resamples'),100), (('decision','selected_method'),'RG'),
            (('evaluations',0,'metrics_bytes_verified'),False), (('sources',0,'archive_sha256'),'bad'),
            (('selection_finalized',),True), (('test_evaluated',),True)]:
        broken=deepcopy(result);target=broken
        for key in keys[:-1]:target=target[key]
        target[keys[-1]]=value
        with pytest.raises(ValueError):validate_quality_result(broken)
    # The real ZIP envelope keeps only aggregates and identities, never tables.
    rid='quality-summary-fixture'
    transported=deepcopy(entries)
    for entry in transported:
        entry['path']='/artifacts/handoff/'+entry['run_id']+'.zip'
        entry['work_path']='/artifacts/data-jobs/'+entry['run_id']+'/work'
    limits={'memory_bytes':5*2**30,'cpus':3,'polars_threads':2,'omp_threads':2,'mkl_threads':2}
    effective=dict(limits,nano_cpus=3*10**9);effective.pop('cpus')
    env.update(run_id=rid,limits=limits,effective_limits=effective,resources={'name':'base'},
        start_free_bytes=20*2**30,quality_budget_seconds=1800,
        quality_budget={'demo':0,'pilot':0,'full':0,'summary':result['job_duration_seconds']},
        isolation={'network':'none','writable_mounts':['/artifacts/data-jobs/'+rid]})
    env['quality_campaign']['entries']=transported
    validate_quality_execution(result,env)
    bad=deepcopy(env);bad['quality_budget_seconds']=1801
    with pytest.raises(ValueError):validate_quality_execution(result,bad)
    bad=deepcopy(env);bad['quality_campaign']['entries'][0]['archive_sha256']='0'*64
    with pytest.raises(ValueError):validate_quality_execution(result,bad)
    atomic_json(tmp_path/'environment.json',env)
    report={'schema_version':1,'kind':'quality-handoff','run_id':rid,'machine_id':'fixture','context':'fixture',
        'source_commit':'a'*40,'source_clean':True,'status':'passed','steps':[],'result':result,
        'container_exit_code':0,'oom_killed':False,**{k:env[k] for k in ('resources','limits','effective_limits')}}
    archive=package_report(tmp_path,report)
    assert read_report(archive)['result']==result
