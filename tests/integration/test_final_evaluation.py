"""Only synthetic transactions: catches leakage and denominator regressions."""
import csv
import io
from datetime import date
from zipfile import ZipFile

import pytest

from hm_recommender.data.ingest import ingest
from hm_recommender.data.validate import SCHEMAS
from hm_recommender.quality.contracts import FoldSpec


def fixture(root, recent_b=False):
    root.mkdir()
    tx = [('2020-09-15','recent','A'), ('2020-08-01','old','B'),
          ('2020-05-01','ancient','B'), ('2020-09-15','inactive','A'),
          ('2020-09-16','recent','A'), ('2020-09-16','recent','A'),
          ('2020-09-22','recent','C'), ('2020-09-16','new','B'),
          ('2020-09-22','old','B'), ('2020-09-22','ancient','B'),
          ('2020-09-23','excluded','D')]
    if recent_b:
        tx[0]=('2020-09-14','recent','A')
        tx.append(('2020-09-15','recent','B'))
    users=sorted({u for _,u,_ in tx})
    rows={'customers.csv':[dict(customer_id=u) for u in users],
          'sample_submission.csv':[dict(customer_id=u,prediction='A B') for u in users],
          'articles.csv':[dict(article_id=a,product_type_name='t',product_group_name='g',colour_group_name='c') for a in 'ABCD'],
          'transactions_train.csv':[dict(t_dat=d,customer_id=u,article_id=a,price='.1',sales_channel_id='1') for d,u,a in tx]}
    path=root/'synthetic.zip'
    with ZipFile(path,'w') as z:
        for name,values in rows.items():
            s=io.StringIO(); w=csv.DictWriter(s,fieldnames=SCHEMAS[name]); w.writeheader(); w.writerows(values); z.writestr(name,s.getvalue())
    return ingest(path,root/'dataset',expected=None)


def test_final_evaluator_temporal_population_and_denominators(tmp_path):
    from hm_recommender.quality import final
    ds=fixture(tmp_path/'input')
    result=final.evaluate_final(ds,tmp_path/'output')
    assert result['protocol']=='final-hp-v1'
    for method in ('H','P'):
        r=result['evaluations'][method]
        assert r['population']=={'users':4,'catalog_size':2}
        assert r['denominators']['relevant_items']==5
        assert r['denominators']['out_of_catalog_relevant_items']==1
        assert r['metrics']['recall_at_200']==pytest.approx(.875)
        assert r['metrics']['map_at_12']==pytest.approx(.5)
        assert {k:v['users'] for k,v in r['segments'].items()}==dict(recent28d=1,history29_90d=1,older_history=1,no_history=1)
    assert result['snapshot_counts']['profiles']==4  # includes inactive, excludes new/excluded
    assert result['snapshot_counts']['catalog']==2  # excludes C at cutoff and D after end
    assert result['selected_method']=='H'
    with pytest.raises(FileExistsError):
        final.evaluate_final(ds,tmp_path/'output')  # no automatic repeat/resume


def test_quality_campaign_still_rejects_test():
    with pytest.raises(ValueError):
        FoldSpec(fold_id='TEST',cutoff=date(2020,9,16),label_end=date(2020,9,23))
    with pytest.raises(ValueError):
        FoldSpec.evaluation('TEST')


def test_final_window_cannot_be_retuned_and_quality_builder_stays_closed(tmp_path):
    from hm_recommender.quality.final import FinalWindow
    from hm_recommender.quality.contracts import QualityConfig
    from hm_recommender.quality.temporal import build_snapshot
    with pytest.raises(ValueError):
        FinalWindow(cutoff=date(2020,9,15))
    ds=fixture(tmp_path/'input')
    with pytest.raises(ValueError):
        build_snapshot(ds,FinalWindow(),tmp_path/'forbidden',QualityConfig())
    assert not (tmp_path/'forbidden').exists()


def test_final_h_preserves_history_order_and_p_popularity_order(tmp_path):
    from hm_recommender.quality.final import FinalWindow, FinalSnapshot, FinalRanking
    from hm_recommender.quality.contracts import QualityConfig
    from hm_recommender.quality.temporal import build_snapshot, frame
    from hm_recommender.quality.baselines import rank_baseline
    ds=fixture(tmp_path/'input',recent_b=True)
    # At the fixed cutoff, old/ancient have no recent history and use popularity.
    snapshot=build_snapshot(ds,FinalWindow(),tmp_path/'snap',QualityConfig(),artifact_type=FinalSnapshot)
    for method in ('H','P'):
        ranking=rank_baseline(snapshot,['new','old','recent'],method,tmp_path/method,artifact_type=FinalRanking)
        assert frame(ranking,'ranking')['article_id'].to_list()==['A','B']*2+(['B','A'] if method=='H' else ['A','B'])
    assert frame(snapshot,'histories')['customer_id'].to_list()==['inactive','recent','recent']


def test_supervisor_closes_once_with_private_synthetic_child(tmp_path):
    import json
    from hm_recommender.data.io import sha256
    from hm_recommender.quality.final_worker import supervise
    ds=fixture(tmp_path/'input');root=tmp_path/'worker';root.mkdir()
    (root/'config.json').write_text(json.dumps(dict(dataset_dir=str(ds._root),source_sha256=ds.source_sha256,dataset_manifest_sha256=sha256(ds._root/'manifest.json'))))
    (root/'go.json').write_text('{}')
    result=supervise(root,timeout=30)
    assert result['exit_code']==0 and result['error_type'] is None
    assert json.loads((root/'evaluation-result.json').read_text())['selected_method']=='H'
    assert (root/'complete.json').exists()


def test_supervisor_timeout_keeps_completion_and_kills_child(tmp_path):
    from unittest.mock import patch,Mock
    import subprocess
    from hm_recommender.quality.final_worker import supervise
    (tmp_path/'go.json').write_text('{}')
    child=Mock();child.wait.side_effect=[subprocess.TimeoutExpired('child',1),subprocess.TimeoutExpired('child',10),-9];child.returncode=-9
    with patch('hm_recommender.quality.final_worker.subprocess.Popen',return_value=child):
        result=supervise(tmp_path,timeout=1)
    child.terminate.assert_called_once();child.kill.assert_called_once()
    assert result['exit_code']==-9 and result['error_type']=='TimeoutError'
    assert (tmp_path/'complete.json').exists()


def test_supervisor_never_marks_complete_with_live_child_after_error(tmp_path):
    from unittest.mock import patch,Mock
    from hm_recommender.quality.final_worker import supervise
    (tmp_path/'go.json').write_text('{}')
    child=Mock();child.wait.side_effect=[RuntimeError('observer interrupted'),0];child.poll.return_value=None
    with patch('hm_recommender.quality.final_worker.subprocess.Popen',return_value=child):
        result=supervise(tmp_path,timeout=1)
    child.terminate.assert_called_once()
    assert result['error_type']=='RuntimeError'
