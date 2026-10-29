"""Resource limits must reach Docker and survive reports and resume."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import data_job
from handoff import read_report


class ResourceLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('data', 'artifacts', 'configs/resources'):
            (self.root / name).mkdir(parents=True)
        (self.root / 'configs/demo.yaml').write_text('dataset: synthetic\n')
        (self.root / 'uv.lock').write_text('fixture lock')
        for name, memory, vm_memory in [('base', 5, 8), ('mac24', 10, 14)]:
            (self.root / 'configs/resources' / (name + '.json')).write_text(json.dumps({
                'schema_version': 1, 'colima': {'memory_gib': vm_memory, 'cpus': 4},
                'job': {'memory_gib': memory, 'cpus': 3, 'threads': 2}}))
        self.values = {'DATA_DIR': str(self.root / 'data'), 'ARTIFACTS_DIR': str(self.root / 'artifacts'),
                       'COMPOSE_PROJECT_NAME': 'fixture', 'HMREC_UID': '501', 'HMREC_GID': '20'}
        self.commands = []
        self.engine_memory = 14 * 2**30 - 256 * 2**20
        self.engine_cpus = 4
        self.applied_memory = 10 * 2**30
        self.applied_cpus = 3000000000
        self.applied_threads = '2'
        self.containers = {}
        self.image_id = 'sha256:' + 'b' * 64
        self.build = mock.Mock()
        for patch in [mock.patch.object(data_job, 'ROOT', self.root),
                      mock.patch.object(data_job, 'read_configuration', return_value=self.values),
                      mock.patch.object(data_job, 'capture', side_effect=self.capture),
                      mock.patch.object(data_job, 'compose', self.build)]:
            patch.start()
            self.addCleanup(patch.stop)

    def capture(self, env, *command, **kwargs):
        self.commands.append(command)
        if command[:2] == ('git', 'rev-parse'): return 'a' * 40
        if command[:2] == ('git', 'status'): return ''
        if command[:3] == ('docker', 'context', 'inspect'):
            return json.dumps([{'Endpoints': {'docker': {'Host': 'unix:///fixture'}}}])
        if command[:2] == ('docker', 'info'):
            return json.dumps({'Architecture': 'aarch64', 'MemTotal': self.engine_memory,
                               'NCPU': self.engine_cpus, 'ServerVersion': 'fixture'})
        if command[:2] == ('docker', 'ps'): return ''
        if command[:3] == ('docker', 'image', 'inspect'):
            return json.dumps([{'Id': self.image_id, 'Architecture': 'arm64'}])
        if command[:2] == ('docker', 'create'):
            container = format(len(self.containers) + 1, '064x')
            self.containers[container] = next(value.split('=', 1)[1] for value in command if value.startswith('hmrec.run-id='))
            return container
        if command[:2] == ('docker', 'inspect'):
            return json.dumps([{'Image': self.image_id,
                'Config': {'Labels': {'hmrec.run-id': self.containers[command[2]]},
                           'Env': [key + '=' + self.applied_threads for key in
                                   ('POLARS_MAX_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS')]},
                'HostConfig': {'Memory': self.applied_memory, 'NanoCpus': self.applied_cpus,
                               'MemorySwap': 2 * self.applied_memory},
                'State': {'Running': False, 'Status': 'exited', 'ExitCode': 0, 'OOMKilled': False}}])
        if command[:2] == ('docker', 'start'): return 'c' * 64
        self.fail('Unexpected external command: ' + repr(command[:3]))

    def cli(self, *args):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            try:
                code = data_job.main(list(args))
            except SystemExit as error:
                code = error.code
        return code, output.getvalue()

    def runs(self):
        return list((self.root / 'artifacts/data-jobs').glob('*/environment.json'))

    def launch(self, *extra):
        return self.cli('start', '--machine-id', 'fixture', '--profile', 'default',
                        '--dataset', 'synthetic', *extra)

    def test_mac24_reaches_docker_and_report_instead_of_fixed_5g(self):
        code, output = self.launch('--resources', 'mac24')
        self.assertEqual(code, 0, output)
        create = next(c for c in self.commands if c[:2] == ('docker', 'create'))
        self.assertEqual(create[create.index('--memory') + 1], '10g')
        self.assertEqual(create[create.index('--cpus') + 1], '3')
        self.assertIn('POLARS_MAX_THREADS=2', create)
        run = json.loads(self.runs()[0].read_text())
        self.assertEqual(run['limits']['memory_bytes'], 10737418240)
        self.assertEqual(run['effective_limits']['memory_bytes'], 10737418240)
        self.assertEqual(run['effective_limits']['nano_cpus'], 3000000000)
        self.assertEqual(run['effective_limits']['polars_threads'], 2)
        self.assertEqual(run['resources']['name'], 'mac24')
        self.assertEqual(run['resources']['config']['colima']['memory_gib'], 14)
        self.assertEqual(len(run['resources']['sha256']), 64)
        result = {'status': 'passed', 'stage': 'T2', 'verification_exit_code': 0}
        (self.runs()[0].parent / 'result.json').write_text(json.dumps(result))
        def logs(command, root, env, path):
            path.write_text('fixture completed\n')
            return 0, False
        with mock.patch.object(data_job, 'execute', side_effect=logs):
            report = read_report(data_job.export(run['run_id']))
        self.assertEqual(report['resources'], run['resources'])
        self.assertEqual(report['effective_limits']['memory_bytes'], 10737418240)

    def test_custom_cpu_and_threads_reach_docker(self):
        path = self.root / 'configs/resources/custom.json'
        path.write_text(json.dumps({'schema_version': 1, 'colima': {'memory_gib': 8, 'cpus': 4},
                                    'job': {'memory_gib': 6, 'cpus': 2, 'threads': 1}}))
        self.applied_memory, self.applied_cpus, self.applied_threads = 6 * 2**30, 2000000000, '1'
        code, output = self.launch('--resources', 'custom')
        self.assertEqual(code, 0, output)
        create = next(c for c in self.commands if c[:2] == ('docker', 'create'))
        self.assertEqual(create[create.index('--memory') + 1], '6g')
        self.assertEqual(create[create.index('--cpus') + 1], '2')
        for key in ('POLARS_MAX_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
            self.assertIn(key + '=1', create)

    def test_default_preserves_base_budget(self):
        self.applied_memory = 5 * 2**30
        code, output = self.launch()
        self.assertEqual(code, 0, output)
        run = json.loads(self.runs()[0].read_text())
        self.assertEqual(run.get('resources', {}).get('name'), 'base')
        self.assertEqual(run['effective_limits']['memory_bytes'], 5368709120)

    def test_insufficient_engine_fails_before_build_and_remains_exportable(self):
        for memory, cpus in [(8 * 2**30, 4), (10 * 2**30, 4), (14 * 2**30, 2)]:
            with self.subTest(memory=memory, cpus=cpus):
                self.engine_memory, self.engine_cpus = memory, cpus
                before = set(self.runs())
                code, output = self.launch('--resources', 'mac24')
                self.assertEqual(code, 1, output)
                created = set(self.runs()) - before
                self.assertEqual(len(created), 1, output)
                self.assertFalse(self.build.called)
                self.assertFalse(any(c[:2] == ('docker', 'create') for c in self.commands))
                path = created.pop()
                report = read_report(data_job.export(path.parent.name))
                self.assertEqual(report['status'], 'failed')

    def test_incorrect_effective_limits_prevent_start(self):
        for field, wrong in [('applied_memory', 5 * 2**30), ('applied_cpus', 1000000000),
                             ('applied_threads', '1')]:
            with self.subTest(field=field):
                original = getattr(self, field)
                setattr(self, field, wrong)
                self.commands.clear()
                code, output = self.launch('--resources', 'mac24')
                self.assertEqual(code, 1, output)
                self.assertIn('efectivos', output)
                self.assertFalse(any(c[:2] == ('docker', 'start') for c in self.commands))
                setattr(self, field, original)

    def test_resume_preserves_resource_profile_and_refuses_drift(self):
        code, output = self.launch('--resources', 'mac24')
        self.assertEqual(code, 0, output)
        first = self.runs()[0]
        original = json.loads(first.read_text())
        code, output = self.cli('resume', '--run-id', first.parent.name)
        self.assertEqual(code, 0, output)
        resumed = next(p for p in self.runs() if p != first)
        self.assertEqual(json.loads(resumed.read_text())['resources'], original['resources'])
        profile = self.root / 'configs/resources/mac24.json'
        changed = json.loads(profile.read_text())
        changed['job']['memory_gib'] = 9
        profile.write_text(json.dumps(changed))
        self.commands.clear()
        code, output = self.cli('resume', '--run-id', first.parent.name)
        self.assertEqual(code, 1, output)
        self.assertIn('recursos', output)
        self.assertFalse(any(c[:2] == ('docker', 'create') for c in self.commands))

    def test_invalid_profiles_fail_before_docker(self):
        for name in ('../mac24', '/tmp/profile', 'missing'):
            with self.subTest(name=name):
                code, output = self.launch('--resources', name)
                self.assertEqual(code, 1, output)
                self.assertFalse(any(c[0] == 'docker' for c in self.commands))
        path = self.root / 'configs/resources/mac24.json'
        valid = json.loads(path.read_text())
        invalid = [None, {'schema_version': 99}, {**valid, 'unexpected': 1}]
        for value in (0, -1, True, '10', 1.5):
            invalid.append({**valid, 'job': {**valid['job'], 'memory_gib': value}})
        invalid.append({**valid, 'job': {**valid['job'], 'threads': 4}})
        invalid.append({**valid, 'colima': {'memory_gib': 10, 'cpus': 4}})
        for value in invalid:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                code, output = self.launch('--resources', 'mac24')
                self.assertEqual(code, 1, output)
                self.assertFalse(any(c[0] == 'docker' for c in self.commands))
