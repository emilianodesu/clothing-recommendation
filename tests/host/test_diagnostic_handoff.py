import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from handoff import package_report, read_report
import data_job
import diagnose_job
import test_data_job_resources as resource_tests
spec = importlib.util.spec_from_file_location('github_diagnostic_handoff', Path(__file__).resolve().parents[2] / 'scripts/github-handoff.py')
github = importlib.util.module_from_spec(spec); spec.loader.exec_module(github)


class DiagnosticHandoffTests(unittest.TestCase):
    def test_diagnostic_roundtrip_is_distinct_from_training_and_requires_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, replay in [('valid', True), ('invalid', False)]:
                directory = Path(tmp) / ('diagnose-mac24-' + name); directory.mkdir()
                (directory / 'job.log').write_text('Diagnostics completed\n')
                result = {'stage': 'T3-diagnostics', 'status': 'passed', 'verification_exit_code': 0,
                          'diagnostic': {'replay_verified': replay, 'quality_accepted': False, 'test_evaluated': False}}
                (directory / 'result.json').write_text(json.dumps(result))
                report = dict(schema_version=1, kind='diagnostic-handoff', run_id=directory.name,
                    machine_id='mac24', context='colima-hm-project', source_commit='a'*40, source_clean=True,
                    status='passed', result=result, steps=[dict(name='diagnose', log='job.log', status='passed', exit_code=0)])
                if not replay:
                    with self.assertRaises(ValueError): package_report(directory, report)
                else:
                    archive = package_report(directory, report)
                    self.assertEqual(github.validate_archive(archive)['kind'], 'diagnostic-handoff')
                    self.assertEqual(read_report(archive)['status'], 'passed')
                    self.assertEqual(github.tag_for(directory.name), 'handoff-' + directory.name)


class DiagnosticLaunchTests(unittest.TestCase):
    setUp = resource_tests.ResourceLaunchTests.setUp
    capture = resource_tests.ResourceLaunchTests.capture

    def test_diagnostic_mounts_only_its_output_writable_and_checks_effective_isolation(self):
        (self.root / 'configs/diagnostics').mkdir()
        (self.root / 'configs/diagnostics/validation.json').write_text('{}')
        original_capture = self.capture
        def capture(env, *command, **kwargs):
            output = original_capture(env, *command, **kwargs)
            if command[:2] == ('docker', 'inspect'):
                info = json.loads(output)[0]
                create = next(c for c in self.commands if c[:2] == ('docker', 'create'))
                mounts = []
                for i, value in enumerate(create):
                    if value == '--mount':
                        parts = create[i+1].split(',')
                        fields = dict(p.split('=', 1) for p in parts if '=' in p)
                        mounts.append({'Destination': fields['target'], 'RW': 'readonly' not in parts})
                info['Mounts'] = mounts
                info['HostConfig']['NetworkMode'] = 'none'
                return json.dumps([info])
            return output
        source = {'run_id': 'train-fixture', 'config': {'demo': True}}
        with mock.patch.object(data_job, 'capture', side_effect=capture), mock.patch.object(diagnose_job, 'source_for', return_value=source):
            run_id = data_job.start('fixture', 'default', 'synthetic', 'a'*40, 'mac24', diagnostic_run='train-fixture')
        run = json.loads((self.root / 'artifacts/data-jobs' / run_id / 'environment.json').read_text())
        self.assertTrue(run_id.startswith('diagnose-'))
        self.assertEqual(run['diagnostic_source'], source)
        self.assertEqual(run['isolation']['network'], 'none')
        self.assertEqual(run['isolation']['writable_mounts'], ['/artifacts/data-jobs/' + run_id])
        create = next(c for c in self.commands if c[:2] == ('docker', 'create'))
        self.assertIn('hm_recommender.model.diagnostic_worker', create)
        self.assertIn('type=bind,source=' + str((self.root/'data').resolve()) + ',target=/data,readonly', create)
        self.assertIn('type=bind,source=' + str((self.root/'artifacts').resolve()) + ',target=/artifacts,readonly', create)
        self.assertFalse(any('mlflow' in call.args for call in self.build.call_args_list))

