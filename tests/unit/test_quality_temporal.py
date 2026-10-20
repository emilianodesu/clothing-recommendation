from datetime import date, timedelta
import json
import subprocess
import sys

import polars as pl
import pytest

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json


def fixture(root, extra=()):
    root.mkdir()
    rows = [('2020-06-01', 'ancient', f'{i:03}') for i in range(1,25)]
    rows += [('2020-09-08','recent','002')]*2 + [('2020-09-08','recent','001'),
             ('2020-09-01','recent','003'), ('2020-08-01','older','004'),
             ('2020-09-09','recent','002'), ('2020-09-09','recent','099'),
             ('2020-09-10','new','001'), ('2020-09-10','older','004')]
    rows += list(extra)
    tx=pl.DataFrame(rows,schema=['t_dat','customer_id','article_id'],orient='row').with_columns(pl.col('t_dat').str.to_date())
    articles=pl.DataFrame({'article_id':[f'{i:03}' for i in range(1,25)]+['099'],
        'product_type_name':['shirt']*25,'product_group_name':['garment']*25,'colour_group_name':['blue']*25})
    files=[write_partition(tx.to_arrow(),root,'transactions/part.parquet'),
           write_partition(articles.to_arrow(),root,'articles/part.parquet')]
    ds=DatasetManifest(dataset_id='fixture',source_sha256='a'*64,min_date=date(2020,6,1),max_date=date(2020,9,22),
        counts={'transactions':len(tx),'articles':25,'customers':4},partitions=files)
    ds._root=root
    atomic_json(root/'manifest.json',ds.model_dump(mode='json'))
    return ds


def test_future_changes_do_not_change_snapshot_or_ranking(tmp_path):
    from hm_recommender.quality.contracts import QualityConfig, FoldSpec
    from hm_recommender.quality.temporal import build_snapshot, frame
    from hm_recommender.quality.baselines import rank_baseline
    fold=FoldSpec.evaluation('V3'); config=QualityConfig()
    left=build_snapshot(fixture(tmp_path/'left'),fold,tmp_path/'a',config)
    right=build_snapshot(fixture(tmp_path/'right',[('2020-09-16','new','099')]*20),fold,tmp_path/'b',config)
    for name in ('catalog','profiles','histories','popularity'):
        assert frame(left,name).equals(frame(right,name))
    a=rank_baseline(left,['new','recent'],'H',tmp_path/'rank-a')
    b=rank_baseline(right,['new','recent'],'H',tmp_path/'rank-b')
    assert frame(a,'ranking').equals(frame(b,'ranking'))
    assert '099' not in frame(left,'catalog')['article_id']


def test_snapshot_identity_rejects_other_cutoff_and_corruption(tmp_path):
    from hm_recommender.quality.contracts import QualityConfig,FoldSpec
    from hm_recommender.quality.temporal import build_snapshot,frame
    ds=fixture(tmp_path/'ds')
    snap=build_snapshot(ds,FoldSpec.evaluation('V3'),tmp_path/'snapshot',QualityConfig())
    with pytest.raises(ValueError):
        build_snapshot(ds,FoldSpec.evaluation('V2'),tmp_path/'snapshot',QualityConfig())
    (tmp_path/'snapshot'/'catalog.parquet').write_bytes(b'corrupt')
    with pytest.raises(ValueError): frame(snap,'catalog')


def test_test_fold_is_rejected_and_training_windows_derived_from_parent():
    from hm_recommender.quality.contracts import FoldSpec
    with pytest.raises(ValueError): FoldSpec.evaluation('test')
    with pytest.raises(ValueError):
        FoldSpec(fold_id='V3',cutoff=date(2020,9,16),label_end=date(2020,9,23))
    first=FoldSpec.training('V1',4)
    assert first.cutoff==date(2020,7,29)
    assert first.label_end==date(2020,8,5)
    with pytest.raises(ValueError): FoldSpec.training('V1',0)


def test_history_dates_frequency_and_id_ties_and_small_catalog(tmp_path):
    from hm_recommender.quality.contracts import QualityConfig,FoldSpec
    from hm_recommender.quality.temporal import build_snapshot,frame,read_labels
    from hm_recommender.quality.baselines import rank_baseline
    from hm_recommender.quality.evaluate import evaluate_ranking
    ds=fixture(tmp_path/'ds'); snap=build_snapshot(ds,FoldSpec.evaluation('V3'),tmp_path/'snapshot',QualityConfig())
    rankings={m:rank_baseline(snap,['new','older','recent'],m,tmp_path/m) for m in ('P','H')}
    h=frame(rankings['H'],'ranking'); p=frame(rankings['P'],'ranking')
    assert h.filter(pl.col('customer_id')=='recent')['article_id'].to_list()[:3]==['002','001','003']
    assert h.filter(pl.col('customer_id')=='new')['article_id'].to_list()==p.filter(pl.col('customer_id')=='new')['article_id'].to_list()
    assert len(h)==72 and h.unique(['customer_id','article_id']).height==72
    labels=read_labels(ds,FoldSpec.evaluation('V3'))
    result=evaluate_ranking(rankings['H'],labels,snap,tmp_path/'eval')
    assert result.population['users']==3 and result.population['catalog_size']==24
    assert result.metrics['recall_at_200']==pytest.approx(5/6)
    assert result.metrics['map_at_12']==pytest.approx((.5+.5+.25)/3)
    assert result.segments['recent28d']['users']==1
    assert result.segments['history29_90d']['users']==1
    assert result.segments['no_history']['users']==1
    assert result.denominators['relevant_items']==4
    assert result.denominators['out_of_catalog_relevant_items']==1
    assert frame(result,'metrics').height==3


def test_baselines_import_without_torch():
    output=subprocess.check_output([sys.executable,'-c','import sys; import hm_recommender.quality.baselines; print("torch" in sys.modules)'],text=True)
    assert output.strip()=='False'
