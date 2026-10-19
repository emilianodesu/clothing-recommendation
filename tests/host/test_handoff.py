"""Host-only tests: synthetic reports and real child processes, no Docker."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))


class HandoffTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('handoff', ROOT / 'scripts/handoff.py')
        self.assertTrue(spec and spec.loader and Path(spec.origin).exists(),
                        'The handoff implementation is missing')
        self.handoff = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.handoff)
        self.temp = tempfile.TemporaryDirectory(prefix='handoff with spaces ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def report(self):
        return {'schema_version': 1, 'kind': 'bootstrap-handoff',
                'run_id': 'bootstrap-mac24-20261019T010203Z-abcdef12',
                'machine_id': 'mac24', 'status': 'passed', 'source_commit': 'a' * 40,
                'source_clean': True, 'started_at': '2026-10-19T01:02:03+00:00',
                'finished_at': '2026-10-19T01:02:04+00:00',
                'context': 'colima-hm-project', 'steps': [], 'warnings': []}

    def test_failure_stops_later_steps_but_exports_failure_log(self):
        run = self.root / 'run'
        run.mkdir()
        marker = self.root / 'must-not-exist'
        report = self.report()
        steps = [
            ('failure', [sys.executable, '-c', 'import sys; print("diagnostic"); sys.exit(7)']),
            ('later', [sys.executable, '-c', 'from pathlib import Path; Path("must-not-exist").touch()']),
        ]
        code = self.handoff.run_steps(self.root, run, report, steps, os.environ.copy())
        self.assertEqual(code, 7)
        self.assertFalse(marker.exists())
        self.assertEqual(report['status'], 'failed')
        archive = self.handoff.package_report(run, report)
        verified = self.handoff.read_report(archive)
        self.assertEqual(verified['steps'][0]['exit_code'], 7)
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn(b'diagnostic', bundle.read('failure.log'))

    def test_success_and_paths_with_spaces_round_trip(self):
        run = self.root / 'run'
        run.mkdir()
        report = self.report()
        code = self.handoff.run_steps(self.root, run, report,
            [('hello', [sys.executable, '-c', 'print("real child process")'])], os.environ.copy())
        self.assertEqual(code, 0)
        verified = self.handoff.read_report(self.handoff.package_report(run, report))
        self.assertEqual(verified['status'], 'passed')
        self.assertEqual(verified['source_commit'], 'a' * 40)
        self.assertEqual(verified['steps'][0]['exit_code'], 0)

    def test_unlisted_private_files_are_never_packaged(self):
        run = self.root / 'run'
        run.mkdir()
        (run / '.env').write_text('SECRET=must-not-leave')
        (run / 'dataset.zip').write_bytes(b'private')
        (run / 'token.txt').write_text('private')
        archive = self.handoff.package_report(run, self.report())
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(set(bundle.namelist()), {'manifest.json', 'summary.md'})

    def test_tampered_log_is_rejected(self):
        run = self.root / 'run'
        run.mkdir()
        report = self.report()
        self.handoff.run_steps(self.root, run, report,
            [('hello', [sys.executable, '-c', 'print("original")'])], os.environ.copy())
        archive = self.handoff.package_report(run, report)
        broken = self.root / 'broken.zip'
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(broken, 'w') as destination:
            for name in source.namelist():
                destination.writestr(name, b'changed' if name == 'hello.log' else source.read(name))
        with self.assertRaisesRegex(ValueError, 'integridad'):
            self.handoff.read_report(broken)

    def test_unsafe_archive_path_is_rejected_without_extracting(self):
        archive = self.root / 'unsafe.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('../escaped.txt', 'bad')
        with self.assertRaisesRegex(ValueError, 'ruta'):
            self.handoff.read_report(archive)
        self.assertFalse((self.root.parent / 'escaped.txt').exists())

    def test_duplicate_archive_entries_are_rejected(self):
        archive = self.root / 'duplicate.zip'
        import warnings
        with warnings.catch_warnings(), zipfile.ZipFile(archive, 'w') as bundle:
            warnings.simplefilter('ignore', UserWarning)
            bundle.writestr('manifest.json', '{}')
            bundle.writestr('manifest.json', '{}')
        with self.assertRaisesRegex(ValueError, 'duplicad'):
            self.handoff.read_report(archive)

    def test_inherited_remote_docker_and_compose_settings_are_removed(self):
        original = {'PATH': '/bin', 'DOCKER_HOST': 'tcp://unrelated:2376',
                    'DOCKER_CONTEXT': 'unrelated', 'DOCKER_DEFAULT_PLATFORM': 'linux/amd64',
                    'COMPOSE_PROJECT_NAME': 'unrelated', 'COMPOSE_FILE': 'unrelated.yaml',
                    'BASH_ENV': '/unrelated.sh'}
        environment = self.handoff.local_environment('hm-project', original)
        self.assertEqual(environment['DOCKER_CONTEXT'], 'colima-hm-project')
        self.assertEqual(original['DOCKER_CONTEXT'], 'unrelated')
        for key in ('DOCKER_HOST', 'DOCKER_DEFAULT_PLATFORM', 'COMPOSE_PROJECT_NAME', 'COMPOSE_FILE', 'BASH_ENV'):
            self.assertNotIn(key, environment)

    def test_invalid_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            self.handoff.local_environment('../outside', {})

    def test_logs_are_bounded_and_keep_the_failure_tail(self):
        run = self.root / 'run'
        run.mkdir()
        report = self.report()
        self.handoff.run_steps(self.root, run, report,
            [('large', [sys.executable, '-c', 'print("x" * (6 * 1024 * 1024)); print("last diagnostic")'])],
            os.environ.copy())
        log = run / 'large.log'
        self.assertLess(log.stat().st_size, 5 * 1024 * 1024)
        self.assertIn(b'last diagnostic', log.read_bytes())
        self.assertTrue(report['steps'][0]['log_truncated'])

    def test_manifest_that_is_not_an_object_is_rejected(self):
        archive = self.root / 'wrong-type.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('manifest.json', '[]')
        with self.assertRaises(ValueError):
            self.handoff.read_report(archive)

    def test_launcher_reports_preflight_failure_without_touching_private_files(self):
        spec = importlib.util.spec_from_file_location('starter', ROOT / 'scripts/start-second-mac.py')
        self.assertTrue(Path(spec.origin).exists(), 'The starter script is missing')
        starter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(starter)
        (self.root / '.env').write_text('private configuration')
        code, archive = starter.run(self.root, 'mac24', 'hm-project', False, None)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.handoff.read_report(archive)['status'], 'failed')
        self.assertEqual((self.root / '.env').read_text(), 'private configuration')
        with zipfile.ZipFile(archive) as bundle:
            self.assertNotIn('.env', bundle.namelist())

    def test_remote_context_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'local'):
            self.handoff.validate_context('tcp://other-host:2376', 'aarch64')

    def test_emulated_architecture_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ARM64'):
            self.handoff.validate_context('unix:///tmp/docker.sock', 'x86_64')

    def test_reader_rejects_success_with_a_failed_step(self):
        archive = self.root / 'contradiction.zip'
        report = self.report()
        report['steps'] = [{'name': 'check', 'status': 'failed', 'exit_code': 0,
                            'log': 'check.log'}]
        import hashlib
        report['files'] = [{'path': 'check.log', 'bytes': 0,
                            'sha256': hashlib.sha256(b'').hexdigest()}]
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('manifest.json', json.dumps(report))
            bundle.writestr('check.log', '')
        with self.assertRaises(ValueError):
            self.handoff.read_report(archive)

    def test_reader_cli_distinguishes_failed_run_from_invalid_zip(self):
        run = self.root / 'run'
        run.mkdir()
        report = self.report()
        report['status'] = 'failed'
        archive = self.handoff.package_report(run, report)
        command = [sys.executable, str(ROOT / 'scripts/read-handoff.py')]
        valid_failure = subprocess.run([*command, str(archive)], capture_output=True)
        self.assertEqual(valid_failure.returncode, 1)
        invalid = self.root / 'corrupt.zip'
        invalid.write_bytes(b'not a zip')
        rejected = subprocess.run([*command, str(invalid)], capture_output=True)
        self.assertEqual(rejected.returncode, 2)

    def test_interrupt_stops_child_and_preserves_report(self):
        child = self.root / 'child.py'
        child.write_text('''import signal, time
from pathlib import Path
def stop(signum, frame):
    Path('stopped').touch()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
Path('ready').touch()
while True:
    time.sleep(0.1)
''')
        runner = self.root / 'runner.py'
        runner.write_text('''import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from handoff import run_steps, package_report
root = Path.cwd()
run = root / 'run'
run.mkdir()
report = json.loads(sys.argv[2])
code = run_steps(root, run, report, [('wait', [sys.executable, 'child.py'])], os.environ.copy())
package_report(run, report)
sys.exit(code)
''')
        process = subprocess.Popen([sys.executable, str(runner), str(ROOT / 'scripts'),
                                    json.dumps(self.report())], cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while not (self.root / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root / 'ready').exists(), 'Child did not start')
            process.send_signal(signal.SIGINT)
            output, _ = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 130, output.decode())
            self.assertTrue((self.root / 'stopped').exists())
            verified = self.handoff.read_report(self.root / 'run.zip')
            self.assertEqual(verified['status'], 'interrupted')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
