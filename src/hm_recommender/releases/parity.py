"""Exact offline parity; isolated labels are used only after state construction."""
from datetime import date
import math
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from hm_recommender.data.io import sha256
from hm_recommender.ranking.history import rank_history
from hm_recommender.releases.package import verify_history_release
from hm_recommender.quality.contracts import RankingArtifact, QualityFile, digest
from hm_recommender.quality.temporal import frame, verified_path, publish
from hm_recommender.quality.evaluate import evaluate_ranking


def compare_aggregates(actual,expected):
    if isinstance(expected,dict):
        if not isinstance(actual,dict) or set(actual)!=set(expected):raise ValueError('Parity fields differ')
        for key in expected:compare_aggregates(actual[key],expected[key])
    elif expected is None:
        if actual is not None:raise ValueError('Parity empty segment differs')
    elif not math.isclose(actual,expected,abs_tol=1e-10,rel_tol=0):raise ValueError('Parity metric differs')


def verify_parity(release,dataset,snapshot,labels,reference,expected,output_dir):
    release=verify_history_release(release._root)
    if release.data_as_of!=date(2020,9,9) or snapshot.fold.cutoff!=release.data_as_of:
        raise ValueError('T4-A parity is restricted to validation V3')
    if (release.source_sha256!=dataset.source_sha256
        or release.dataset_manifest_sha256!=sha256(dataset._root/'manifest.json')):
        raise ValueError('Release source differs')
    root=release._root
    catalog=pl.read_parquet(root/'catalog.parquet')
    if not catalog.equals(frame(snapshot,'catalog')):raise ValueError('Catalog differs from reference')
    customers=labels['customer_id'].unique().sort().to_list()
    eligible=set(catalog['article_id']);k=min(200,len(eligible))
    population=pl.read_parquet(root/'customers.parquet')
    if not population.equals(frame(snapshot,'profiles').select('customer_id')):raise ValueError('Past population differs')
    histories=pl.read_parquet(root/'histories.parquet').filter(pl.col('customer_id').is_in(customers))
    mapped={c:items for c,items in histories.group_by('customer_id',maintain_order=True).agg('article_id').iter_rows()}
    popularity=pl.read_parquet(root/'popularity.parquet')['article_id'].to_list()
    if (reference.identity['snapshot']!=digest(snapshot.model_dump(mode='json'))
        or reference.identity['customers_sha256']!=digest(customers) or reference.customers!=len(customers)
        or reference.method!='H' or reference.fold!=snapshot.fold):raise ValueError('Reference ranking identity differs')
    original=pq.ParquetFile(verified_path(reference,'ranking'))
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=False)
    path=output/'ranking.parquet';writer=None;position=0
    try:
        for batch in original.iter_batches(batch_size=256*k):
            rows=[]
            for buyer in pl.from_arrow(batch).partition_by('customer_id',maintain_order=True):
                customer=buyer['customer_id'][0]
                if position>=len(customers) or customer!=customers[position]:raise ValueError('Reference buyers differ')
                history=mapped.get(customer,[])
                selected=rank_history(history,popularity,eligible,limit=k)
                if (buyer['rank'].to_list()!=list(range(1,k+1)) or selected!=buyer['article_id'].to_list()):
                    raise ValueError('Exact top200 parity failed')
                position+=1;recent=set(history)
                rows.extend((customer,r,a,1.0/r,'recent_purchase' if a in recent else 'popular_7d') for r,a in enumerate(selected,1))
            table=pl.DataFrame(rows,schema={'customer_id':pl.String,'rank':pl.UInt16,'article_id':pl.String,
                'score':pl.Float64,'source':pl.String},orient='row').to_arrow()
            if writer is None:writer=pq.ParquetWriter(path,table.schema,compression='zstd')
            writer.write_table(table)
    finally:
        if writer:writer.close()
    if position!=len(customers):raise ValueError('Incomplete reference')
    ranking=publish(output,RankingArtifact(identity=dict(reference.identity),fold=snapshot.fold,method='H',
        customers=len(customers),files={'ranking':QualityFile(path=path.name,sha256=sha256(path),rows=position*k,size_bytes=path.stat().st_size)}))
    evaluated=evaluate_ranking(ranking,labels.lazy(),snapshot,output/'evaluation')
    for key in ('population','metrics','segments','denominators','exposure','targets'):
        compare_aggregates(getattr(evaluated,key),getattr(expected,key))
    from hm_recommender.quality.worker import legacy_parity
    legacy=legacy_parity(dataset,snapshot,customers,{'H':ranking})
    return {'exact_top200':True,'metrics_equal':True,'absolute_tolerance':1e-10,'relative_tolerance':0,
        'buyers':len(customers),'catalog_size':len(catalog),'unknown_buyers':len(set(customers)-set(population['customer_id'])),
        'out_of_catalog_relevant_items':evaluated.denominators['out_of_catalog_relevant_items'],
        'metrics':evaluated.metrics,'legacy_ranking_verified':legacy['legacy_ranking_verified'],
        'reference_ranking_sha256':reference.files['ranking'].sha256,
        'ranking_sha256':ranking.files['ranking'].sha256,'metrics_sha256':evaluated.files['metrics'].sha256}
