import copy
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from concurrency_handoff import overlap,validate
from benchmark import phase
import concurrency_transition as t
from handoff import package_report,read_report,write_json

class Concurrency(unittest.TestCase):
    def test_overlap_includes_omitted_arrivals(self):
        cases=[{'group':g} for g in ('history','known_fallback','unknown')]
        trace=dict(started=100,scheduled=6,rate=2,samples=[dict(group='history',ok=True,due=100,finished=100.1,latency=.1,service=.09)])
        result=overlap(trace,cases,100,102)
        self.assertEqual(result['scheduled'],4);self.assertEqual(result['omitted'],3)
        self.assertEqual(result['error_fraction'],.75)
    def test_trace_keeps_default_report_and_records_arrivals(self):
        trace={};called=[]
        result=phase([{'group':'history'}],rate=20,seconds=.1,workers=1,request=lambda c:True,max_lag=1,trace=trace,on_start=lambda:called.append(True))
        self.assertEqual(called,[True]);self.assertEqual(result['scheduled'],2)
        self.assertEqual(len(trace['samples']),2);self.assertNotIn('samples',result)
    def test_stop_validates_all_targets_before_mutation(self):
        row=dict(container_id='id',image_id='image',memory_bytes=1,nano_cpus=1)
        with patch.object(t,'inspect',return_value=dict(row,image_id='wrong',running=True)),patch.object(t,'command') as cmd:
            with self.assertRaises(ValueError):t.stop([row])
            cmd.assert_not_called()
    def test_restore_database_before_api_and_tracking(self):
        rows=[dict(container_id=s,image_id='image',memory_bytes=1,nano_cpus=1,service=s) for s in ('api','postgres','mlflow')]
        running=set()
        def inspect(cid):return dict(next(r for r in rows if r['container_id']==cid),running=cid in running,health='healthy')
        def command(args,**kwargs):running.add(args[-1])
        with patch.object(t,'inspect',side_effect=inspect),patch.object(t,'command',side_effect=command) as cmd:
            t.restore(rows)
            self.assertEqual([c.args[0] for c in cmd.call_args_list],[['docker','start',s] for s in ('postgres','api','mlflow')])
    def test_wrong_restoration_identity_rejected(self):
        cap=dict(result=dict(containers=[dict(scope='other',service='api',project_sha256='p',container_id='api'),dict(scope='other',service='postgres',project_sha256='p',container_id='wrong')]))
        with self.assertRaises(ValueError):t.targets(cap,dict(result=dict(destination_postgres=dict(container_id='expected'))))
    def test_worker_timeout_terminates_child_and_writes_completion(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'worker.json';child=Mock();child.wait.side_effect=[subprocess.TimeoutExpired('build',540),0]
            with patch.object(sys,'argv',['worker','config',str(output)]),patch('subprocess.Popen',return_value=child):
                with self.assertRaises(SystemExit) as e:runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/concurrency_worker.py'),run_name='__main__')
            self.assertEqual(e.exception.code,124);child.terminate.assert_called_once()
            self.assertTrue(json.loads(output.with_suffix('.exit.json').read_text())['timed_out'])
    def test_failed_report_roundtrip_and_private_field_rejection(self):
        r=dict(protocol='scheduler-concurrency-v1',status='failed',warmup=None,measurement=None,overlap=None,worker_seconds=0.,build_seconds=0.,build_release_id=None,build_exit_code=None,resources_ok=False,source_preserved=False,auxiliaries_restored=True,auxiliary_transitions=[],generator_cpu_seconds=0.,generator_max_rss_bytes=0,error_type='ValueError',training_performed=False,test_evaluated=False,t8_closed=False)
        env=dict(source_commit='a'*40,**{k:'b'*64 for k in ('runner_tree_sha256','baseline_archive_sha256','capacity_archive_sha256','cases_sha256','api_code_sha256','scheduler_code_sha256','state_before_sha256','state_after_sha256')})
        report=dict(schema_version=1,kind='concurrency-handoff',run_id='concurrency-local-test',machine_id='local',context='local',source_commit='a'*40,source_clean=True,started_at='2026-10-23T00:00:00+00:00',finished_at='2026-10-23T00:00:01+00:00',status='failed',warnings=[],steps=[dict(name='concurrency',log='job.log',status='failed',exit_code=1),dict(name='resources',log='resources.log',status='failed',exit_code=1)],result=r)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/report['run_id'];folder.mkdir()
            for name,value in [('result.json',r),('environment.json',env),('resources.log',[])]:write_json(folder/name,value)
            (folder/'commit.txt').write_text('a'*40+'\n');(folder/'job.log').write_text('concurrency T8-B\nstatus: failed\n')
            archive=package_report(folder,report);self.assertEqual(read_report(archive)['result'],r)
            spec=importlib.util.spec_from_file_location('gh_concurrency',Path(__file__).resolve().parents[2]/'scripts/github-handoff.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            self.assertEqual(mod.validate_archive(archive)['kind'],'concurrency-handoff')
            r['customer_id']='private'
            with self.assertRaises(ValueError):validate(report,r,env,[])
