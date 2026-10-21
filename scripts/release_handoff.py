"""Standard-library contract for T4-A aggregate reports, never service data."""
import json
from pathlib import Path
import re
from quality_handoff import ALLOWED, require, number, count, metrics, no_private_rows


def load_release_config():
    return json.loads((Path(__file__).resolve().parents[1]/'configs/releases/history-v1.json').read_text())


def hash_value(value):
    return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value)


def validate_release_result(result,config):
    try:
        no_private_rows(result)
        require(set(result)=={'stage','protocol','status','dataset','source_commit','data_as_of','verification_exit_code',
            'test_evaluated','quality_accepted','activated','training_performed','inputs','references','package','parity',
            'job_duration_seconds','job_resources','disk'})
        require(result['stage']=='T4-A' and result['protocol']=='history-release-v1' and result['status']=='passed')
        require(all(result[k] is False for k in ('test_evaluated','quality_accepted','activated','training_performed')))
        require(result['data_as_of']==config['data_as_of']=='2020-09-09' and result['verification_exit_code']==0)
        require(result['dataset'] in ('synthetic','hm') and re.fullmatch('[a-f0-9]{40}',result['source_commit']))
        require(set(result['inputs'])=={'source_sha256','dataset_manifest_sha256'} and all(hash_value(v) for v in result['inputs'].values()))
        require(result['references']=={k:config[k] for k in ('selection','baseline','diagnostic')})
        package=result['package'];parity=result['parity']
        require(set(package)=={'release_id','schema_version','manifest_sha256','selection_archive_verified',
            'past_buyers','history_rows','catalog_size','bytes','files'})
        require(re.fullmatch('h-[a-f0-9]{64}',package['release_id']) and package['schema_version']==2)
        require(hash_value(package['manifest_sha256']) and type(package['selection_archive_verified']) is bool)
        require(all(count(package[k],1) for k in ('past_buyers','catalog_size','bytes')) and count(package['history_rows']))
        names={'catalog.parquet','customers.parquet','histories.parquet','popularity.parquet','rules.json','validation-reference.json'}
        files=package['files'];require(isinstance(files,list) and len(files)==6 and {f['name'] for f in files}==names)
        rows={};total=0
        for item in files:
            require(set(item)=={'name','size_bytes','rows','sha256'} and hash_value(item['sha256']) and count(item['size_bytes'],1))
            require(count(item['rows']) if item['name'].endswith('.parquet') else item['rows'] is None)
            total+=item['size_bytes'];rows[item['name']]=item['rows']
        require(package['bytes']>total)
        require(rows['catalog.parquet']==rows['popularity.parquet']==package['catalog_size'])
        require(rows['customers.parquet']==package['past_buyers'] and rows['histories.parquet']==package['history_rows'])
        require(set(parity)=={'exact_top200','metrics_equal','absolute_tolerance','relative_tolerance','buyers','catalog_size',
            'unknown_buyers','out_of_catalog_relevant_items','metrics','legacy_ranking_verified',
            'reference_ranking_sha256','ranking_sha256','metrics_sha256'})
        require(all(parity[k] is True for k in ('exact_top200','metrics_equal','legacy_ranking_verified')))
        require(parity['absolute_tolerance']==1e-10 and parity['relative_tolerance']==0)
        require(count(parity['buyers'],1) and parity['catalog_size']==package['catalog_size'])
        require(count(parity['unknown_buyers']) and parity['unknown_buyers']<=parity['buyers'])
        require(parity['buyers']-parity['unknown_buyers']<=package['past_buyers'])
        require(count(parity['out_of_catalog_relevant_items']))
        metrics(parity['metrics'])
        require(all(hash_value(parity[k]) for k in ('reference_ranking_sha256','ranking_sha256','metrics_sha256')))
        if result['dataset']=='hm':
            require(package['selection_archive_verified'] is True)
            require(result['inputs']=={k:config[k] for k in ('source_sha256','dataset_manifest_sha256')})
            require(parity['buyers']==config['validation_buyers'] and parity['catalog_size']==config['catalog_size'])
        else:require(package['selection_archive_verified'] is False)
        require(number(result['job_duration_seconds'],0,config['max_seconds']))
        require(count(result['job_resources']['peak_rss_bytes'],1))
        disk=result['disk'];require(set(disk)=={'start_free_bytes','min_free_bytes','end_free_bytes'})
        require(all(count(v,config['min_free_bytes']) for v in disk.values()))
        require(disk['min_free_bytes']<=min(disk['start_free_bytes'],disk['end_free_bytes']))
    except (KeyError,TypeError,AttributeError,OverflowError) as error:
        raise ValueError('Malformed release report') from error


def validate_release_execution(result,environment,config=None):
    config=load_release_config() if config is None else config
    validate_release_result(result,config)
    try:
        require(result['source_commit']==environment['source_commit'] and environment['source_clean'] is True)
        require(result['dataset']==environment['dataset'] and environment['release']=={'data_as_of':result['data_as_of']})
        require(count(environment['start_free_bytes'],config['start_free_bytes']))
        require(environment['isolation']=={'network':'none','writable_mounts':['/artifacts/data-jobs/'+environment['run_id']]})
        limits=environment['limits'];effective=environment['effective_limits']
        require(effective['memory_bytes']==limits['memory_bytes'] and effective['nano_cpus']==limits['cpus']*10**9)
        require(all(effective[k]==limits[k] for k in ('polars_threads','omp_threads','mkl_threads')))
        if result['dataset']=='hm':
            for name in ('selection','baseline','diagnostic'):
                require(all(environment['release_references'][name][k]==config[name][k] for k in config[name]))
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError('Malformed release execution') from error


def validate_release_envelope(report,result,environment,config,names):
    require(set(names)<={'manifest.json',*ALLOWED} and {'result.json','environment.json','config.resolved.json'}<=set(names))
    require(config==load_release_config())
    require(report['result']==result and report['source_commit']==environment['source_commit'])
    require(report['run_id']==environment['run_id'] and report['run_id'].startswith('release-'))
    require(report['source_clean'] is True and report.get('oom_killed') is False and report.get('container_exit_code')==0)
    for key in ('resources','limits','effective_limits'):require(report[key]==environment[key])
    validate_release_execution(result,environment,config)
