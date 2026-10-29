"""Operator-run SSH adapter must never silently repeat the remote rehearsal."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


class Mac24TerminalTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('mac24_terminal'))
        import mac24_terminal
        return mac24_terminal

    def test_once_marker_survives_success_failure_and_interrupt(self):
        m=self.module()
        for error in (None, ValueError('failed'), KeyboardInterrupt()):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);calls=[]
                def job():
                    calls.append('job')
                    if error:raise error
                    return 1
                try:m.once(root,'a'*40,job)
                except (ValueError,KeyboardInterrupt):pass
                with self.assertRaises(ValueError):m.once(root,'a'*40,job)
                self.assertEqual(calls,['job'])

    def test_prior_incomplete_or_closed_operational_attempt_blocks(self):
        m=self.module()
        for suffix in ('','.zip','.zip.partial'):
            with tempfile.TemporaryDirectory() as d:
                p=Path(d);(p/('events-mac24-new'+suffix)).touch()
                with self.assertRaises(ValueError):m.check_attempts(p)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            for rid in m.HISTORICAL:(p/(rid+'.zip')).touch()
            m.check_attempts(p)

    def test_postgres_restart_or_replacement_is_not_silently_allowed(self):
        m=self.module();before=dict(container_id='a',started_at='2026-10-22T00:00:00Z',restart_count=0,image_id='b')
        m.same_service(before,dict(before))
        for key,value in [('container_id','c'),('started_at','2026-10-23T00:00:00Z'),('restart_count',1),('image_id','c')]:
            after=dict(before);after[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):m.same_service(before,after)

    def test_inventory_checks_original_bytes_and_keeps_new_outputs(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'original').write_bytes(b'one')
            before=m.inventory([p]);(p/'new').write_bytes(b'two')
            self.assertEqual(m.preserved(before)['files'],1)
            (p/'original').write_bytes(b'two')
            with self.assertRaises(ValueError):m.preserved(before)

    def test_failed_job_still_exports_but_failed_verification_never_starts_job(self):
        m=self.module()
        for bad_verify in (False,True):
            calls=[]
            def verify():
                calls.append('verify')
                if bad_verify:raise ValueError('tests failed')
            with tempfile.TemporaryDirectory() as d:
                try:
                    m.sequence(Path(d),'a'*40,verify,lambda:calls.append('job') or 1,lambda:calls.append('publish'))
                except ValueError:pass
            self.assertEqual(calls,['verify'] if bad_verify else ['verify','job','publish'])

    def test_inventory_rejects_symlinks(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'target').write_bytes(b'x');(p/'link').symlink_to(p/'target')
            with self.assertRaises(ValueError):m.inventory([p])

    def test_existing_postgres_verification_never_starts_or_recreates_it(self):
        from unittest.mock import patch
        import project
        for cid in ('existing-id', ''):
            with patch.object(project,'compose',return_value=cid) as compose:
                if cid:project.verification_postgres(Path('/tmp'),{},existing_only=True)
                else:
                    with self.assertRaises(ValueError):project.verification_postgres(Path('/tmp'),{},existing_only=True)
                self.assertEqual(compose.call_count,1)
                self.assertEqual(compose.call_args.args[2:],('ps','--status','running','-q','postgres'))

    def test_docker_timeout_does_not_prevent_preservation_report(self):
        from unittest.mock import patch
        import subprocess
        m=self.module()
        with patch.object(m,'service',side_effect=subprocess.TimeoutExpired('docker',30)):
            result=m.check_preservation({}, {}, {'postgres':{},'ui':{}})
        self.assertFalse(result['unchanged'])
        self.assertEqual(result['error_type'],'TimeoutExpired')

    def test_events_cli_existing_services_keeps_all_checks_without_up(self):
        from unittest.mock import patch
        import project
        args=['project.py','verify','--suite','events','--existing-services']
        with patch.object(sys,'argv',args),patch.object(project,'read_configuration',return_value={}),patch.object(project,'compose',return_value='active') as compose,patch.object(project.subprocess,'run') as run:
            self.assertEqual(project.main(),0)
        calls=[call.args[2:] for call in compose.call_args_list]
        self.assertFalse(any('up' in call for call in calls))
        self.assertIn(('ps','--status','running','-q','postgres'),calls)
        self.assertTrue(any('tests/integration/test_events.py' in call for call in calls))
        self.assertTrue(any('tests/integration/test_recommendations.py' in call for call in calls))
        self.assertEqual(run.call_count,2)
