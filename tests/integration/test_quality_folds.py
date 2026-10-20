import numpy as np
import polars as pl
import pytest

from hm_recommender.model.demo import prepare_demo
from hm_recommender.data.ingest import load_dataset,scan
from hm_recommender.model.recent_history import recent_histories,compose_recent
from hm_recommender.model.diagnostics import recent_popular
from hm_recommender.quality.contracts import QualityConfig,FoldSpec
from hm_recommender.quality.temporal import build_snapshot,frame,read_labels
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.evaluate import evaluate_ranking


def test_legacy_v3_exact_ranking_and_all_three_folds(tmp_path):
    prepare_demo(tmp_path/'demo'); ds=load_dataset(tmp_path/'demo/dataset')
    for name, buyers in [('V1',32),('V2',32),('V3',33)]:
        fold=FoldSpec.evaluation(name); snap=build_snapshot(ds,fold,tmp_path/name,QualityConfig())
        labels=read_labels(ds,fold); customers=labels.select('customer_id').unique().sort('customer_id').collect()['customer_id'].to_list()
        assert len(customers)==buyers
        p=rank_baseline(snap,customers,'P',tmp_path/(name+'-P'))
        h=rank_baseline(snap,customers,'H',tmp_path/(name+'-H'))
        result=evaluate_ranking(h,labels,snap,tmp_path/(name+'-eval'))
        if name=='V3':
            catalog=frame(snap,'catalog'); ids=catalog['article_id'].to_numpy()
            popular=recent_popular(scan(ds),catalog,7)
            histories=recent_histories(scan(ds),catalog,customers)
            actual=frame(h,'ranking').group_by('customer_id',maintain_order=True).agg('article_id')['article_id'].to_list()
            expected=[ids[compose_recent(v,popular,k=24)].tolist() for v in histories]
            assert actual==expected
            assert frame(p,'ranking')['article_id'].to_list()==ids[popular].tolist()*buyers
            assert result.metrics['map_at_12']==pytest.approx((31+2/3+1/5)/33,abs=1e-10)
            assert result.metrics['recall_at_200']==pytest.approx((32+2/3)/33,abs=1e-10)
