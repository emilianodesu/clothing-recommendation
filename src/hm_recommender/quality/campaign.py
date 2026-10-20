"""Verify immutable report ancestry and private per-buyer evaluation tables."""
import json
from pathlib import Path
import sys

import numpy as np
import polars as pl

from hm_recommender.data.io import sha256
from hm_recommender.quality.contracts import FoldEvaluation, FoldSpec, METRICS, SEGMENTS, digest
from hm_recommender.quality.temporal import frame
from hm_recommender.quality.selection import FOLDS

FIELDS = ('population', 'metrics', 'segments', 'denominators', 'exposure', 'targets')
ROLES = {'Q1': ('Q1', ('P', 'H')), 'Q2': ('Q2', ('G',)), 'RH': ('Q3', ('RH',)), 'RG': ('Q3', ('RG',))}


def closed_report(entry, size, config):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
    sys.path.insert(0, '/opt/hmrec/scripts')
    from handoff import read_report
    path=Path(entry['path'])
    if sha256(path)!=entry['archive_sha256']: raise ValueError('Campaign archive hash mismatch')
    report=read_report(path); result=report.get('result', {})
    if (report['run_id']!=entry['run_id'] or report['status']!='passed'
            or report['kind']!='quality-handoff' or not report['source_clean']
            or result['stage']!=ROLES[entry['role']][0] or result['size']!=size
            or result['fold']!=FoldSpec.evaluation(entry['fold']).model_dump(mode='json')
            or result['inputs']['protocol_sha256']!=digest(config.model_dump())):
        raise ValueError('Campaign report scope or identity mismatch')
    if entry['role'] in ('RH','RG') and result['ranker']['pool']!=entry['role'][1:]:
        raise ValueError('Wrong ranker pool')
    return result


def metric_table(entry, result, method):
    work=Path(entry['work_path']);root=work/('evaluation-'+method)
    raw=(root/'manifest.json').read_text()
    evaluation=FoldEvaluation.model_validate_json(raw);evaluation._root=root
    ranking=json.loads((work/('ranking-'+method)/'manifest.json').read_text())
    if (evaluation.method!=method or evaluation.fold.model_dump(mode='json')!=result['fold']
            or {k:getattr(evaluation,k) for k in FIELDS}!=result['methods'][method]
            or evaluation.files['metrics'].sha256!=result['artifacts'][method]['metrics_sha256']
            or evaluation.identity['ranking']!=digest(ranking)
            or ranking['files']['ranking']['sha256']!=result['artifacts'][method]['ranking_sha256']
            or ranking['fold']!=result['fold'] or ranking['method']!=method
            or ranking['identity']['snapshot']!=result['inputs']['snapshot_sha256']):
        raise ValueError('Private evaluation/ranking identity mismatch')
    table=frame(evaluation,'metrics').sort('customer_id')
    expected={'customer_id','fold_id','method','segment','relevant_items','available_relevant_items','repeat_relevant_items',*METRICS}
    if (set(table.columns)!=expected or any(table.null_count().row(0))
            or table.height!=result['population']['users'] or table['customer_id'].n_unique()!=table.height
            or set(table['fold_id'])!={entry['fold']} or set(table['method'])!={method}
            or not set(table['segment'])<=set(SEGMENTS)
            or ranking['customers']!=table.height
            or ranking['identity']['customers_sha256']!=digest(table['customer_id'].to_list())):
        raise ValueError('Private table population/schema mismatch')
    values=table.select(*METRICS).to_numpy()
    if not np.isfinite(values).all() or (values<0).any() or (values>1).any():
        raise ValueError('Invalid individual metrics')
    if (table.filter((pl.col('relevant_items')<1) | (pl.col('available_relevant_items')<0)
            | (pl.col('available_relevant_items')>pl.col('relevant_items'))
            | (pl.col('repeat_relevant_items')<0) | (pl.col('repeat_relevant_items')>pl.col('relevant_items'))).height):
        raise ValueError('Invalid per-buyer denominators')
    for m in METRICS:
        if not np.isclose(table[m].mean(),evaluation.metrics[m],rtol=0,atol=1e-10):
            raise ValueError('Individual/global metric mismatch')
    for segment in SEGMENTS:
        subset=table.filter(pl.col('segment')==segment); expected=evaluation.segments[segment]
        if subset.height!=expected['users']: raise ValueError('Segment population mismatch')
        if subset.height and any(not np.isclose(subset[m].mean(),expected['metrics'][m],rtol=0,atol=1e-10) for m in METRICS):
            raise ValueError('Individual/segment metric mismatch')
    denom=evaluation.denominators
    if (table['relevant_items'].sum()!=denom['relevant_items']
            or (table['relevant_items']-table['available_relevant_items']).sum()!=denom['out_of_catalog_relevant_items']
            or not np.isclose((table['available_relevant_items']/table['relevant_items']).mean(),denom['mean_available_recall_ceiling'],rtol=0,atol=1e-10)):
        raise ValueError('Individual/global denominator mismatch')
    return evaluation, table, {'fold':entry['fold'],'method':method,'run_id':entry['run_id'],
        'manifest_sha256':sha256(root/'manifest.json'),'metrics_sha256':evaluation.files['metrics'].sha256,
        'ranking_sha256':result['artifacts'][method]['ranking_sha256'],
        'labels_sha256':evaluation.identity['labels_sha256'],'buyers':table.height,
        'metrics_bytes_verified':True,'ranking_bytes_rehashed':False}


