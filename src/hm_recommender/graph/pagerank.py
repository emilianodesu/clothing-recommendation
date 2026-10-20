"""Bounded induced-subgraph PPR; an academic sample, never a full-model winner."""
import hashlib
from pathlib import Path
import numpy as np
import polars as pl
from scipy import sparse
from hm_recommender.quality.contracts import digest
from hm_recommender.quality.temporal import file_entry
from hm_recommender.graph.retrieve import RetrievalContext,write_ranking


def power_pagerank(matrix,restart,config):
    sums=np.asarray(matrix.sum(axis=1)).ravel();inverse=np.divide(1.,sums,out=np.zeros_like(sums,dtype=float),where=sums!=0)
    transition=sparse.diags(inverse)@matrix;dangling=sums==0
    values=restart.copy();residual=float('inf')
    for iteration in range(1,config['iterations']+1):
        updated=config['alpha']*(transition.T@values+values[dangling].sum()*restart)+(1-config['alpha'])*restart
        residual=float(np.abs(updated-values).sum());values=updated
        if residual<=config['tolerance']:break
    return values,{'iterations':iteration,'residual_l1':residual,'converged':residual<=config['tolerance']}


def personalized_list(matrix,seeds,seed_weights,neighbors,config):
    nodes=list(seeds);seen=set(nodes)
    extras=[n for n in neighbors if n not in seen]
    truncated=max(0,len(nodes)+len(extras)-config['nodes']);nodes+=extras[:config['nodes']-len(nodes)]
    local=matrix[nodes,:][:,nodes];restart=np.zeros(len(nodes));restart[:len(seeds)]=seed_weights/np.sum(seed_weights)
    values,stats=power_pagerank(local,restart,config)
    stats.update(nodes=len(nodes),edges=local.nnz,truncated_nodes=truncated,fallback=None)
    if not stats['converged']:
        stats['fallback']='G';return neighbors[:200],stats
    order=sorted(range(len(nodes)),key=lambda i:(-values[i],nodes[i]))
    return [int(nodes[i]) for i in order if values[i]>0][:200],stats


def derangement(size,seed):
    if size<2:raise ValueError('Personalization contrast requires at least two seeded buyers')
    # Rejection sampling is deterministic and yields a permutation with no owner.
    rng=np.random.default_rng(seed);original=np.arange(size)
    while True:
        candidate=rng.permutation(size)
        if np.all(candidate!=original):return candidate.tolist()


def pagerank_control(snapshot,graph,customers,output_dir,config):
    cohort=sorted(sorted(customers,key=lambda c:(hashlib.sha256(b'42:'+c.encode()).digest(),c))[:config.pagerank['sample_users']])
    ctx=RetrievalContext(snapshot,graph,cohort);root=Path(output_dir);root.mkdir(parents=True,exist_ok=True)
    lists={};runs=[];seeded=[]
    for customer in cohort:
        if customer not in ctx.seeds:continue
        seeded.append(customer);nodes,weights=ctx.seeds[customer];neighbors,_=ctx.neighbor_scores(customer)
        lists[customer],stats=personalized_list(ctx.matrix,nodes.tolist(),weights,neighbors,config.pagerank);runs.append(dict(customer_id=customer,**stats))
    identity={'snapshot':digest(snapshot.model_dump(mode='json')),'graph':digest(graph.model_dump(mode='json')),'customers_sha256':digest(cohort),'method':'PPR'}
    def write(name,donors):
        def provider(customer):
            neighbors=lists.get(donors.get(customer,customer),[])
            selected=ctx.rank(customer,neighbors)
            return [ctx.ids[i] for i in selected],['ppr_control']*len(selected)
        return write_ranking(snapshot,cohort,'PPR',root/name,dict(identity,control=name),provider)
    normal=write('normal',{})
    table=pl.DataFrame(runs,schema={'customer_id':pl.String,'iterations':pl.UInt16,'residual_l1':pl.Float64,'converged':pl.Boolean,
        'nodes':pl.UInt16,'edges':pl.UInt32,'truncated_nodes':pl.UInt32,'fallback':pl.String})
    iteration_file=file_entry(root,'iterations',table)
    permutations=[]
    if len(seeded)>=2:
        for seed in range(42,52):
            indices=derangement(len(seeded),seed);donors={c:seeded[i] for c,i in zip(seeded,indices)}
            artifact=write('permutation-'+str(seed),donors)
            permutations.append({'seed':seed,'fixed_owners':0,'ranking_sha256':artifact.files['ranking'].sha256})
    return {'normal_ranking_sha256':normal.files['ranking'].sha256,'iteration_statistics_sha256':iteration_file.sha256,'scope':'sample','cohort_users':len(cohort),'cohort_sha256':digest(cohort),'seeded_users':len(seeded),
        'no_seed_users':len(cohort)-len(seeded),'converged_users':sum(s['converged'] for s in runs),
        'nonconverged_fallback_G':sum(not s['converged'] for s in runs),'no_seed_fallback_H':len(cohort)-len(seeded),
        'max_nodes':max((s['nodes'] for s in runs),default=0),'max_edges':max((s['edges'] for s in runs),default=0),
        'mean_nodes':float(np.mean([s['nodes'] for s in runs])) if runs else 0.,
        'max_iterations':max((s['iterations'] for s in runs),default=0),
        'max_residual_l1':max((s['residual_l1'] for s in runs),default=0.),
        'max_converged_residual_l1':max((s['residual_l1'] for s in runs if s['converged']),default=0.),
        'truncated_users':sum(s['truncated_nodes']>0 for s in runs),'truncated_nodes':sum(s['truncated_nodes'] for s in runs),
        'permutations':permutations,'personalization_applicable':len(seeded)>=2,'promotion_allowed':False}
