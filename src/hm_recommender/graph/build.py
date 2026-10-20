"""Weekly proxy baskets and blockwise sparse cooccurrence, never a dense catalog²."""
from datetime import timedelta
from pathlib import Path
import time
import numpy as np
import polars as pl
from scipy import sparse

from hm_recommender.data.ingest import scan
from hm_recommender.data.io import locked, sha256
from hm_recommender.quality.contracts import LocalArtifact, FoldSpec, QualityConfig, QualityFile, digest
from hm_recommender.quality.temporal import existing, file_entry, frame, history_table, publish, verified_path


class GraphManifest(LocalArtifact):
    fold: FoldSpec
    statistics: dict


def load_csr(graph):
    matrix=sparse.load_npz(verified_path(graph,'csr')).tocsr()
    if matrix.shape!=(graph.statistics['nodes'],)*2 or matrix.nnz!=graph.statistics['edges']:
        raise ValueError('Graph dimensions differ from manifest')
    return matrix


def build_graph(dataset,snapshot,config,output_dir):
    if config.model_dump()!=QualityConfig().model_dump():raise ValueError('Fixed quality protocol required')
    if (dataset.source_sha256!=snapshot.identity['source_sha256'] or
        sha256(dataset._root/'manifest.json')!=snapshot.identity['dataset_manifest_sha256']):
        raise ValueError('Graph dataset differs from snapshot')
    root=Path(output_dir);started=time.monotonic()
    identity={'snapshot':digest(snapshot.model_dump(mode='json')),'config_sha256':digest(config.model_dump()),'graph_version':1}
    with locked(root):
        saved=existing(root,identity,GraphManifest)
        if saved:return saved
        scratch=root/'temporary';scratch.mkdir(exist_ok=False)
        cutoff=snapshot.fold.cutoff;lower=cutoff-timedelta(days=90)
        nodes=frame(snapshot,'catalog').select('article_id').with_row_index('node')
        tx=scan(dataset).filter((pl.col('t_dat')>=lower)&(pl.col('t_dat')<cutoff))
        unique=(tx.with_columns(((pl.col('t_dat')-pl.lit(lower)).dt.total_days()//7).alias('week'))
            .group_by('customer_id','week','article_id').agg(pl.col('t_dat').max().alias('last_purchase'),pl.len().alias('frequency'))
            .sort(['customer_id','week','last_purchase','frequency','article_id'],descending=[False,False,True,True,False]))
        baskets=(unique.group_by('customer_id','week',maintain_order=True)
            .agg(pl.col('article_id').head(50),pl.len().alias('uncapped'),pl.col('frequency').sum().alias('transactions')).collect(engine='streaming'))
        stats={'transactions_window':int(baskets['transactions'].sum()),'unique_memberships':int(baskets['uncapped'].sum()),'nodes':len(nodes),'baskets':len(baskets),'window_days':90,'basket_days':7,'basket_semantics':'weekly_proxy',
            'capped_baskets':int((baskets['uncapped']>50).sum()),
            'discarded_memberships':int((baskets['uncapped'].cast(pl.Int64)-50).clip(lower_bound=0).sum()),'block_nodes':256}
        members=(baskets.with_row_index('basket').select('basket','article_id').explode('article_id',empty_as_null=False)
            .join(nodes,on='article_id',how='left').select('basket','node'))
        if members['node'].null_count():raise ValueError('Basket item outside past catalog')
        stats['retained_memberships']=len(members)
        member_path=scratch/'memberships.parquet';members.write_parquet(member_path)
        incidence=sparse.csr_matrix((np.ones(len(members),dtype=np.int64),(members['basket'].to_numpy(),members['node'].to_numpy())),shape=(len(baskets),len(nodes)))
        support=np.asarray(incidence.sum(axis=0)).ravel()
        nodes=nodes.with_columns(pl.Series('support',support))
        del members,baskets
        # CSC makes selecting origin columns cheap. Product has <=256 origins;
        # complete unpruned cooccurrence never exists in RAM or as a dense matrix.
        columns=incidence.tocsc();out_r=[];out_c=[];out_w=[];partitions=[]
        temporary_peak=member_path.stat().st_size;raw_edges=0
        for start in range(0,len(nodes),256):
            end=min(start+256,len(nodes));cooc=(columns[:,start:end].T@incidence).tocoo()
            keep=cooc.row+start!=cooc.col
            table=pl.DataFrame({'origin':cooc.row[keep]+start,'target':cooc.col[keep],'cooccurrence':cooc.data[keep]})
            path=scratch/'cooccurrence.parquet';table.sort('origin','target').write_parquet(path)
            partitions.append({'start':start,'end':end,'rows':len(table),'sha256':sha256(path),'size_bytes':path.stat().st_size})
            temporary_peak=max(temporary_peak,member_path.stat().st_size+path.stat().st_size);raw_edges+=len(table)
            del cooc,table
            table=pl.read_parquet(path)
            r=table['origin'].to_numpy();c=table['target'].to_numpy()
            weights=table['cooccurrence'].to_numpy()/np.sqrt(support[r].astype(float)*support[c])
            retained=(table.with_columns(pl.Series('weight',weights)).sort(['origin','weight','target'],descending=[False,True,False])
                .group_by('origin',maintain_order=True).head(50))
            out_r.append(retained['origin'].to_numpy());out_c.append(retained['target'].to_numpy());out_w.append(retained['weight'].to_numpy())
            path.unlink()
        matrix=sparse.csr_matrix((np.concatenate(out_w),(np.concatenate(out_r),np.concatenate(out_c))),shape=(len(nodes),len(nodes)))
        matrix.sort_indices();sparse.save_npz(root/'graph.npz',matrix)
        del incidence,columns
        seeds=(history_table(tx,cutoff,90).group_by('customer_id',maintain_order=True).head(50)
            .join(nodes.select('article_id','node'),on='article_id',how='left')
            .sort(['customer_id','last_purchase','frequency','article_id'],descending=[False,True,True,False]))
        seeds=seeds.with_columns((pl.col('frequency').log1p()*2**(-((pl.lit(cutoff)-pl.col('last_purchase')).dt.total_days()/14))).alias('raw_weight'))
        seeds=seeds.with_columns((pl.col('raw_weight')/pl.col('raw_weight').sum().over('customer_id')).alias('weight')).drop('raw_weight')
        files={'nodes':file_entry(root,'nodes',nodes),'seeds':file_entry(root,'seeds',seeds)}
        path=root/'graph.npz';files['csr']=QualityFile(path=path.name,sha256=sha256(path),rows=matrix.nnz,size_bytes=path.stat().st_size)
        member_path.unlink();scratch.rmdir()
        stats.update(seeded_profiles=seeds['customer_id'].n_unique(),seed_memberships=len(seeds),edges=matrix.nnz,isolated_nodes=int(np.sum(np.diff(matrix.indptr)==0)),max_degree=int(np.diff(matrix.indptr).max()),
            unpruned_directed_edges=raw_edges,partitions=partitions,temporary_peak_bytes=temporary_peak,
            final_bytes=sum(f.size_bytes for f in files.values()),build_seconds=time.monotonic()-started)
        return publish(root,GraphManifest(identity=identity,files=files,fold=snapshot.fold,statistics=stats))
