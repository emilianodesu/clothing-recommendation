from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import polars as pl
import pytest

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.ranking.history import rank_history
from hm_recommender.releases.package import build_history_release, verify_history_release, release_identity


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT', 'a'*40)
    root=tmp_path/'data'; root.mkdir()
    events=[
        ('old-only','a','2020-07-01'), ('buyer','b','2020-08-12'),
        ('buyer','a','2020-08-11'), ('buyer','c','2020-09-08'),
        ('buyer','c','2020-09-07'), ('buyer','d','2020-09-08'),
        ('buyer','e','2020-09-08'), ('future','f','2020-09-09'),
        ('buyer','a','2020-09-09')]
    tx=pl.DataFrame(events,schema=['customer_id','article_id','t_dat'],orient='row').with_columns(pl.col('t_dat').str.to_date())
    catalog=pl.DataFrame({'article_id':list('abcdef'),'product_type_name':['shirt']*6,
        'product_group_name':['garment']*6,'colour_group_name':['blue']*6})
    files=[write_partition(tx.to_arrow(),root,'transactions/part.parquet'),
        write_partition(catalog.to_arrow(),root,'articles/part.parquet')]
    ds=DatasetManifest(dataset_id='synthetic',source_sha256='b'*64,min_date=date(2020,7,1),
        max_date=date(2020,9,9),counts={'transactions':9},partitions=files)
    ds._root=root; atomic_json(root/'manifest.json',ds.model_dump(mode='json'))
    return ds


def test_history_rank_matches_fixed_fixture():
    assert rank_history(['c','c','d','b'],['c','d','a','b','e'],set('abcde'))==list('cdbae')
    assert rank_history([],list('cdabe'),set('abcde'))==list('cdabe')
    assert rank_history(['x','b','c'],list('adbc'),set('abc'),limit=2)==['b','c']
    assert rank_history(['a'],['b'],set())==[]
    assert rank_history([],list(map(str,range(250))),set(map(str,range(250))))==list(map(str,range(200)))
    for limit in (0,-1,201,True):
        with pytest.raises(ValueError):rank_history([],[],set(),limit=limit)


def test_cutoff_excludes_future_events(dataset,tmp_path):
    release=build_history_release(dataset,date(2020,9,9),tmp_path/'release')
    assert release.schema_version==2
    assert pl.read_parquet(tmp_path/'release/catalog.parquet')['article_id'].to_list()==list('abcde')
    history=pl.read_parquet(tmp_path/'release/histories.parquet')
    assert history['article_id'].to_list()==list('cdeb')
    assert history['frequency'].to_list()==[2,1,1,1]
    assert pl.read_parquet(tmp_path/'release/popularity.parquet')['article_id'].to_list()==list('cdeab')
    assert verify_history_release(tmp_path/'release')==release
    ref=json.loads((tmp_path/'release/validation-reference.json').read_text())
    assert ref['validation_cutoff']=='2020-09-09'
    assert ref['metrics_measured_for_this_package'] is False
    later=build_history_release(dataset,date(2020,9,10),tmp_path/'later')
    assert later.data_as_of==date(2020,9,10)
    assert json.loads((tmp_path/'later/validation-reference.json').read_text())['validation_cutoff']=='2020-09-09'


def test_build_includes_non_validation_buyers(dataset,tmp_path):
    release=build_history_release(dataset,date(2020,9,9),tmp_path/'release')
    assert pl.read_parquet(tmp_path/'release/customers.parquet')['customer_id'].to_list()==['buyer','old-only']
    assert release.files['customers'].rows==2
    assert release.files['histories'].rows==4


def test_incomplete_or_corrupt_release_not_publishable(dataset,tmp_path):
    root=tmp_path/'release'
    build_history_release(dataset,date(2020,9,9),root)
    original=(root/'manifest.json').read_bytes()
    with pytest.raises(ValueError):build_history_release(dataset,date(2020,9,8),root)
    assert (root/'manifest.json').read_bytes()==original
    (root/'catalog.parquet').write_bytes(b'broken')
    with pytest.raises(ValueError):verify_history_release(root)
    with pytest.raises(ValueError):build_history_release(dataset,date(2020,9,9),root)
    incomplete=tmp_path/'incomplete';incomplete.mkdir()
    (incomplete/'catalog.parquet').write_bytes(b'partial')
    with pytest.raises(ValueError):build_history_release(dataset,date(2020,9,9),incomplete)
    assert not (incomplete/'manifest.json').exists()


def test_schema1_not_read_as_schema2(dataset,tmp_path):
    root=tmp_path/'release';build_history_release(dataset,date(2020,9,9),root)
    payload=json.loads((root/'manifest.json').read_text());payload['schema_version']=1
    atomic_json(root/'manifest.json',payload)
    with pytest.raises(ValueError):verify_history_release(root)


@pytest.mark.parametrize('mutation',['traversal','symlink','extra','rows','order','duplicate','future'])
def test_release_paths_are_relative_and_semantics_verified(dataset,tmp_path,mutation):
    root=tmp_path/'release';build_history_release(dataset,date(2020,9,9),root)
    payload=json.loads((root/'manifest.json').read_text())
    if mutation=='traversal':payload['files']['catalog']['path']='../data/articles/part.parquet'
    elif mutation=='symlink':
        target=root/'catalog.parquet'; target.rename(tmp_path/'outside.parquet');target.symlink_to(tmp_path/'outside.parquet')
    elif mutation=='extra':(root/'untracked.txt').write_text('unexpected')
    elif mutation=='rows':payload['files']['histories']['rows']+=1
    else:
        target=root/'histories.parquet';table=pl.read_parquet(target)
        if mutation=='order':table=table.reverse()
        elif mutation=='duplicate':table=pl.concat([table,table.head(1)])
        else:table=table.with_columns(pl.lit(date(2020,9,9)).alias('last_purchase'))
        table.write_parquet(target)
        payload['files']['histories'].update(sha256=sha256(target),size_bytes=target.stat().st_size,rows=len(table))
    payload['release_id']=release_identity(payload)
    atomic_json(root/'manifest.json',payload)
    with pytest.raises(ValueError):verify_history_release(root)


def test_atomic_publication_keeps_failure_unpublished(dataset,tmp_path,monkeypatch):
    import hm_recommender.releases.package as package
    def fail(*args,**kwargs):raise OSError('disk failure')
    monkeypatch.setattr(package,'verify_history_release',fail)
    with pytest.raises(OSError):build_history_release(dataset,date(2020,9,9),tmp_path/'release')
    assert not (tmp_path/'release').exists()


def test_ranking_core_does_not_import_torch():
    code="import sys; from hm_recommender.ranking.history import rank_history; assert 'torch' not in sys.modules; assert rank_history([], ['a'], {'a'}) == ['a']"
    subprocess.run([sys.executable,'-c',code],check=True)
