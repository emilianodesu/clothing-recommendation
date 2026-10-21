from datetime import date
import json
from pathlib import Path
import sys

import polars as pl
import pytest

from hm_recommender.model.demo import prepare_demo_dataset
from hm_recommender.data.io import atomic_json
from hm_recommender.quality.contracts import FoldSpec, QualityConfig
from hm_recommender.quality.temporal import build_snapshot, read_labels, frame
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.evaluate import evaluate_ranking
from hm_recommender.releases.package import build_history_release, verify_history_release
from hm_recommender.releases.parity import verify_parity


def test_unfiltered_parity_with_frozen_diagnostic(tmp_path,monkeypatch):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT','a'*40)
    ds=prepare_demo_dataset(tmp_path/'demo')
    fold=FoldSpec.evaluation('V3');snap=build_snapshot(ds,fold,tmp_path/'snapshot',QualityConfig())
    labels=read_labels(ds,fold).collect();buyers=labels['customer_id'].unique().sort().to_list()
    reference=rank_baseline(snap,buyers,'H',tmp_path/'reference')
    expected=evaluate_ranking(reference,labels.lazy(),snap,tmp_path/'expected')
    release=build_history_release(ds,date(2020,9,9),tmp_path/'release')
    result=verify_parity(release,ds,snap,labels,reference,expected,tmp_path/'parity')
    assert result['buyers']==33 and result['catalog_size']==24
    assert result['exact_top200'] and result['legacy_ranking_verified']
    assert result['metrics']['map_at_12']==pytest.approx((31+2/3+1/5)/33,abs=1e-10,rel=0)
    assert result['metrics']['recall_at_200']==pytest.approx((32+2/3)/33,abs=1e-10,rel=0)
    assert result['out_of_catalog_relevant_items']==1
    assert result['unknown_buyers']==1
    assert release.files['customers'].rows==32
    # Changed private reference cannot be replaced with its aggregate report.
    (reference._root/'ranking.parquet').write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        verify_parity(release,ds,snap,labels,reference,expected,tmp_path/'bad')
    later=build_history_release(ds,date(2020,9,10),tmp_path/'later')
    with pytest.raises(ValueError):
        verify_parity(later,ds,snap,labels,reference,expected,tmp_path/'wrong-cutoff')


def test_release_worker_returns_aggregate_only_and_preserves_failed_attempt(tmp_path):
    from hm_recommender.releases.worker import run
    from hm_recommender.releases.package import configuration
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    sys.path.insert(0,'/opt/hmrec/scripts')
    from release_handoff import validate_release_result
    config=tmp_path/'config.json';atomic_json(config,configuration())
    root=tmp_path/'run';root.mkdir()
    atomic_json(root/'environment.json',{'source_commit':'a'*40,'dataset':'synthetic',
        'release':{'data_as_of':'2020-09-09'}})
    assert run(config,root,verify=False)==0
    result=json.loads((root/'result.json').read_text())
    validate_release_result(result,configuration())
    assert result['test_evaluated'] is False and result['activated'] is False
    assert result['parity']['buyers']==33
    assert 'customer_id' not in json.dumps(result) and 'article_id' not in json.dumps(result)
    assert verify_history_release(root/'work/release').release_id==result['package']['release_id']
    failed=tmp_path/'failed';failed.mkdir()
    atomic_json(failed/'environment.json',{'source_commit':'a'*40,'dataset':'synthetic',
        'release':{'data_as_of':'2020-09-16'}})
    assert run(config,failed,verify=False)==1
    assert json.loads((failed/'result.json').read_text())['status']=='failed'
    assert not (failed/'work/release').exists()
