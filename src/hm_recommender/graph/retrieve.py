"""Past-only seeds, direct neighbors and fixed reciprocal-rank fusion."""
from collections import Counter
from pathlib import Path
import numpy as np
import polars as pl
import pyarrow.parquet as pq
from hm_recommender.data.io import locked,sha256
from hm_recommender.quality.contracts import RankingArtifact,QualityFile,digest
from hm_recommender.quality.temporal import existing,frame,publish,verified_path
from hm_recommender.quality.baselines import compose_history
from hm_recommender.graph.build import load_csr


class GraphRankingArtifact(RankingArtifact):
    statistics: dict


def compose_graph(history,neighbors,popularity,k=200):
    if not neighbors:return compose_history(history,popularity,k)
    scores={}
    for source in (neighbors[:200],popularity[:200]):
        for rank,item in enumerate(source,1):scores[item]=scores.get(item,0)+1/(60+rank)
    fused=sorted(scores,key=lambda item:(-scores[item],item))
    return compose_history(history,fused,k)


class RetrievalContext:
    def __init__(self,snapshot,graph,customers):
        if graph.fold!=snapshot.fold or graph.identity['snapshot']!=digest(snapshot.model_dump(mode='json')):
            raise ValueError('Graph snapshot mismatch')
        self.matrix=load_csr(graph);self.ids=frame(graph,'nodes')['article_id'].to_list()
        mapping={a:i for i,a in enumerate(self.ids)};self.k=min(200,len(self.ids))
        self.popular=[mapping[a] for a in frame(snapshot,'popularity')['article_id'].head(200)]
        table=frame(snapshot,'histories').filter(pl.col('customer_id').is_in(customers))
        self.histories={c:[mapping[a] for a in items] for c,items in table.group_by('customer_id',maintain_order=True).agg(pl.col('article_id').head(200)).iter_rows()}
        seeds=pl.scan_parquet(verified_path(graph,'seeds')).filter(pl.col('customer_id').is_in(customers)).collect(engine='streaming')
        self.seeds={c:(np.asarray(nodes,dtype=np.int32),np.asarray(weights,dtype=float)) for c,nodes,weights in seeds.group_by('customer_id',maintain_order=True).agg('node','weight').iter_rows()}

    def history(self,customer):return self.histories.get(customer,[])

    def neighbor_scores(self,customer):
        nodes,weights=self.seeds.get(customer,(np.array([],dtype=int),np.array([])))
        indices=[];values=[]
        for node,weight in zip(nodes,weights):
            start,end=self.matrix.indptr[node:node+2]
            indices.append(self.matrix.indices[start:end]);values.append(self.matrix.data[start:end]*weight)
        if not indices:return [],np.array([])
        indices=np.concatenate(indices);values=np.concatenate(values)
        unique,inverse=np.unique(indices,return_inverse=True);scores=np.bincount(inverse,weights=values,minlength=len(unique))
        keep=scores>0;unique,scores=unique[keep],scores[keep]
        order=np.lexsort((unique,-scores))
        return unique[order].tolist(),scores[order]

    def neighbors(self,customer):return self.neighbor_scores(customer)[0][:200]

    def rank(self,customer,neighbors=None):
        if neighbors is None:neighbors=self.neighbors(customer)
        return compose_graph(self.history(customer),neighbors,self.popular,self.k)


def write_ranking(snapshot,customers,method,root,identity,provider,statistics=None):
    """Provider yields (item IDs, source memberships); buffers <=256 buyers."""
    root=Path(root);model=GraphRankingArtifact if statistics is not None else RankingArtifact
    with locked(root):
        saved=existing(root,identity,model)
        if saved:return saved
        temporary=root/'ranking.parquet.partial';writer=None;total=0
        try:
            for start in range(0,len(customers),256):
                rows=[]
                for customer in customers[start:start+256]:
                    articles,sources=provider(customer)
                    for rank,(article,source) in enumerate(zip(articles,sources,strict=True),1):rows.append((customer,rank,article,1/rank,source))
                batch=pl.DataFrame(rows,schema={'customer_id':pl.String,'rank':pl.UInt16,'article_id':pl.String,'score':pl.Float64,'source':pl.String},orient='row').to_arrow()
                if writer is None:writer=pq.ParquetWriter(temporary,batch.schema,compression='zstd')
                writer.write_table(batch);total+=len(batch)
        finally:
            if writer:writer.close()
        path=root/'ranking.parquet';temporary.replace(path)
        extra={'statistics':dict(statistics)} if statistics is not None else {}
        return publish(root,model(identity=identity,files={'ranking':QualityFile(path=path.name,sha256=sha256(path),rows=total,size_bytes=path.stat().st_size)},fold=snapshot.fold,method=method,customers=len(customers),**extra))


def rank_graph(snapshot,graph,customers,output_dir):
    if not customers or customers!=sorted(set(customers)):raise ValueError('Sorted unique buyers required')
    ctx=RetrievalContext(snapshot,graph,customers)
    identity={'snapshot':digest(snapshot.model_dump(mode='json')),'graph':digest(graph.model_dump(mode='json')),'customers_sha256':digest(customers),'method':'G'}
    stats=Counter(users=0,no_seeds=0,no_neighbors=0)
    def provider(customer):
        neighbors=ctx.neighbors(customer);selected=ctx.rank(customer,neighbors)
        sources={'history':set(ctx.history(customer)),'graph':set(neighbors),'popular':set(ctx.popular)}
        stats['users']+=1;stats['no_seeds']+=int(customer not in ctx.seeds);stats['no_neighbors']+=int(not neighbors)
        for name,items in sources.items():stats['input_'+name+'_memberships']+=len(items)
        memberships=['|'.join(name for name,items in sources.items() if item in items) for item in selected]
        for key in memberships:stats['selected_'+key]+=1
        return [ctx.ids[i] for i in selected],memberships
    return write_ranking(snapshot,customers,'G',output_dir,identity,provider,stats)
