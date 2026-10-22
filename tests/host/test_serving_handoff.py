import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from handoff import package_report,read_report,write_json


def fixture():
    rid='serving-test-20261021T000000Z-01234567';code='c'*64
    sources={k:dict(run_id='release-test-'+k,archive_sha256='a'*64,release_id='h-'+v*64,
        manifest_sha256='b'*64,dataset='synthetic') for k,v in [('reference','d'),('candidate','e')]}
    checks={k:dict(release_id=sources['reference' if k in ('reference','rollback') else 'candidate']['release_id'],
        source_tree_sha256=code,sampled_buyers=3,checked_requests=11,filtered_cases=4,http_parity=True,
        response_sha256=('f' if k in ('reference','rollback') else 'a')*64,catalog_size=24,past_buyers=32,
        request_seconds_mean=.001,request_seconds_max=.002,load_benchmark=False)
        for k in ('reference','candidate','restart','rollback','final')}
    resource=dict(image_id='sha256:'+'a'*64,memory_bytes=4*2**30,nano_cpus=2*10**9,
        cgroup_peak_bytes=1000000,swap_peak_bytes=0,oom_killed=False,running=True,threads=2)
    result=dict(stage='T4-B',protocol='history-serving-v1',status='passed',source_tree_sha256=code,
        sources=sources,checks=checks,resources={k:resource.copy() for k in ('before_restart','after_restart')},
        final_release_id=sources['candidate']['release_id'],activated=True,test_evaluated=False,
        quality_accepted=False,training_performed=False,duration_seconds=10)
    report=dict(schema_version=1,kind='serving-handoff',run_id=rid,machine_id='test',context='colima',
        started_at='2026-10-21T00:00:00+00:00',finished_at='2026-10-21T00:00:10+00:00',
        source_commit='a'*40,source_clean=True,status='passed',warnings=[],result=result,
        steps=[dict(name='serving',log='job.log',status='passed',exit_code=0)])
    env=dict(run_id=rid,source_commit='a'*40,source_tree_sha256=code)
    return report,env


class ServingHandoffTests(unittest.TestCase):
    def v2(self):
        report,env=fixture();result=report['result'];result['protocol']='history-serving-v2'
        result['host_checks']={}
        for key,check in result['checks'].items():
            check['host_reference_sha256']=check['response_sha256']
            result['host_checks'][key]=dict(release_id=check['release_id'],source_tree_sha256=check['source_tree_sha256'],
                host='127.0.0.1',port=8000,binding_verified=True,health_live=True,health_ready=True,
                checked_requests=2,http_parity=True,response_sha256=check['host_reference_sha256'],
                request_seconds_mean=.001,request_seconds_max=.002,load_benchmark=False)
        return report,env

    def test_v2_roundtrip_requires_host_evidence_for_every_lifecycle_stage(self):
        report,env=self.v2()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(read_report(self.package(Path(d),report,env))['result'],report['result'])
        mutations=[lambda r:r['result'].pop('host_checks'),
            lambda r:r['result']['host_checks'].pop('restart'),
            lambda r:r['result']['host_checks']['rollback'].update(release_id='h-'+'0'*64),
            lambda r:r['result']['host_checks']['final'].update(response_sha256='0'*64),
            lambda r:r['result']['host_checks']['candidate'].update(port=9000),
            lambda r:r['result']['host_checks']['reference'].update(binding_verified=False),
            lambda r:r['result']['host_checks']['final'].update(host='0.0.0.0'),
            lambda r:r['result']['host_checks']['reference'].update(checked_requests=0),
            lambda r:r['result']['checks']['final'].pop('host_reference_sha256')]
        for mutate in mutations:
            with tempfile.TemporaryDirectory() as d, self.subTest(mutation=mutate):
                report,env=self.v2();mutate(report)
                with self.assertRaises(ValueError):self.package(Path(d),report,env)

    def test_failed_v2_host_check_is_exported_without_private_payload(self):
        report,env=self.v2();report['status']='failed'
        report['steps'][0].update(status='failed',exit_code=1)
        report['result'].update(status='failed',phase='candidate-host',error_type='URLError')
        report['result']['host_checks'].pop('candidate')
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(read_report(self.package(Path(d),report,env))['status'],'failed')

    def test_project_down_includes_all_profiles_and_preserves_volumes(self):
        import project
        values = {'COMPOSE_PROJECT_NAME': 'isolated-test'}
        with patch.object(sys, 'argv', ['scripts/project.py', 'down']), \
                patch.object(project, 'read_configuration', return_value=values), \
                patch.object(project, 'compose') as compose:
            self.assertEqual(project.main(), 0)
        compose.assert_called_once_with(project.ROOT, values, '--profile', '*', 'down')

    def test_documented_project_command_dispatches_all_serving_options(self):
        import project
        import serving_check
        sha = 'a' * 40
        argv = ['scripts/project.py', 'serving-check', '--machine-id', 'mac24',
            '--expected-commit', sha, '--reference-run-id', 'release-test-reference',
            '--candidate-run-id', 'release-test-candidate']
        # The runner is the external Docker/filesystem boundary, after both real parsers.
        with patch.object(sys, 'argv', argv), patch.object(serving_check, 'run', return_value=0) as run:
            self.assertEqual(project.main(), 0)
        run.assert_called_once_with(serving_check.ROOT, 'mac24', sha,
            'release-test-reference', 'release-test-candidate')

    def package(self,root,report,env):
        run=root/report['run_id'];run.mkdir()
        write_json(run/'environment.json',env);write_json(run/'result.json',report['result'])
        (run/'commit.txt').write_text(report['source_commit']+'\n');(run/'job.log').write_text('aggregate-only\n')
        return package_report(run,report)

    def test_complete_serving_report_roundtrip_and_github_prefix(self):
        spec=importlib.util.spec_from_file_location('serving_github',ROOT/'scripts/github-handoff.py')
        github=importlib.util.module_from_spec(spec);spec.loader.exec_module(github)
        with tempfile.TemporaryDirectory() as d:
            report,env=fixture();archive=self.package(Path(d),report,env)
            self.assertEqual(read_report(archive)['result'],report['result'])
            self.assertEqual(github.validate_archive(archive)['kind'],'serving-handoff')
            self.assertEqual(github.tag_for(report['run_id']),'handoff-'+report['run_id'])

    def test_invalid_or_private_serving_evidence_is_rejected(self):
        mutations=[lambda r:r['result']['checks'].pop('restart'),
            lambda r:r['result']['checks']['final'].update(checked_requests=0),
            lambda r:r['result']['checks']['rollback'].update(response_sha256='0'*64),
            lambda r:r['result']['resources']['after_restart'].update(oom_killed=True),
            lambda r:r['result']['resources']['before_restart'].update(memory_bytes=5*2**30),
            lambda r:r['result'].update(customer_id='private'),
            lambda r:r.update(finished_at=None),
            lambda r:r['result']['checks']['restart'].update(past_buyers=100),
            lambda r:r['result'].update(quality_accepted=True)]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as d:
                report,env=fixture();mutate(report)
                with self.assertRaises(ValueError):self.package(Path(d),report,env)

    def test_failed_serving_attempt_is_exportable_without_success_checks(self):
        report,env=fixture();report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        report['result']={'stage':'T4-B','status':'failed','phase':'candidate','error_type':'RuntimeError'}
        with tempfile.TemporaryDirectory() as d:
            archive=self.package(Path(d),report,env)
            self.assertEqual(read_report(archive)['status'],'failed')
