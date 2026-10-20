"""Q1/Q2: isolated snapshots, private rows and aggregate evidence."""
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
import subprocess
import sys
import time
from zipfile import ZipFile

import polars as pl
import pyarrow.parquet as pq
from hm_recommender.data.ingest import load_dataset,scan
from hm_recommender.data.io import atomic_json,sha256
from hm_recommender.quality.contracts import FoldSpec,load_config,digest
from hm_recommender.quality.temporal import build_snapshot,frame,read_labels,verified_path
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.evaluate import evaluate_ranking
from hm_recommender.quality.supervisor import resources


def legacy_parity(dataset,snapshot,customers,rankings):
    from hm_recommender.model.diagnostics import recent_popular
    from hm_recommender.model.recent_history import recent_histories,compose_recent
    catalog=frame(snapshot,'catalog'); ids=catalog['article_id'].to_numpy();k=min(200,len(ids))
    popular=recent_popular(scan(dataset),catalog,7)
    histories=recent_histories(scan(dataset),catalog,customers)
    for method,ranking in rankings.items():
        position=0
        for batch in pq.ParquetFile(verified_path(ranking,'ranking')).iter_batches(batch_size=256*k):
            for buyer in pl.from_arrow(batch).partition_by('customer_id',maintain_order=True):
                expected=ids[compose_recent(histories[position],popular,k=k) if method=='H' else popular[:k]].tolist()
                if buyer['customer_id'][0]!=customers[position] or buyer['article_id'].to_list()!=expected:
                    raise ValueError('Legacy V3 ranking mismatch')
                position+=1
        if position!=len(customers):raise ValueError('Incomplete legacy parity')
    return {'legacy_ranking_verified':True,'buyers':len(customers)}


def compare_reference(reference,source_sha,methods,population):
    archive=Path(reference['path'])
    if sha256(archive)!=reference['archive_sha256']:raise ValueError('Reference archive changed')
    with ZipFile(archive) as bundle:
        history=json.loads(bundle.read('result.json'))['history_validation']
        source=json.loads(bundle.read('environment.json'))['diagnostic_source']['input_identity']['source_sha256']
    if source!=source_sha:raise ValueError('Reference dataset differs')
    for field in ('users','catalog_size'):
        if history['population'][field]!=population[field]:raise ValueError('Reference population differs')
    def compare(actual,expected):
        if isinstance(expected,dict):
            if set(actual)!=set(expected):raise ValueError('Reference fields differ')
            for key in expected:compare(actual[key],expected[key])
        elif not math.isclose(actual,expected,rel_tol=0,abs_tol=1e-10):raise ValueError('Reference baseline mismatch')
    for method,old in [('P','popular_7d'),('H','recent_then_popular')]:
        compare(methods[method]['metrics'],history['methods'][old]['metrics'])
        for k in ('12','200'):compare(methods[method]['exposure'][k],history['methods'][old]['exposure_at_'+k])
        compare(methods[method]['segments']['recent28d'],history['methods'][old]['segments']['with_recent_history'])
    return {'verified':True,'run_id':reference['run_id'],'archive_sha256':reference['archive_sha256'],'absolute_tolerance':1e-10}


def run(config_path,run_dir,*,verify=True):
    started=time.monotonic();run_dir=Path(run_dir)
    initial_free=shutil.disk_usage(run_dir).free
    result={'stage':'Q1','protocol':'quality-v1','status':'failed','test_evaluated':False,'quality_accepted':False}
    try:
        config=load_config(config_path);environment=json.loads((run_dir/'environment.json').read_text())
        options=environment['quality'];size=options['size'];fold=FoldSpec.evaluation(options['fold'])
        if options['stage'] not in ('baselines','graph') or size not in ('demo','pilot','full'):raise ValueError('Only Q1/Q2 are implemented')
        result['stage']='Q2' if options['stage']=='graph' else 'Q1'
        result.update(source_commit=environment['source_commit'],size=size,fold=fold.model_dump(mode='json'))
        atomic_json(run_dir/'config.resolved.json',config.model_dump())
        code=subprocess.run([sys.executable,'-m','pytest','tests/unit','tests/integration/test_quality_folds.py','-q','-p','no:cacheprovider'],check=False).returncode if verify else 0
        result['verification_exit_code']=code
        if code:raise ValueError('Quality verification failed')
        work=run_dir/'work'
        if size=='demo':
            from hm_recommender.model.demo import prepare_demo_dataset
            dataset=prepare_demo_dataset(work/'demo')
        else:dataset=load_dataset(Path('/data/processed/hm-v1'))
        labels=read_labels(dataset,fold).collect(engine='streaming')
        customers=labels['customer_id'].unique().sort().to_list();total=len(customers)
        if size=='pilot':
            customers=sorted(sorted(customers,key=lambda c:(hashlib.sha256(b'42:'+c.encode()).digest(),c))[:config.pilot_users])
            labels=labels.filter(pl.col('customer_id').is_in(customers))
        if not customers:raise ValueError('Empty validation population')
        if options['stage']=='graph':
            from hm_recommender.quality.graph_stage import run_graph_stage
            result.update(run_graph_stage(environment,dataset,fold,config,customers,labels,total,work))
        else:
            snapshot=build_snapshot(dataset,fold,work/'snapshot',config)
            methods={};rankings={};artifacts={}
            for method in ('P','H'):
                print('Q1: evaluating '+method,flush=True)
                ranking=rank_baseline(snapshot,customers,method,work/('ranking-'+method));rankings[method]=ranking
                evaluated=evaluate_ranking(ranking,labels.lazy(),snapshot,work/('evaluation-'+method))
                methods[method]={k:getattr(evaluated,k) for k in ('population','metrics','segments','denominators','exposure','targets')}
                artifacts[method]={'ranking_sha256':ranking.files['ranking'].sha256,'metrics_sha256':evaluated.files['metrics'].sha256}
            population=dict(methods['P']['population'],validation_buyers=total,sampled_out_users=total-len(customers))
            result.update(methods=methods,population=population,artifacts=artifacts,inputs={
                'source_sha256':dataset.source_sha256,'dataset_manifest_sha256':snapshot.identity['dataset_manifest_sha256'],
                'protocol_sha256':digest(config.model_dump()),'snapshot_sha256':digest(snapshot.model_dump(mode='json')),
                'max_feature_date':str(frame(snapshot,'profiles')['last_purchase'].max())})
            if fold.fold_id=='V3':result['parity']=legacy_parity(dataset,snapshot,customers,rankings)
            if fold.fold_id=='V3' and size=='full':
                result['reference']=compare_reference(environment['quality_reference'],dataset.source_sha256,methods,population)
        result['status']='passed'
    except Exception as error:
        # Aggregate error only; private row data never enters report fields.
        result.update(status='failed',failure_reason=type(error).__name__)
        print(result['stage']+' failed: '+type(error).__name__+': '+str(error),flush=True)
    final_free=shutil.disk_usage(run_dir).free
    result['disk']={'start_free_bytes':initial_free,'min_free_bytes':min(initial_free,final_free),'end_free_bytes':final_free}
    result.update(job_duration_seconds=time.monotonic()-started,job_resources=resources())
    atomic_json(run_dir/'result.json',result)
    return 0 if result['status']=='passed' else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args();return run(args.config,args.run_dir)

if __name__=='__main__':sys.exit(main())
