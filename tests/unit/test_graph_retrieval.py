import polars as pl
from test_graph_weights import make_graph
from hm_recommender.quality.temporal import frame
from hm_recommender.quality.baselines import rank_baseline


def test_graph_fallback_equals_H(tmp_path):
    from hm_recommender.graph.retrieve import rank_graph
    snap,g=make_graph(tmp_path,[('2020-09-08','solo','000')])
    users=['absent','solo']
    h=rank_baseline(snap,users,'H',tmp_path/'h');r=rank_graph(snap,g,users,tmp_path/'g')
    assert frame(h,'ranking').select('customer_id','rank','article_id').equals(frame(r,'ranking').select('customer_id','rank','article_id'))


def test_old_history_can_supply_seeds(tmp_path):
    from hm_recommender.graph.retrieve import rank_graph, RetrievalContext
    rows=[('2020-08-01','old','000'),('2020-08-01','other','000'),('2020-08-01','other','059')]
    snap,g=make_graph(tmp_path,rows)
    ctx=RetrievalContext(snap,g,['old'])
    assert ctx.history('old')==[] and ctx.neighbors('old')==[59]
    r=rank_graph(snap,g,['old'],tmp_path/'r')
    assert frame(r,'ranking')['article_id'][0]=='059'


def test_candidate_union_is_label_independent(tmp_path):
    from hm_recommender.graph.retrieve import rank_graph
    rows=[('2020-09-08','u','000'),('2020-09-08','u','059')]
    for name in ('a','b'):(tmp_path/name).mkdir()
    a,ga=make_graph(tmp_path/'a',rows+[('2020-09-10','u','001')])
    b,gb=make_graph(tmp_path/'b',rows+[('2020-09-10','u','002')]*20)
    ra=rank_graph(a,ga,['u'],tmp_path/'ra');rb=rank_graph(b,gb,['u'],tmp_path/'rb')
    assert frame(ra,'ranking').equals(frame(rb,'ranking'))


def test_graph_prefix_deduplicates_and_caps_200():
    from hm_recommender.graph.retrieve import compose_graph
    prefix=list(range(5))+[0]; neighbors=list(range(300,0,-1));popular=list(range(400))
    result=compose_graph(prefix,neighbors,popular,200)
    assert result[:5]==list(range(5)) and len(result)==len(set(result))==200
    assert compose_graph(prefix,[],popular,200)==list(range(200))
