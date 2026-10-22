import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from handoff import package_report,read_report,write_json


def fixture():
    rid='events-test-20261022T000000Z-12345678';sha='a'*64
    resources={name:dict(image_id='sha256:'+sha,memory_bytes=memory,nano_cpus=cpu,
        cgroup_peak_bytes=1000000,swap_peak_bytes=0,oom_killed=False)
        for name,memory,cpu in [('api',4*2**30,2*10**9),('ui',2**29,500000000),('postgres',2**29,0),('seed',3*2**30,10**9)]}
    checks=dict(initial_parity_requests=4,new_events=4,duplicate_status=200,conflict_status=409,
        purchase_changes_profile=True,views_clicks_not_purchases=True,late_event_order=True,
        refresh_idempotent=True,base_unchanged=True,profile_sha256=sha,revision=3,
        data_as_of='2020-09-09',processed_events=4)
    source=dict(run_id=None,archive_sha256=None,release_id='h-'+sha,manifest_sha256=sha,dataset='synthetic')
    result=dict(stage='T5',protocol='events-v1',status='passed',source_tree_sha256=sha,
        source=source,stream_id='a0000000-0000-0000-0000-000000000000',mode='synthetic',checks=checks,
        seed=dict(daily_rows=256,purchases=256,first_day='2020-08-12',last_day='2020-09-02',data_as_of='2020-09-09'),
        resources=resources,host=dict(host='127.0.0.1',api_port=8000,ui_port=8501,bindings_verified=True,ui_healthy=True),
        restart_persistent=True,source_unchanged=True,test_evaluated=False,training_performed=False,
        load_benchmark=False,duration_seconds=25)
    report=dict(schema_version=1,kind='events-handoff',run_id=rid,machine_id='test',context='colima',
        source_commit='a'*40,source_clean=True,started_at='2026-10-22T00:00:00Z',finished_at='2026-10-22T00:01:00Z',
        status='passed',warnings=[],result=result,steps=[dict(name='events',log='job.log',status='passed',exit_code=0)])
    return report,dict(run_id=rid,source_commit='a'*40,source_tree_sha256=sha,uv_lock_sha256=sha)


class EventsHandoffTests(unittest.TestCase):
    def test_demo_does_not_generate_new_credentials_over_existing_volumes(self):
        import project
        with tempfile.TemporaryDirectory() as d,patch.object(project.subprocess,'run',return_value=type('Result',(),{'stdout':'existing_postgres\n'})()),patch.object(project,'prepare_configuration') as prepare:
            with self.assertRaises(ValueError):project.prepare_start_configuration(Path(d))
            prepare.assert_not_called()

    def test_documented_events_command_reaches_runner(self):
        import project,events_check
        argv=['scripts/project.py','events-check','--machine-id','mac24','--expected-commit','a'*40,
            '--source-run-id','release-test-full']
        with patch.object(sys,'argv',argv),patch.object(events_check,'run',return_value=0) as run:
            self.assertEqual(project.main(),0)
        run.assert_called_once_with(events_check.ROOT,'mac24','a'*40,'release-test-full')

    def package(self,root,report,env):
        folder=root/report['run_id'];folder.mkdir()
        write_json(folder/'environment.json',env);write_json(folder/'result.json',report['result'])
        (folder/'commit.txt').write_text(report['source_commit']+'\n');(folder/'job.log').write_text('aggregates\n')
        return package_report(folder,report)

    def test_events_roundtrip_and_private_github_contract(self):
        report,env=fixture()
        spec=importlib.util.spec_from_file_location('events_github',ROOT/'scripts/github-handoff.py')
        github=importlib.util.module_from_spec(spec);spec.loader.exec_module(github)
        with tempfile.TemporaryDirectory() as d:
            archive=self.package(Path(d),report,env)
            self.assertEqual(read_report(archive)['result'],report['result'])
            self.assertEqual(github.validate_archive(archive)['kind'],'events-handoff')
            self.assertEqual(github.marker_for(report['run_id']),'<!-- hmrec-events-handoff-v1 -->')

    def test_invalid_success_or_private_rows_are_rejected(self):
        for change in [lambda r:r['result']['checks'].update(new_events=5),
            lambda r:r['result'].update(restart_persistent=False),
            lambda r:r['result']['seed'].update(last_day='2020-09-09'),
            lambda r:r['result']['resources']['seed'].update(memory_bytes=5*2**30),
            lambda r:r['result']['resources']['api'].update(oom_killed=True),
            lambda r:r['result']['host'].update(bindings_verified=False),
            lambda r:r['result'].update(customer_id='private'),
            lambda r:r['result']['checks'].update(profile_sha256='bad'),
            lambda r:r['result'].update(training_performed=True)]:
            report,env=fixture();change(report)
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError):self.package(Path(d),report,env)

    def test_failed_attempt_exports_partial_aggregates(self):
        report,env=fixture();report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        report['result']=dict(stage='T5',status='failed',phase='seed',error_type='ValueError')
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(read_report(self.package(Path(d),report,env))['status'],'failed')
