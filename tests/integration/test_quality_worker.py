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
    # The scientific fixture lives in Docker's small /tmp tmpfs. Supply its
    # external disk observation explicitly; the detached demo checks real storage.
    from unittest.mock import patch
    from collections import namedtuple
    disk=namedtuple('disk_usage','total used free')
    with patch('hm_recommender.quality.worker.shutil.disk_usage',return_value=disk(100*2**30,20*2**30,80*2**30)):
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


def test_supervisor_checks_limits_when_worker_finishes_between_polls(tmp_path):
    import json
    from hm_recommender.quality.supervisor import supervise
    from unittest.mock import patch
    from collections import namedtuple
    disk=namedtuple('disk_usage','total used free')
    for reason in ('timeout','disk'):
        root=tmp_path/reason;root.mkdir()
        command=[sys.executable,'-c',"import time,json,pathlib; time.sleep(.08); pathlib.Path("+repr(str(root/'result.json'))+").write_text(json.dumps({'status':'passed'}))"]
        with patch('hm_recommender.quality.supervisor.shutil.disk_usage',side_effect=[disk(100,0,100),disk(100,0,100),disk(100,99,1)]):
            code=supervise(command,root,.05 if reason=='timeout' else 10,0 if reason=='timeout' else 5,interval=.2)
        result=json.loads((root/'result.json').read_text())
        assert code!=0 and result['status']=='failed' and result['failure_reason']==reason
