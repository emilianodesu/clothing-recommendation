import json
from pathlib import Path
import sys

import pytest
from hm_recommender.data.io import atomic_json


def test_quality_worker_produces_private_artifacts_and_valid_aggregate(tmp_path):
    from hm_recommender.quality.worker import run
    from hm_recommender.quality.contracts import QualityConfig
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    from quality_handoff import validate_quality_result
    root=tmp_path/'run';root.mkdir()
    atomic_json(root/'environment.json',{'source_commit':'a'*40,'quality':{'stage':'baselines','fold':'V3','size':'demo'}})
    config=tmp_path/'quality.json';atomic_json(config,QualityConfig().model_dump())
    assert run(config,root,verify=False)==0
    result=json.loads((root/'result.json').read_text());validate_quality_result(result)
    assert result['parity']['legacy_ranking_verified']
    assert result['population']['users']==33
    assert result['methods']['H']['metrics']['map_at_12']==pytest.approx((31+2/3+1/5)/33)
    assert (root/'work/evaluation-H/metrics.parquet').exists()
    assert 'customer_id' not in json.dumps(result)
    assert result['test_evaluated'] is False
    from copy import deepcopy
    for path,value in [(('fold','cutoff'),'2020-09-16'),(('methods','H','metrics','map_at_12'),float('nan')),
                       (('methods','P','population','users'),32),(('methods','H','segments'),{}),
                       (('test_evaluated',),True),(('verification_exit_code',),1),
                       (('parity','legacy_ranking_verified'),False)]:
        broken=deepcopy(result);obj=broken
        for key in path[:-1]:obj=obj[key]
        obj[path[-1]]=value
        with pytest.raises(ValueError):validate_quality_result(broken)


def test_supervisor_timeout_and_disk_keep_failed_result(tmp_path):
    from hm_recommender.quality.supervisor import supervise
    for name,seconds,free in [('timeout',.1,0),('disk',10,10**30)]:
        root=tmp_path/name;root.mkdir()
        code=supervise([sys.executable,'-c','import time; time.sleep(30)'],root,seconds,free,interval=.02)
        assert code!=0
        result=json.loads((root/'result.json').read_text())
        assert result['status']=='failed' and result['failure_reason']==name


def test_supervisor_worker_death_not_success(tmp_path):
    from hm_recommender.quality.supervisor import supervise
    code=supervise([sys.executable,'-c','import os; os._exit(7)'],tmp_path,10,0,interval=.02)
    assert code==7
    assert json.loads((tmp_path/'result.json').read_text())['status']=='failed'
