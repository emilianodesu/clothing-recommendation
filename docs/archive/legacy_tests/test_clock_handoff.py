"""Closed read-only follow-ups must not change their source or leak logs."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


class ClockHandoffTests(unittest.TestCase):
    def github(self):
        spec = importlib.util.spec_from_file_location('clock_github', Path(__file__).resolve().parents[2]/'scripts/github-handoff.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def repack(self, source, target, change):
        """Keep all hashes coherent so tests exercise content, not ZIP integrity."""
        with zipfile.ZipFile(source) as bundle:
            contents = {name: bundle.read(name) for name in bundle.namelist()}
        report = json.loads(contents['manifest.json'])
        change(report, contents)
        contents['result.json'] = json.dumps(report['result']).encode()
        for item in report['files']:
            data = contents[item['path']]
            item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        report.pop('package_id')
        report['package_id'] = hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
        contents['manifest.json'] = json.dumps(report).encode()
        with zipfile.ZipFile(target, 'w') as bundle:
            for name, data in contents.items():
                bundle.writestr(name, data)
        return target

    def package(self, root, report, env, log=None):
        from handoff import package_report, write_json
        folder = root/report['run_id']; folder.mkdir()
        write_json(folder/'environment.json', env)
        write_json(folder/'result.json', report['result'])
        (folder/'commit.txt').write_text(report['source_commit']+'\n')
        (folder/'job.log').write_text(log if log is not None else 'read-only clock follow-up\ncollection: '+report['result']['clock_diagnostic']['power']['collection_status']+'\n')
        return package_report(folder, report)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('clock_check'), 'Missing read-only collector')
        import clock_check
        return clock_check

    def source(self, root):
        from test_events_memory import diagnostic_fixture
        from test_events_handoff import EventsHandoffTests
        report, env = diagnostic_fixture()
        # Preserve the real shape of a closed memory-diagnostic source.
        return EventsHandoffTests().package(root, report, env)

    def collect(self, root, machine='test'):
        m = self.module()
        source = self.source(root)
        original = source.read_bytes()
        with patch.object(m, 'checkout_commit', return_value='a'*40), patch.object(m, 'runner_digest', return_value='b'*64), \
             patch.object(m, 'collect_power', return_value=dict(
                 collection_status='command_failed', log_brackets_window=False, window_timestamped_rows=0,
                 unrecognized_window_rows=0, malformed_timestamp_rows=0, counts=dict(sleep=0,wake=0,darkwake=0),
                 events=[],events_truncated=False)):
            path = m.run(root, machine, 'a'*40, source)
        self.assertEqual(source.read_bytes(), original)
        return path

    def test_missing_power_logs_close_as_incomplete_not_no_sleep(self):
        self.module()
        from handoff import read_report, summary
        with tempfile.TemporaryDirectory() as directory:
            path = self.collect(Path(directory)); report = read_report(path)
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(report['result']['clock_diagnostic']['complete'])
            self.assertFalse(report['result']['clock_diagnostic']['timing']['cause_confirmed'])
            self.assertIn('lectura', summary(report))
            spec=importlib.util.spec_from_file_location('clock_github',Path(__file__).resolve().parents[2]/'scripts/github-handoff.py')
            github=importlib.util.module_from_spec(spec);spec.loader.exec_module(github)
            self.assertEqual(github.validate_archive(path)['status'],'failed')

    def test_mismatched_machine_cannot_claim_local_historical_evidence(self):
        self.module()
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            self.collect(Path(d), machine='another')

    def test_changed_derivatives_private_fields_and_downgrade_are_rejected(self):
        self.module()
        from handoff import read_report
        from clock_handoff import validate_clock
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            path=self.collect(Path(d)); original=read_report(path)
            with zipfile.ZipFile(path) as z:env=json.loads(z.read('environment.json'))
            for change in [lambda r:r.update(protocol='events-v1'),
                           lambda r:r['clock_diagnostic']['timing'].update(difference_seconds=12345),
                           lambda r:r['clock_diagnostic']['power'].update(raw_log='PRIVATE'),
                           lambda r:r['clock_diagnostic'].update(complete=True)]:
                report=copy.deepcopy(original);change(report['result'])
                with self.subTest(change=change),self.assertRaises(ValueError):
                    validate_clock(report,report['result'],env,{'manifest.json','result.json','environment.json','commit.txt','job.log','summary.md'})
            # Exercise routing through the real package reader as well as the validator.
            from test_events_handoff import EventsHandoffTests
            report=copy.deepcopy(original);report['result']['protocol']='events-v1'
            with tempfile.TemporaryDirectory() as bad, self.assertRaises(ValueError):
                EventsHandoffTests().package(Path(bad),report,env)

    def test_complete_collection_still_does_not_accept_t5_or_explain_cause(self):
        self.module()
        from handoff import read_report
        from test_events_handoff import EventsHandoffTests
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            archive=self.collect(Path(d));report=read_report(archive)
            with zipfile.ZipFile(archive) as z:env=json.loads(z.read('environment.json'))
            report['status']='passed';report['steps'][0].update(status='passed',exit_code=0)
            result=report['result'];result['status']='passed'
            result['clock_diagnostic']['power'].update(collection_status='complete',log_brackets_window=True)
            result['clock_diagnostic']['complete']=True
            with tempfile.TemporaryDirectory() as completed:
                verified=read_report(self.package(Path(completed),report,env))
                self.assertFalse(verified['result']['t5_closed'])
                self.assertFalse(verified['result']['clock_diagnostic']['timing']['cause_confirmed'])

    def test_private_attachment_text_and_manifest_fields_rejected_by_reader_and_upload(self):
        from handoff import read_report
        private = b'PRIVATE device /Users/private-owner/power-log\n'
        mutations = [
            (name, lambda r, c, name=name: c.update({name: private}))
            for name in ('job.log', 'summary.md', 'commit.txt')]
        mutations += [
            ('manifest-extra', lambda r,c: r.update(raw_log=private.decode())),
            ('warnings', lambda r,c: r.update(warnings=[private.decode()])),
            ('step-extra', lambda r,c: r['steps'][0].update(message=private.decode())),
            ('inventory-extra', lambda r,c: r['files'][0].update(message=private.decode())),
        ]
        with tempfile.TemporaryDirectory() as d:
            original = self.collect(Path(d))
            for name, change in mutations:
                archive = self.repack(original, Path(d)/(name+'.zip'), change)
                for reader in (read_report, self.github().validate_archive):
                    with self.subTest(case=name, reader=reader.__name__), self.assertRaises(ValueError):
                        reader(archive)

    def test_packager_rejects_raw_power_text(self):
        from handoff import read_report
        with tempfile.TemporaryDirectory() as d:
            source = self.collect(Path(d)); report = read_report(source)
            with zipfile.ZipFile(source) as bundle:
                env = json.loads(bundle.read('environment.json'))
            with tempfile.TemporaryDirectory() as bad, self.assertRaises(ValueError):
                self.package(Path(bad), report, env, log='PRIVATE device /Users/private-owner/power-log\n')

    def test_missing_reordered_or_regressive_snapshots_rejected_by_reader_and_upload(self):
        from handoff import read_report
        from events_clock import analyze_timing
        def change_shape(report, contents, mode):
            timing = report['result']['clock_diagnostic']['timing']
            snapshots = timing['snapshots']
            if mode == 'empty': snapshots.clear()
            elif mode == 'missing': snapshots.pop()
            elif mode == 'reordered':
                snapshots[0]['service'], snapshots[1]['service'] = snapshots[1]['service'], snapshots[0]['service']
            # Recompute derivatives to expose missing validation of source shape.
            report['result']['clock_diagnostic']['timing'] = analyze_timing(
                timing['started_at'],timing['finished_at'],timing['monotonic_duration_seconds'],snapshots)
        def regress(report, contents):
            timing = report['result']['clock_diagnostic']['timing']
            timing['snapshots'][0]['captured_at'] = '2026-10-22T00:00:01Z'
            timing['intervals'][0]['utc_seconds'] = -1.0
            timing['intervals'][0]['difference_seconds'] = -1.0-timing['intervals'][0]['monotonic_seconds']
        changes = [(mode, lambda r,c,mode=mode: change_shape(r,c,mode)) for mode in ('empty','missing','reordered')]
        changes.append(('backward-utc', regress))
        with tempfile.TemporaryDirectory() as d:
            original = self.collect(Path(d))
            for name, change in changes:
                archive = self.repack(original,Path(d)/(name+'.zip'),change)
                for reader in (read_report, self.github().validate_archive):
                    with self.subTest(case=name, reader=reader.__name__), self.assertRaises(ValueError):
                        reader(archive)

    def test_dirty_or_wrong_checkout_fails_before_collection(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'untracked').write_text('preserve me')
            with self.assertRaises((ValueError,subprocess.CalledProcessError)):
                m.checkout_commit(root,'a'*40)
            self.assertEqual((root/'untracked').read_text(),'preserve me')

    def test_project_command_accepts_flags_before_any_docker_configuration(self):
        m=self.module()
        import project
        with patch.object(sys,'argv',['project.py','clock-check','--machine-id','test',
                 '--expected-commit','a'*40,'--source-report','source.zip']), \
             patch.object(m,'main',return_value=1) as call:
            self.assertEqual(project.main(),1)
        self.assertIn('source.zip',call.call_args.args[0])