class DiagnosticSourceTests(unittest.TestCase):
    def test_source_is_bound_to_closed_report_and_current_dependency_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            directory = artifacts / 'handoff/train-fixture'; directory.mkdir(parents=True)
            environment = {'run_id': 'train-fixture', 'experiment_id': 'train-fixture', 'source_commit': 'a'*40, 'uv_lock_sha256': 'b'*64}
            result = {'stage': 'T3', 'status': 'passed', 'verification_exit_code': 0,
                'training': {'status': 'passed', 'config': {'demo': True}, 'rows_consumed': 384, 'input_identity': {'sha256': 'c'*64}},
                'evaluation': {'test_evaluated': False}, 'last_durable_progress': {'sha256': 'd'*64}}
            (directory/'environment.json').write_text(json.dumps(environment))
            (directory/'result.json').write_text(json.dumps(result))
            (directory/'job.log').write_text('completed')
            package_report(directory, dict(schema_version=1, kind='training-handoff', run_id=directory.name,
                machine_id='fixture', context='colima', source_commit='a'*40, source_clean=True, status='passed', result=result,
                steps=[dict(name='train', log='job.log', status='passed', exit_code=0)]))
            source = diagnose_job.source_for(artifacts, 'train-fixture', 'b'*64)
            self.assertEqual(source['checkpoint']['sha256'], 'd'*64)
            self.assertEqual(source['source_commit'], 'a'*40)
            self.assertEqual(len(source['archive_sha256']), 64)
            with self.assertRaises(ValueError): diagnose_job.source_for(artifacts, 'train-fixture', 'e'*64)
            with self.assertRaises(ValueError): diagnose_job.source_for(artifacts, '../train-fixture', 'b'*64)

class ThreeEpochLaunchTests(unittest.TestCase):
    setUp = resource_tests.ResourceLaunchTests.setUp
    capture = resource_tests.ResourceLaunchTests.capture

    def test_three_epoch_profiles_reach_correct_worker_and_tracking_mode(self):
        import train_job
        from contextlib import redirect_stdout
        from io import StringIO
        original = Path(__file__).resolve().parents[2]
        (self.root/'configs/training').mkdir()
        for name, dataset in [('demo-3epochs','synthetic'), ('hm-full-3epochs','hm')]:
            with self.subTest(name=name):
                (self.root/'configs/training'/(name+'.json')).write_bytes((original/'configs/training'/(name+'.json')).read_bytes())
                self.build.reset_mock(); self.commands.clear()
                with redirect_stdout(StringIO()):
                    self.assertEqual(train_job.main(['start','--config',name,'--expected-commit','a'*40,'--resources','mac24']),0)
                create=next(c for c in self.commands if c[:2]==('docker','create'))
                self.assertIn('/opt/hmrec/configs/training/'+name+'.json',create)
                self.assertEqual(create[create.index('--network')+1], 'none' if dataset=='synthetic' else 'fixture_backend')
                tracking=any('mlflow' in call.args for call in self.build.call_args_list)
                self.assertEqual(tracking,dataset=='hm')

    def test_missing_comparison_cannot_mark_a_comparison_job_passed(self):
        result={'stage':'T3-diagnostics','status':'passed','verification_exit_code':0}
        state={'Running':False,'ExitCode':0,'OOMKilled':False}
        self.assertNotEqual(data_job.classify_result(state,result,'T3-diagnostics',require_comparison=True),'passed')


class ComparisonReportTests(unittest.TestCase):
    def test_passed_package_requires_completed_comparison_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'diagnose-mac24-incomplete';directory.mkdir()
            (directory/'job.log').write_text('incomplete comparison')
            result={'stage':'T3-diagnostics','status':'passed','verification_exit_code':0,'comparison_required':True,
                    'diagnostic':{'replay_verified':True,'quality_accepted':False,'test_evaluated':False}}
            (directory/'result.json').write_text(json.dumps(result))
            report=dict(schema_version=1,kind='diagnostic-handoff',run_id=directory.name,machine_id='mac24',context='colima',
                        source_commit='a'*40,source_clean=True,status='passed',result=result,
                        steps=[dict(name='diagnose',log='job.log',status='passed',exit_code=0)])
            with self.assertRaises(ValueError):package_report(directory,report)
