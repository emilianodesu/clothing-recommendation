import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from data_job import classify_result, validate_run_id


class DataJobTests(unittest.TestCase):
    def test_zero_exit_without_complete_result_is_interrupted(self):
        self.assertEqual(classify_result({'Running':False,'ExitCode':0}, None), 'interrupted')
        self.assertEqual(classify_result({'Running':False,'ExitCode':137,'OOMKilled':True}, {'status':'running'}), 'failed')
        self.assertEqual(classify_result({'Running':True}, {'status':'running'}), 'running')

    def test_success_requires_worker_verification_and_pipeline_success(self):
        state = {'Running':False,'ExitCode':0}
        self.assertEqual(classify_result(state, {'status':'passed','stage':'T2'}), 'interrupted')
        self.assertEqual(classify_result(state, {'status':'passed','stage':'T2','verification_exit_code':0}), 'passed')

    def test_run_id_cannot_escape_artifacts_directory(self):
        for name in ('../data-evil', 'data-x/child', 'bootstrap-mac24', ''):
            with self.assertRaises(ValueError):validate_run_id(name)
        self.assertEqual(validate_run_id('data-mac24-20261019T100000Z-a1b2c3d4'), 'data-mac24-20261019T100000Z-a1b2c3d4')

class DataJobRecoveryTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest import mock
        import data_job
        self.module = data_job
        self.temp = tempfile.TemporaryDirectory(prefix='data-job-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.values = {'DATA_DIR':str(self.root/'data'), 'ARTIFACTS_DIR':str(self.root/'artifacts'),
                       'COMPOSE_PROJECT_NAME':'hmrec_fixture', 'HMREC_UID':'501','HMREC_GID':'20'}
        for name in ('data','artifacts','configs'):(self.root/name).mkdir()
        (self.root/'configs/demo.yaml').write_text('dataset: synthetic\n')
        (self.root/'configs/resources').mkdir()
        (self.root/'configs/resources/base.json').write_text(json.dumps({
            'schema_version':1, 'colima':{'memory_gib':8,'cpus':4},
            'job':{'memory_gib':5,'cpus':3,'threads':2}}))
        (self.root/'uv.lock').write_text('synthetic lock')
        patches = [mock.patch.object(data_job,'ROOT',self.root), mock.patch.object(data_job,'read_configuration',return_value=self.values)]
        for patch in patches:patch.start();self.addCleanup(patch.stop)
        self.run_id = 'data-fixture'
        self.directory = self.root / 'artifacts/data-jobs' / self.run_id
        self.directory.mkdir(parents=True)
        self.run = {'run_id':self.run_id,'machine_id':'fixture','context':'colima','profile':'default',
                    'source_commit':'a'*40,'source_clean':True,'image_id':'sha256:'+'b'*64,'container_id':'c'*64,
                    'started_at':'2026-10-19T00:00:00Z'}
        (self.directory/'environment.json').write_text(json.dumps(self.run))

    def info(self, code=0, oom=False):
        return [{'Config':{'Labels':{'hmrec.run-id':self.run_id}},'Image':self.run['image_id'],
                 'State':{'Running':False,'ExitCode':code,'OOMKilled':oom}}]

    def test_completion_race_exports_latest_success(self):
        from unittest import mock
        from handoff import read_report
        (self.directory/'result.json').write_text(json.dumps({'status':'running','stage':'T2'}))
        def inspect(*args, **kwargs):
            (self.directory/'result.json').write_text(json.dumps({'status':'passed','stage':'T2','verification_exit_code':0}))
            return json.dumps(self.info())
        def logs(command, root, env, path):path.write_text('done');return 0,False
        with mock.patch.object(self.module,'capture',side_effect=inspect), mock.patch.object(self.module,'execute',side_effect=logs):
            self.assertEqual(read_report(self.module.export(self.run_id))['status'], 'passed')

    def test_interrupt_failure_oom_and_missing_container_export(self):
        from unittest import mock
        import subprocess
        from handoff import read_report
        for code, oom, missing, expected in [(130,False,False,'interrupted'), (137,True,False,'failed'), (1,False,False,'failed'), (0,False,True,'interrupted')]:
            with self.subTest(code=code, missing=missing):
                for leftover in (self.root/'artifacts/handoff').glob('*.partial') if (self.root/'artifacts/handoff').exists() else []:
                    leftover.unlink()
                def inspect(env,*command,**kwargs):
                    if missing and command[1]=='inspect':raise subprocess.CalledProcessError(1,command)
                    return 'engine' if command[1]=='info' else json.dumps(self.info(code,oom))
                def logs(command, root, env, path):path.write_text('stopped');return 0,False
                with mock.patch.object(self.module,'capture',side_effect=inspect), mock.patch.object(self.module,'execute',side_effect=logs):
                    archive=self.module.export(self.run_id)
                    self.assertEqual(read_report(archive)['status'],expected)
                    archive.unlink()

    def test_build_and_create_failures_remain_exportable(self):
        from unittest import mock
        from contextlib import redirect_stdout
        from io import StringIO
        import subprocess
        from handoff import read_report
        for failure in ('build','create'):
            with self.subTest(failure=failure):
                def capture(env,*command,**kwargs):
                    if command[:2]==('git','rev-parse'):return 'a'*40
                    if command[:2]==('git','status'):return ''
                    if command[:3]==('docker','context','inspect'):return json.dumps([{'Endpoints':{'docker':{'Host':'unix:///synthetic'}}}])
                    if command[:2]==('docker','info'):return json.dumps({'Architecture':'aarch64','MemTotal':8*2**30,'NCPU':4,'ServerVersion':'synthetic'})
                    if command[:2]==('docker','ps'):return ''
                    if command[:3]==('docker','image','inspect'):return json.dumps([{'Id':'sha256:'+'b'*64,'Architecture':'arm64'}])
                    if command[:2]==('docker','create'):raise subprocess.CalledProcessError(1,command)
                    self.fail('Unexpected boundary ' + str(command[:3]))
                def build(*args, **kwargs):
                    if failure=='build':raise subprocess.CalledProcessError(1,['docker','compose','build'])
                output=StringIO()
                before=set((self.root/'artifacts/data-jobs').iterdir())
                with mock.patch.object(self.module,'capture',side_effect=capture), mock.patch.object(self.module,'compose',side_effect=build), redirect_stdout(output):
                    with self.assertRaises(subprocess.CalledProcessError):
                        self.module.start('fixture','default','synthetic')
                    created=set((self.root/'artifacts/data-jobs').iterdir())-before
                    self.assertEqual(len(created),1)
                    run=created.pop()
                    self.assertIn(run.name,output.getvalue())
                    report=read_report(self.module.export(run.name))
                    self.assertEqual(report['status'],'failed')

    def test_in_progress_launch_cannot_be_exported(self):
        import fcntl
        self.run.pop('container_id')
        self.run['launch_status']='running'
        (self.directory/'environment.json').write_text(json.dumps(self.run))
        with (self.directory/'.launch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.module.inspect_run(self.run_id)[-1], 'running')
            with self.assertRaisesRegex(ValueError,'Esperar'):
                self.module.export(self.run_id)
            from unittest import mock
            self.run['container_id']='c'*64
            (self.directory/'environment.json').write_text(json.dumps(self.run))
            with mock.patch.object(self.module,'capture',side_effect=AssertionError('launch is still active')):
                self.assertEqual(self.module.inspect_run(self.run_id)[-1], 'running')
            self.run.pop('container_id')
            (self.directory/'environment.json').write_text(json.dumps(self.run))
        self.assertEqual(self.module.inspect_run(self.run_id)[-1], 'interrupted')

    def test_tracking_failure_preserves_successful_build_and_tracking_log(self):
        from unittest import mock
        from contextlib import redirect_stdout
        from io import StringIO
        import subprocess
        from handoff import read_report
        (self.root/'configs/training').mkdir()
        (self.root/'configs/training/hm-small.json').write_text('{}')
        def capture(env, *command, **kwargs):
            if command[:2] == ('git','rev-parse'):return 'a'*40
            if command[:2] == ('git','status'):return ''
            if command[:3] == ('docker','context','inspect'):return json.dumps([{'Endpoints':{'docker':{'Host':'unix:///synthetic'}}}])
            if command[:2] == ('docker','info'):return json.dumps({'Architecture':'aarch64','MemTotal':8*2**30,'NCPU':4,'ServerVersion':'synthetic'})
            if command[:2] == ('docker','ps'):return ''
            self.fail('Unexpected boundary ' + str(command[:3]))
        def compose(root, values, action, *args, log_path):
            log_path.write_text('image built' if action == 'build' else 'tracking port occupied')
            if action == 'up':raise subprocess.CalledProcessError(1, ['docker','compose','up'])
        with mock.patch.object(self.module,'capture',side_effect=capture), mock.patch.object(self.module,'compose',side_effect=compose), redirect_stdout(StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                self.module.start('fixture','default','hm',training_config='hm-small')
            run = next((self.root/'artifacts/data-jobs').glob('train-*'))
            report = read_report(self.module.export(run.name))
        self.assertEqual(report['steps'][0]['status'], 'passed')
        self.assertEqual(report['steps'][1]['name'], 'tracking')
        self.assertEqual(report['steps'][1]['status'], 'failed')
        self.assertIn('tracking.log', [entry['path'] for entry in report['files']])

    def test_export_separates_observed_and_durable_training_progress(self):
        from unittest import mock
        import zipfile
        self.run.update(training_config='hm-small', experiment_id='train-fixture', attempt=1)
        (self.directory/'environment.json').write_text(json.dumps(self.run))
        model = self.root/'artifacts/training/train-fixture'; model.mkdir(parents=True)
        (model/'progress.json').write_text(json.dumps({'step':240,'rows_consumed':24000}))
        (model/'latest.json').write_text(json.dumps({'step':0,'rows_consumed':0,'path':'checkpoint.pt','sha256':'a'*64}))
        def logs(command, root, env, path):path.write_text('killed');return 0,False
        with mock.patch.object(self.module,'capture',return_value=json.dumps(self.info(137,True))), mock.patch.object(self.module,'execute',side_effect=logs):
            archive = self.module.export(self.run_id)
        with zipfile.ZipFile(archive) as bundle:
            result = json.loads(bundle.read('result.json'))
        self.assertEqual(result['last_durable_progress']['step'],0)
        self.assertEqual(result['last_observed_progress']['step'],240)
