"""Exact metrics and private per-buyer evidence for a fixed evaluation fold."""
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow.parquet as pq
from hm_recommender.data.io import locked
from hm_recommender.model.metrics import ranking_metrics
from hm_recommender.quality.contracts import FoldEvaluation,METRICS,SEGMENTS,digest
from hm_recommender.quality.temporal import existing,file_entry,frame,publish,verified_path


def average(records):
    return {key:float(np.mean([r[key] for r in records])) for key in METRICS} if records else None


def exposure(counter,lists,sets,catalog_size,k):
    counts=np.array(sorted(counter.values(),reverse=True),dtype=np.float64)
    probabilities=counts/counts.sum()
    return dict(k=k,distinct_items=len(counts),coverage=len(counts)/catalog_size,
        unique_ordered_lists=len(lists),unique_sets=len(sets),
        top_exposure_share={str(n):float(counts[:n].sum()/counts.sum()) for n in (1,5,10,50)},
        effective_items=float(np.exp(-np.sum(probabilities*np.log(probabilities)))))


def evaluate_ranking(ranking,labels,snapshot,output_dir):
    if ranking.fold!=snapshot.fold or ranking.identity['snapshot']!=digest(snapshot.model_dump(mode='json')):
        raise ValueError('Ranking snapshot identity mismatch')
    label_frame=labels.collect(engine='streaming')
    groups=label_frame.group_by('customer_id').agg('article_id','seen_before').sort('customer_id')
    customers=groups['customer_id'].to_list()
    if ranking.customers!=len(customers) or ranking.identity['customers_sha256']!=digest(customers):
        raise ValueError('Ranking and labels have different buyer populations')
    truth={c:(set(items),{i for i,s in zip(items,seen) if s}) for c,items,seen in groups.iter_rows()}
    catalog=set(frame(snapshot,'catalog')['article_id']); k=min(200,len(catalog))
    profile=frame(snapshot,'profiles')
    segments=dict(profile.select('customer_id','segment').iter_rows())
    root=Path(output_dir)
    identity={'ranking':digest(ranking.model_dump(mode='json')),'labels_sha256':digest(label_frame.to_dicts())}
    with locked(root):
        saved=existing(root,identity,FoldEvaluation)
        if saved: return saved
        rows=[]; targets={'repeat':[],'discovery':[]}
        counters={n:Counter() for n in (12,200)}
        ordered={n:set() for n in counters}; unordered={n:set() for n in counters}
        observed=set(); ranked_file=pq.ParquetFile(verified_path(ranking,'ranking'))
        for group in range(ranked_file.num_row_groups):
            batch=pl.from_arrow(ranked_file.read_row_group(group))
            for buyer in batch.partition_by('customer_id',maintain_order=True):
                customer=buyer['customer_id'][0]
                articles=buyer['article_id'].to_list()
                if (customer not in truth or customer in observed or len(articles)!=k or len(set(articles))!=k
                    or not set(articles)<=catalog or buyer['rank'].to_list()!=list(range(1,k+1))):
                    raise ValueError('Invalid, duplicate or incomplete ranking')
                observed.add(customer)
                relevant,repeated=truth[customer]
                metrics=ranking_metrics(articles,relevant)
                rows.append(dict(customer_id=customer,fold_id=snapshot.fold.fold_id,method=ranking.method,
                    segment=segments.get(customer,'no_history'),relevant_items=len(relevant),
                    available_relevant_items=len(relevant&catalog),repeat_relevant_items=len(repeated),**metrics))
                for name,subset in [('repeat',repeated),('discovery',relevant-repeated)]:
                    if subset: targets[name].append(dict(relevant_items=len(subset),**ranking_metrics(articles,subset)))
                for n in counters:
                    values=tuple(articles[:n]); counters[n].update(values)
                    ordered[n].add(values); unordered[n].add(tuple(sorted(values)))
        if observed!=set(customers): raise ValueError('Evaluation omitted buyers')
        metrics_table=pl.DataFrame(rows).sort('customer_id')
        files={'metrics':file_entry(root,'metrics',metrics_table)}
        denominators={'relevant_items':sum(r['relevant_items'] for r in rows),
            'out_of_catalog_relevant_items':sum(r['relevant_items']-r['available_relevant_items'] for r in rows),
            'mean_available_recall_ceiling':float(np.mean([r['available_relevant_items']/r['relevant_items'] for r in rows]))}
        return publish(root,FoldEvaluation(identity=identity,files=files,fold=snapshot.fold,method=ranking.method,
            population={'users':len(rows),'catalog_size':len(catalog)},metrics=average(rows),
            segments={s:{'users':sum(r['segment']==s for r in rows),'metrics':average([r for r in rows if r['segment']==s])} for s in SEGMENTS},
            denominators=denominators,
            exposure={str(n):exposure(counters[n],ordered[n],unordered[n],len(catalog),min(n,k)) for n in counters},
            targets={name:{'users':len(values),'relevant_items':sum(r['relevant_items'] for r in values),'metrics':average(values)} for name,values in targets.items()}))
