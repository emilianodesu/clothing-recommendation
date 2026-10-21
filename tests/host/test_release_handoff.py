"""Real host protocol checks; Docker calls are the external boundary."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import data_job
from handoff import package_report, read_report
from release_handoff import validate_release_result, load_release_config
from release_job import main, references_for


def sample_result():
    config=load_release_config()
    files=[{'name':name+'.parquet','sha256':'b'*64,'size_bytes':100,'rows':rows}
        for name,rows in [('catalog',24),('customers',32),('histories',64),('popularity',24)]]
    files.extend({'name':name+'.json','sha256':'b'*64,'size_bytes':100,'rows':None} for name in ('rules','validation-reference'))
    return {'stage':'T4-A','protocol':'history-release-v1','status':'passed','dataset':'synthetic',
        'source_commit':'a'*40,'data_as_of':'2020-09-09','verification_exit_code':0,
        'test_evaluated':False,'quality_accepted':False,'activated':False,'training_performed':False,
        'inputs':{'source_sha256':'c'*64,'dataset_manifest_sha256':'d'*64},
        'references':{name:config[name] for name in ('selection','baseline','diagnostic')},
        'package':{'release_id':'h-'+'e'*64,'schema_version':2,'manifest_sha256':'f'*64,
            'selection_archive_verified':False,'past_buyers':32,'history_rows':64,'catalog_size':24,'bytes':700,'files':files},
        'parity':{'buyers':33,'catalog_size':24,'unknown_buyers':1,'out_of_catalog_relevant_items':1,
            'exact_top200':True,'metrics_equal':True,'legacy_ranking_verified':True,
            'absolute_tolerance':1e-10,'relative_tolerance':0,
            'metrics':{'map_at_12':.96,'ndcg_at_12':.97,'recall_at_200':.98},
            'reference_ranking_sha256':'1'*64,'ranking_sha256':'2'*64,'metrics_sha256':'3'*64},
        'job_duration_seconds':3,'job_resources':{'peak_rss_bytes':123456},
        'disk':{'start_free_bytes':30*2**30,'min_free_bytes':29*2**30,'end_free_bytes':29*2**30}}


class ReleaseHandoffTests(unittest.TestCase):
    def test_rejects_incomplete_parity_metrics_test_or_private_payload(self):
        result=sample_result();validate_release_result(result,load_release_config())
        for path,value in [(('parity','exact_top200'),False),(('parity','metrics','map_at_12'),float('nan')),
            (('parity','buyers'),0),(('data_as_of',),'2020-09-16'),(('test_evaluated',),True),
            (('activated',),True),(('package','schema_version'),1),(('package','files'),[]),
            (('customer_id',),['private']),(('references','baseline','archive_sha256'),'0'*64),
            (('package','past_buyers'),0),(('package','bytes'),1)]:
            bad=deepcopy(result);obj=bad
            for key in path[:-1]:obj=obj[key]
            obj[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):validate_release_result(bad,load_release_config())

    def test_cli_requires_cutoff_and_full_commit_before_docker(self):
        args=['start','--dataset','hm','--data-as-of','2020-09-16','--expected-commit','a'*40]
        with patch('release_job.start') as launch,redirect_stderr(StringIO()):
            self.assertEqual(main(args),1);launch.assert_not_called()
            args[4]='2020-09-09';args[-1]='abc'
            self.assertEqual(main(args),1);launch.assert_not_called()
            args[-1]='a'*40
            self.assertEqual(main(args),0)
            self.assertEqual(launch.call_args.kwargs['release_options'],{'data_as_of':'2020-09-09'})
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):
            references_for(Path(tmp),load_release_config())

    def test_release_launch_enforces_isolation_resources_and_exports_failed_preflight(self):
        # Reuse the external Docker double; exercise the real launch and export.
        import test_data_job_resources
        fixture=test_data_job_resources.ResourceLaunchTests()
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        config=fixture.root/'configs/releases/history-v1.json';config.parent.mkdir()
        config.write_text(json.dumps(load_release_config()))
        def capture(env,*command,**kwargs):
            raw=fixture.capture(env,*command,**kwargs)
            if command[:2]==('docker','inspect'):
                obj=json.loads(raw)[0];rid=fixture.containers[command[2]]
                obj['HostConfig']['NetworkMode']='none'
                obj['Mounts']=[{'Destination':p,'RW':rw} for p,rw in
                    [('/data',False),('/artifacts',False),('/artifacts/data-jobs/'+rid,True)]]
                return json.dumps([obj])
            return raw
        from collections import namedtuple
        disk=namedtuple('disk','total used free')(100*2**30,20*2**30,80*2**30)
        with patch.object(data_job,'capture',side_effect=capture),patch.object(data_job.shutil,'disk_usage',return_value=disk),redirect_stdout(StringIO()):
            rid=data_job.start('fixture','default','synthetic','a'*40,'mac24',release_options={'data_as_of':'2020-09-09'})
        self.assertTrue(rid.startswith('release-'))
        run=json.loads((fixture.root/'artifacts/data-jobs'/rid/'environment.json').read_text())
        self.assertNotIn('quality_budget_seconds',run)
        create=next(c for c in fixture.commands if c[:2]==('docker','create'))
        self.assertEqual(create[create.index('--memory')+1],'10g')
        self.assertIn('hm_recommender.releases.worker',create)
        self.assertIn('--supervise',create)
        self.assertEqual(run['isolation']['writable_mounts'],['/artifacts/data-jobs/'+rid])
        fixture.engine_memory=8*2**30
        with redirect_stdout(StringIO()),self.assertRaises(ValueError):
            data_job.start('fixture','default','hm','a'*40,'mac24',release_options={'data_as_of':'2020-09-09'})
        failed=next(p for p in fixture.runs() if p.parent.name!=rid)
        report=read_report(data_job.export(failed.parent.name))
        self.assertEqual(report['kind'],'release-handoff')
        self.assertEqual(report['status'],'failed')
        spec=importlib.util.spec_from_file_location('github_handoff_release',Path(data_job.__file__).with_name('github-handoff.py'))
        github=importlib.util.module_from_spec(spec);spec.loader.exec_module(github)
        self.assertIn('release-handoff',github.marker_for(report['run_id']))

    def test_closed_release_report_roundtrip_and_rejects_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'release-fixture';root.mkdir()
            result=sample_result();config=load_release_config()
            limits={'memory_bytes':5*2**30,'cpus':3,'polars_threads':2,'omp_threads':2,'mkl_threads':2}
            effective=dict(limits,nano_cpus=3*10**9,memory_swap_bytes=10*2**30);effective.pop('cpus')
            env={'run_id':root.name,'source_commit':'a'*40,'dataset':'synthetic','release':{'data_as_of':'2020-09-09'},
                'source_clean':True,'resources':{'name':'base'},'limits':limits,'effective_limits':effective,
                'start_free_bytes':30*2**30,'isolation':{'network':'none','writable_mounts':['/artifacts/data-jobs/'+root.name]}}
            for name,value in [('environment',env),('result',result),('config.resolved',config)]:
                (root/(name+'.json')).write_text(json.dumps(value))
            report={'schema_version':1,'kind':'release-handoff','run_id':root.name,'machine_id':'fixture','context':'fixture',
                'source_commit':'a'*40,'source_clean':True,'status':'passed','steps':[],'result':result,
                'container_exit_code':0,'oom_killed':False,**{k:env[k] for k in ('resources','limits','effective_limits')}}
            archive=package_report(root,report)
            self.assertEqual(read_report(archive)['result'],result)
            archive.unlink()
            (root/'private.parquet').write_bytes(b'private')
            report['steps']=[{'name':'private','log':'private.parquet','status':'passed','exit_code':0}]
            with self.assertRaises(ValueError):package_report(root,report)