def verified_campaign(entries, size, config):
    if size not in ('demo','full'):raise ValueError('Pilot is not a selection campaign')
    indexed={};reports={};tables={};evaluations=[];methods={};source=None
    for entry in entries:
        key=(entry['fold'],entry['role'])
        if key in indexed or entry['fold'] not in FOLDS or entry['role'] not in ROLES:
            raise ValueError('Duplicate or unauthorized campaign source')
        indexed[key]=entry;reports[key]=closed_report(entry,size,config)
    for fold in FOLDS:
        if (fold,'Q1') not in reports:raise ValueError('Complete three-fold Q1 baseline required')
        base=reports[fold,'Q1'];q1=indexed[fold,'Q1']
        current=(base['inputs']['source_sha256'],base['inputs']['dataset_manifest_sha256'])
        if source is not None and source!=current:raise ValueError('Campaign dataset changed between folds')
        source=current;tables[fold]={}
        for role in ROLES:
            if (fold,role) not in reports:continue
            result=reports[fold,role];entry=indexed[fold,role]
            if result['inputs']!=base['inputs'] or result['population']!=base['population']:
                raise ValueError('Campaign input/population mismatch')
            if role!='Q1':
                parent=q1 if role=='Q2' else indexed.get((fold,'Q2'))
                if not parent or any(result['reference'][k]!=parent[k] for k in ('run_id','archive_sha256')):
                    raise ValueError('Campaign ancestry mismatch')
                if role in ('RH','RG') and any(result['reference']['baseline'][k]!=q1[k] for k in ('run_id','archive_sha256')):
                    raise ValueError('Ranker baseline ancestry mismatch')
                for m in ('P','H'):
                    if result['methods'][m]!=base['methods'][m] or result['artifacts'][m]!=base['artifacts'][m]:
                        raise ValueError('Copied baseline changed')
                if role in ('RH','RG'):
                    graph=reports[fold,'Q2']
                    if result['methods']['G']!=graph['methods']['G'] or result['artifacts']['G']!=graph['artifacts']['G']:
                        raise ValueError('Copied graph changed')
            for method in ROLES[role][1]:
                evaluated,table,identity=metric_table(entry,result,method)
                if 'H' in tables[fold]:
                    columns=['customer_id','fold_id','segment','relevant_items','available_relevant_items','repeat_relevant_items']
                    if not table.select(columns).equals(tables[fold]['H'].select(columns)):
                        raise ValueError('Buyers or denominators differ between methods')
                    if evaluated.identity['labels_sha256']!=next(e['labels_sha256'] for e in evaluations if e['fold']==fold and e['method']=='H'):
                        raise ValueError('Labels differ between methods')
                tables[fold][method]=table;evaluations.append(identity)
                item=methods.setdefault(method,{'folds':{},'cost_seconds':0.,'peak_memory_bytes':0})
                item['folds'][fold]={k:getattr(evaluated,k) for k in FIELDS}
                if method in ('G','RH','RG'): item['cost_seconds']+=result['costs']['candidate_cost_seconds']
                item['peak_memory_bytes']=max(item['peak_memory_bytes'],result['job_resources'].get('memory.peak',result['job_resources']['peak_rss_bytes']))
        # Q1 P and H were loaded consecutively; explicitly check their row identity too.
        cols=['customer_id','segment','relevant_items','available_relevant_items','repeat_relevant_items']
        if not tables[fold]['P'].select(cols).equals(tables[fold]['H'].select(cols)):
            raise ValueError('Q1 methods have different per-buyer populations')
        labels={e['labels_sha256'] for e in evaluations if e['fold']==fold}
        if len(labels)!=1:raise ValueError('Labels differ between methods')
        for reranked,pool in (('RH','H'),('RG','G')):
            if reranked in tables[fold] and not tables[fold][reranked]['recall_at_200'].equals(tables[fold][pool]['recall_at_200']):
                raise ValueError('Reranking changed per-buyer Recall@200')
    for method in ('P','H'):methods[method]['cost_seconds']=None
    sources=[{k:e[k] for k in ('run_id','archive_sha256','role','fold')} for e in sorted(entries,key=lambda e:(e['fold'],e['role']))]
    return tables,methods,evaluations,sources,source
