"""Deterministic P/H retrieval with bounded ranking buffers; no Torch dependency."""
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
from hm_recommender.data.io import locked,sha256
from hm_recommender.quality.contracts import RankingArtifact,QualityFile,digest
from hm_recommender.quality.temporal import existing,frame,publish
from hm_recommender.ranking.history import unique_prefix


def compose_history(history,popularity,k=200):
    return unique_prefix((history,popularity),limit=k)


def rank_baseline(snapshot,customers,method,output_dir):
    if method not in ('P','H') or not customers or customers!=sorted(set(customers)):
        raise ValueError('Expected P/H and nonempty sorted unique customers')
    root=Path(output_dir)
    identity={'snapshot':digest(snapshot.model_dump(mode='json')),'customers_sha256':digest(customers),'method':method}
    with locked(root):
        saved=existing(root,identity,RankingArtifact)
        if saved: return saved
        catalog=frame(snapshot,'catalog'); k=min(200,len(catalog))
        popular=frame(snapshot,'popularity')['article_id'].to_list()[:k]
        table=frame(snapshot,'histories').filter(pl.col('customer_id').is_in(customers))
        histories={row[0]:row[1] for row in table.group_by('customer_id',maintain_order=True).agg(pl.col('article_id').head(k)).iter_rows()}
        temporary=root/'ranking.parquet.partial'; total=0
        writer=None
        try:
            for start in range(0,len(customers),256):
                rows=[]
                for customer in customers[start:start+256]:
                    history=histories.get(customer,[]) if method=='H' else []
                    selected=compose_history(history,popular,k)
                    if len(selected)!=k: raise ValueError('Incomplete baseline candidates')
                    for rank,article in enumerate(selected,1):
                        rows.append((customer,rank,article,1.0/rank,'recent_purchase' if article in history else 'popular_7d'))
                batch=pl.DataFrame(rows,schema={'customer_id':pl.String,'rank':pl.UInt16,'article_id':pl.String,'score':pl.Float64,'source':pl.String},orient='row').to_arrow()
                if writer is None: writer=pq.ParquetWriter(temporary,batch.schema,compression='zstd')
                writer.write_table(batch); total+=batch.num_rows
        finally:
            if writer: writer.close()
        path=root/'ranking.parquet'; temporary.replace(path)
        entry=QualityFile(path=path.name,sha256=sha256(path),rows=total,size_bytes=path.stat().st_size)
        return publish(root,RankingArtifact(identity=identity,files={'ranking':entry},fold=snapshot.fold,method=method,customers=len(customers)))
