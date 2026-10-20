import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from handoff import package_report,read_report
from quality_handoff import validate_quality_result
spec=importlib.util.spec_from_file_location('quality_github',Path(__file__).resolve().parents[2]/'scripts/github-handoff.py')
github=importlib.util.module_from_spec(spec);spec.loader.exec_module(github)
ROOT=Path(__file__).resolve().parents[2]


def fixture(directory):
    config=json.loads((ROOT/'configs/quality/quality-v1.json').read_text())
    metrics={k:.5 for k in ('map_at_12','ndcg_at_12','recall_at_200')}
    method={'population':{'users':1,'catalog_size':2},'metrics':metrics,
        'segments':{s:{'users':int(s=='recent28d'),'metrics':metrics if s=='recent28d' else None} for s in ('recent28d','history29_90d','older_history','no_history')},
        'denominators':{'relevant_items':2,'out_of_catalog_relevant_items':0,'mean_available_recall_ceiling':1},
        'exposure':{str(k):{'k':2,'distinct_items':2,'coverage':1,'unique_ordered_lists':1,'unique_sets':1,'effective_items':2,'top_exposure_share':{'1':.5,'5':1,'10':1,'50':1}} for k in (12,200)},
        'targets':{k:{'users':1,'relevant_items':1,'metrics':metrics} for k in ('repeat','discovery')}}
    result={'stage':'Q1','protocol':'quality-v1','status':'passed','source_commit':'a'*40,'test_evaluated':False,'quality_accepted':False,'verification_exit_code':0,'size':'demo',
        'fold':{'fold_id':'V3','role':'evaluation','parent_fold':None,'cutoff':'2020-09-09','label_end':'2020-09-16'},
        'population':{'users':1,'catalog_size':2,'validation_buyers':1,'sampled_out_users':0},'methods':{'P':copy.deepcopy(method),'H':copy.deepcopy(method)},
        'inputs':{'max_feature_date':'2020-09-08',**{k:'a'*64 for k in ('source_sha256','dataset_manifest_sha256','snapshot_sha256')},
            'protocol_sha256':hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()},
        'artifacts':{m:{'ranking_sha256':'a'*64,'metrics_sha256':'b'*64} for m in ('P','H')},
        'parity':{'legacy_ranking_verified':True},'job_duration_seconds':1,'job_resources':{'peak_rss_bytes':1024}}
    env={'run_id':directory.name,'source_commit':'a'*40,'quality':{'stage':'baselines','fold':'V3','size':'demo'},
        'resources':{'name':'base'},'limits':{'memory_bytes':5*2**30,'cpus':3,'polars_threads':2,'omp_threads':2,'mkl_threads':2},
        'effective_limits':{'memory_bytes':5*2**30,'nano_cpus':3*10**9,'polars_threads':2,'omp_threads':2,'mkl_threads':2},
        'isolation':{'network':'none','writable_mounts':['/artifacts/data-jobs/'+directory.name]}}
    report={'schema_version':1,'kind':'quality-handoff','run_id':directory.name,'machine_id':'local','context':'colima','source_commit':'a'*40,'source_clean':True,
        'status':'passed','steps':[{'name':'quality','log':'job.log','status':'passed','exit_code':0}],'result':result,
        'container_exit_code':0,'oom_killed':False,**{k:env[k] for k in ('resources','limits','effective_limits')}}
    result['disk']={'start_free_bytes':21*2**30,'min_free_bytes':20*2**30,'end_free_bytes':20*2**30}
    env.update(start_free_bytes=21*2**30,quality_budget_seconds=7200,quality_budget={'pilot':0,'full':0,'summary':0,'demo':1})
    return result,env,config,report


def package(directory,result,env,config,report):
    for name,value in [('result.json',result),('environment.json',env),('config.resolved.json',config)]:
        (directory/name).write_text(json.dumps(value))
    (directory/'job.log').write_text('Closed Q1 report\n')
    return package_report(directory,report)


class QualityHandoffTests(unittest.TestCase):
    def test_roundtrip_private_marker_and_cli_status(self):
        for failed in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                directory=Path(tmp)/'quality-local-test';directory.mkdir();result,env,config,report=fixture(directory)
                if failed:
                    result={'status':'failed','failure_reason':'timeout'};report.update(result=result,status='failed',container_exit_code=1)
                    report['steps'][0].update(status='failed',exit_code=1)
                archive=package(directory,result,env,config,report)
                self.assertEqual(github.validate_archive(archive)['kind'],'quality-handoff')
                self.assertEqual(github.marker_for(directory.name),github.QUALITY_MARKER)
                self.assertEqual(subprocess.run([sys.executable,str(ROOT/'scripts/read-handoff.py'),str(archive)],capture_output=True).returncode,1 if failed else 0)

    def test_incomplete_or_inconsistent_results_rejected(self):
        for field,value in [('fold',{}),('test_evaluated',True),('verification_exit_code',1),('inputs',{}),('parity',{})]:
            with self.subTest(field=field):
                result,*_=fixture(Path('quality-local-test'));result[field]=value
                with self.assertRaises(ValueError):validate_quality_result(result)
        for mutation in ('nan','future','population','limits','private_file','private_rows','config'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as tmp:
                directory=Path(tmp)/'quality-local-test';directory.mkdir();result,env,config,report=fixture(directory)
                if mutation=='nan':result['methods']['H']['metrics']['map_at_12']=float('nan')
                if mutation=='future':result['inputs']['max_feature_date']='2020-09-09'
                if mutation=='population':result['methods']['H']['population']['users']=2
                if mutation=='limits':report.pop('effective_limits')
                if mutation=='private_file':
                    (directory/'customers.log').write_text('private');report['steps'].append({'name':'extra','log':'customers.log','status':'passed','exit_code':0})
                if mutation=='private_rows':result['customer_id']=['private']
                if mutation=='config':config['history_days']=27
                with self.assertRaises((ValueError,KeyError)):package(directory,result,env,config,report)

    def test_reference_requires_closed_full_history_report(self):
        from quality_job import reference_for
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):reference_for(Path(tmp),'diagnose-missing')

    def test_closed_report_requires_operational_evidence_and_fixed_protocol(self):
        for mutation in ('disk','budget','over_budget','coherent_config','spent','small_reservation','low_disk'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as tmp:
                directory=Path(tmp)/'quality-local-test';directory.mkdir();result,env,config,report=fixture(directory)
                if mutation=='disk':result.pop('disk',None)
                if mutation=='budget':env.pop('quality_budget_seconds',None)
                if mutation=='over_budget':result['job_duration_seconds']=100000000
                if mutation=='spent':env['quality_budget']['pilot']=7201
                if mutation=='small_reservation':env['quality_budget_seconds']=.5
                if mutation=='low_disk':result['disk']['end_free_bytes']=1
                if mutation=='coherent_config':
                    config['history_days']=27
                    result['inputs']['protocol_sha256']=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                with self.assertRaises(ValueError):package(directory,result,env,config,report)
