"""Closed read-only follow-ups must not change their source or leak logs."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


class ClockHandoffTests(unittest.TestCase):
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
                    validate_clock(report,report['result'],env,{'manifest.json','result.json','environment.json','commit.txt','job.log'})
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
                verified=read_report(EventsHandoffTests().package(Path(completed),report,env))
                self.assertFalse(verified['result']['t5_closed'])
                self.assertFalse(verified['result']['clock_diagnostic']['timing']['cause_confirmed'])

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
