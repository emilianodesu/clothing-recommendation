import numpy as np
from scipy.sparse import csr_matrix
from hm_recommender.quality.contracts import QualityConfig


def test_pagerank_matches_independent_linear_solution_with_dangling():
    from hm_recommender.graph.pagerank import power_pagerank
    a=np.array([[0,1,2,0],[0,0,1,0],[1,0,0,0],[0,0,0,0]],dtype=float)
    restart=np.array([.25,0,0,.75]); sums=a.sum(axis=1)
    transition=np.array([row/s if s else restart for row,s in zip(a,sums)])
    expected=np.linalg.solve(np.eye(4)-.85*transition.T,.15*restart)
    values,stats=power_pagerank(csr_matrix(a),restart,QualityConfig().pagerank)
    np.testing.assert_allclose(values,expected,atol=1e-6,rtol=0)
    assert stats['converged'] and stats['residual_l1']<=1e-6 and abs(values.sum()-1)<1e-12


def test_global_l1_tolerance_and_nonconvergence():
    from hm_recommender.graph.pagerank import power_pagerank
    config=dict(QualityConfig().pagerank,iterations=1)
    _,stats=power_pagerank(csr_matrix([[0.,1],[1,0]]),np.array([1.,0]),config)
    assert not stats['converged'] and stats['residual_l1']>1e-6 and stats['iterations']==1


def test_subgraph_cap_and_fallback_declared():
    from hm_recommender.graph.pagerank import personalized_list
    # 600 direct candidates, but the academic control must retain <=512 nodes.
    a=csr_matrix((np.ones(600),(np.zeros(600,dtype=int),np.arange(1,601))),shape=(601,601))
    result,stats=personalized_list(a,[0],np.array([1.]),list(range(1,601)),dict(QualityConfig().pagerank,iterations=1))
    assert stats['nodes']==512 and stats['truncated_nodes']==89
    assert stats['fallback']=='G' and not stats['converged'] and result==list(range(1,201))


def test_permutations_have_no_fixed_owners_and_are_reproducible():
    from hm_recommender.graph.pagerank import derangement
    for size in (2,3,100):
        for seed in range(42,52):
            p=derangement(size,seed)
            assert sorted(p)==list(range(size)) and all(i!=j for i,j in enumerate(p))
            assert p==derangement(size,seed)
