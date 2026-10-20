"""Q2 orchestration: reuse verified Q1, evaluate G full and PPR on its own cohort."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
from zipfile import ZipFile
import polars as pl
import pyarrow.parquet as pq

from hm_recommender.data.io import sha256
from hm_recommender.quality.contracts import FoldManifest,RankingArtifact,FoldEvaluation,QualityFile,METRICS,digest
from hm_recommender.quality.temporal import frame,verified_path,publish,read_labels
from hm_recommender.quality.evaluate import evaluate_ranking
from hm_recommender.graph.build import build_graph
from hm_recommender.graph.retrieve import rank_graph
from hm_recommender.graph.pagerank import pagerank_control


FIELDS=('population','metrics','segments','denominators','exposure','targets')


def load_local(root,model):
    artifact=model.model_validate_json((root/'manifest.json').read_text());artifact._root=root
    for key in artifact.files:verified_path(artifact,key)
    return artifact


def load_reference(reference,dataset,fold,size,config,customers,labels):
    archive=Path(reference['path'])
    if sha256(archive)!=reference['archive_sha256']:raise ValueError('Q1 archive changed')
    with ZipFile(archive) as z:result=json.loads(z.read('result.json'))
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
    # Installed package lives under site-packages; the image carries stdlib validators here.
    sys.path.insert(0,'/opt/hmrec/scripts')
    from quality_handoff import validate_quality_result
    validate_quality_result(result)
    if result['stage']!='Q1' or result['fold']!=fold.model_dump(mode='json') or result['size']!=size:
        raise ValueError('Q1 fold, size or stage mismatch')
    inputs=result['inputs']
    if (inputs['source_sha256']!=dataset.source_sha256 or inputs['dataset_manifest_sha256']!=sha256(dataset._root/'manifest.json')
        or inputs['protocol_sha256']!=digest(config.model_dump())):raise ValueError('Q1 source or protocol mismatch')
    work=Path(reference['work_path']);snapshot=load_local(work/'snapshot',FoldManifest)
    if digest(snapshot.model_dump(mode='json'))!=inputs['snapshot_sha256'] or snapshot.fold!=fold:raise ValueError('Q1 snapshot mismatch')
    rankings={}
    for method in ('P','H'):
        ranking=load_local(work/('ranking-'+method),RankingArtifact)
        evaluation=load_local(work/('evaluation-'+method),FoldEvaluation)
        if (ranking.method!=method or ranking.fold!=fold or ranking.customers!=len(customers)
            or ranking.identity['snapshot']!=inputs['snapshot_sha256'] or ranking.identity['customers_sha256']!=digest(customers)
            or evaluation.method!=method or evaluation.fold!=fold or evaluation.identity['ranking']!=digest(ranking.model_dump(mode='json'))
            or evaluation.identity['labels_sha256']!=digest(labels.to_dicts())):raise ValueError('Q1 local population or labels mismatch')
        if (ranking.files['ranking'].sha256!=result['artifacts'][method]['ranking_sha256']
            or evaluation.files['metrics'].sha256!=result['artifacts'][method]['metrics_sha256']
            or {k:getattr(evaluation,k) for k in FIELDS}!=result['methods'][method]):raise ValueError('Q1 local evidence changed')
        rankings[method]=ranking
    return snapshot,rankings,result


def subset_ranking(ranking,cohort,output):
    root=Path(output);root.mkdir(parents=True,exist_ok=False);path=root/'ranking.parquet';writer=None;total=0
    try:
        for batch in pq.ParquetFile(verified_path(ranking,'ranking')).iter_batches(batch_size=256*min(200,ranking.files['ranking'].rows//ranking.customers)):
            selected=pl.from_arrow(batch).filter(pl.col('customer_id').is_in(cohort)).to_arrow()
            if not selected.num_rows:continue
            if writer is None:writer=pq.ParquetWriter(path,selected.schema,compression='zstd')
            writer.write_table(selected);total+=selected.num_rows
    finally:
        if writer:writer.close()
    identity=dict(ranking.identity,customers_sha256=digest(cohort),subset_of=digest(ranking.model_dump(mode='json')))
    return publish(root,RankingArtifact(identity=identity,files={'ranking':QualityFile(path=path.name,sha256=sha256(path),rows=total,size_bytes=path.stat().st_size)},fold=ranking.fold,method=ranking.method,customers=len(cohort)))


def pool_diagnostics(ranking,labels):
    truth={c:set(items) for c,items in labels.group_by('customer_id').agg('article_id').iter_rows()}
    membership=Counter();hits=Counter();exclusive={s:0 for s in ('history','graph','popular')}
    for batch in pq.ParquetFile(verified_path(ranking,'ranking')).iter_batches(batch_size=51200):
        for customer,article,source in pl.from_arrow(batch).select('customer_id','article_id','source').iter_rows():
            membership[source]+=1
            if article in truth[customer]:
                hits[source]+=1
                if source in exclusive:exclusive[source]+=1
    return {'scope':'selected_G_pool_at_200','selected_memberships':dict(membership),'relevant_memberships':dict(hits),
        'source_exclusive_hits':exclusive,'causal_attribution':False}


def evaluate_control(snapshot,graph,customers,labels,rankings,root,config):
    control=pagerank_control(snapshot,graph,customers,root,config)
    cohort=sorted(sorted(customers,key=lambda c:(hashlib.sha256(b'42:'+c.encode()).digest(),c))[:config.pagerank['sample_users']])
    sample=labels.filter(pl.col('customer_id').is_in(cohort))
    seeded=set(pl.scan_parquet(verified_path(graph,'seeds')).select('customer_id').unique().filter(pl.col('customer_id').is_in(cohort)).collect()['customer_id'])
    methods={};normal_seed_metrics=None;permuted=[]
    for method in ('H','G','PPR'):
        ranking=load_local(root/'normal',RankingArtifact) if method=='PPR' else subset_ranking(rankings[method],cohort,root/('ranking-'+method))
        evaluation=evaluate_ranking(ranking,sample.lazy(),snapshot,root/('evaluation-'+method))
        methods[method]={k:getattr(evaluation,k) for k in FIELDS}
        if method=='PPR':
            private=frame(evaluation,'metrics').filter(pl.col('customer_id').is_in(seeded))
            normal_seed_metrics={k:float(private[k].mean()) for k in METRICS} if len(private) else None
    for entry in control['permutations']:
        name='permutation-'+str(entry['seed']);ranking=load_local(root/name,RankingArtifact)
        evaluation=evaluate_ranking(ranking,sample.lazy(),snapshot,root/('evaluation-'+name))
        private=frame(evaluation,'metrics').filter(pl.col('customer_id').is_in(seeded))
        metrics={k:float(private[k].mean()) for k in METRICS}
        entry.update(metrics_all=evaluation.metrics,metrics_seeded=metrics);permuted.append(metrics)
    mean={k:sum(p[k] for p in permuted)/len(permuted) for k in METRICS} if permuted else None
    control['methods']=methods
    control['personalization']={'users':len(seeded),'excluded_no_seeds':len(cohort)-len(seeded),
        'normal':normal_seed_metrics,'permuted_mean':mean,
        'delta':{k:normal_seed_metrics[k]-mean[k] for k in METRICS} if mean else None}
    return control


def run_graph_stage(environment,dataset,fold,config,customers,labels,total,work):
    started=time.monotonic()
    snapshot,rankings,reference=load_reference(environment['quality_reference'],dataset,fold,environment['quality']['size'],config,customers,labels)
    costs={'reference_verification_seconds':time.monotonic()-started}
    print('Q2: building 90-day graph',flush=True)
    graph=build_graph(dataset,snapshot,config,work/'graph');costs['graph_build_seconds']=graph.statistics['build_seconds']
    started=time.monotonic();rankings['G']=rank_graph(snapshot,graph,customers,work/'ranking-G');costs['retrieval_seconds']=time.monotonic()-started
    started=time.monotonic();evaluated=evaluate_ranking(rankings['G'],labels.lazy(),snapshot,work/'evaluation-G');costs['evaluation_seconds']=time.monotonic()-started
    pool=pool_diagnostics(rankings['G'],labels)
    started=time.monotonic();print('Q2: bounded PPR control',flush=True)
    control=evaluate_control(snapshot,graph,customers,labels,rankings,work/'pagerank',config);costs['pagerank_seconds']=time.monotonic()-started
    costs['candidate_cost_seconds']=costs['graph_build_seconds']+costs['retrieval_seconds']
    methods=dict(reference['methods'],G={k:getattr(evaluated,k) for k in FIELDS})
    artifacts=dict(reference['artifacts'],G={'ranking_sha256':rankings['G'].files['ranking'].sha256,'metrics_sha256':evaluated.files['metrics'].sha256})
    return {'methods':methods,'artifacts':artifacts,'population':dict(evaluated.population,validation_buyers=total,sampled_out_users=total-len(customers)),
        'inputs':reference['inputs'],'parity':reference.get('parity'),
        'reference':{'verified':True,'stage':'Q1','run_id':environment['quality_reference']['run_id'],'archive_sha256':environment['quality_reference']['archive_sha256'],'private_artifacts_verified':True},
        'graph':graph.statistics,'graph_artifacts':{key:entry.sha256 for key,entry in graph.files.items()},
        'retrieval':rankings['G'].statistics,'pool':pool,'pagerank':control,'costs':costs}
