from datetime import date
import numpy as np
import polars as pl
import pytest

from hm_recommender.contracts import DatasetManifest
from hm_recommender.data.ingest import write_partition
from hm_recommender.data.io import atomic_json
from hm_recommender.quality.contracts import QualityConfig, FoldSpec
from hm_recommender.quality.temporal import build_snapshot, frame


def graph_fixture(root, rows, n=60):
    root.mkdir()
    rows=[('2020-01-01','catalog',f'{i:03}') for i in range(n)]+rows
    tx=pl.DataFrame(rows,schema=['t_dat','customer_id','article_id'],orient='row').with_columns(pl.col('t_dat').str.to_date())
    articles=pl.DataFrame({'article_id':[f'{i:03}' for i in range(n)],'product_type_name':['shirt']*n,
        'product_group_name':['garment']*n,'colour_group_name':['blue']*n})
    files=[write_partition(tx.to_arrow(),root,'transactions/part.parquet'),write_partition(articles.to_arrow(),root,'articles/part.parquet')]
    ds=DatasetManifest(dataset_id='graph-fixture',source_sha256='a'*64,min_date=date(2020,1,1),max_date=date(2020,9,22),
        counts={'transactions':len(tx),'articles':n,'customers':100},partitions=files)
    ds._root=root;atomic_json(root/'manifest.json',ds.model_dump(mode='json'))
    return ds


def make_graph(root,rows,n=60):
    from hm_recommender.graph.build import build_graph
    ds=graph_fixture(root/'ds',rows,n)
    snap=build_snapshot(ds,FoldSpec.evaluation('V3'),root/'snapshot',QualityConfig())
    return snap,build_graph(ds,snap,QualityConfig(),root/'graph')


def test_hand_weights_dedup_isolates_and_no_self_loops(tmp_path):
    from hm_recommender.graph.build import load_csr
    rows=[('2020-09-08','u','000')]*3+[('2020-09-08','u','001'),('2020-09-08','v','000'),('2020-09-08','v','002')]
    _,g=make_graph(tmp_path,rows)
    m=load_csr(g)
    assert m.shape==(60,60) and m.nnz==4
    assert m[0,1]==pytest.approx(1/np.sqrt(2)) and m[0,2]==pytest.approx(1/np.sqrt(2))
    assert m.diagonal().sum()==0 and m[59].nnz==0
    assert frame(g,'nodes')['support'].to_list()[:3]==[2,1,1]
    assert g.statistics['baskets']==2 and g.statistics['retained_memberships']==4
    assert g.statistics['temporary_peak_bytes']>0


def test_basket_cap_and_outdegree(tmp_path):
    from hm_recommender.graph.build import load_csr
    rows=[('2020-09-08','large',f'{i:03}') for i in range(51)]
    rows += [('2020-09-08',f'u{i}',a) for i in range(1,60) for a in ('000',f'{i:03}')]
    _,g=make_graph(tmp_path,rows)
    m=load_csr(g)
    assert g.statistics['capped_baskets']==1 and g.statistics['discarded_memberships']==1
    assert max(np.diff(m.indptr))==50
    assert frame(g,'nodes')['support'][50]==1  # excluded from oversized tied basket


def test_graph_ignores_future_baskets(tmp_path):
    from hm_recommender.graph.build import load_csr
    rows=[('2020-09-08','u','000'),('2020-09-08','u','001')]
    for name in ('a','b'): (tmp_path/name).mkdir()
    _,a=make_graph(tmp_path/'a',rows)
    _,b=make_graph(tmp_path/'b',rows+[('2020-09-09','x','002'),('2020-09-20','x','003')])
    x,y=load_csr(a),load_csr(b)
    assert np.array_equal(x.indptr,y.indptr) and np.array_equal(x.indices,y.indices) and np.array_equal(x.data,y.data)
    assert frame(a,'seeds').equals(frame(b,'seeds'))


def test_empty_90_day_window_keeps_isolated_catalog(tmp_path):
    from hm_recommender.graph.build import load_csr
    _,g=make_graph(tmp_path,[])
    assert load_csr(g).nnz==0 and g.statistics['isolated_nodes']==60
    assert g.statistics['baskets']==0 and frame(g,'seeds').is_empty()
