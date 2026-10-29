import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import portability_check as p
import mac24_t7 as m


class T7FollowupTests(unittest.TestCase):
    def test_only_exact_failed_archive_is_allowed_and_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            archive = folder / (p.FAILED_T7 + '.zip')
            archive.write_bytes(b'original')
            with patch.object(p, 'checksum_file', return_value=p.FAILED_T7_SHA), patch.object(p, 'read_report', return_value={'status': 'failed', 'run_id': p.FAILED_T7}):
                p.check_attempts(folder, 'mac24', True)
                self.assertEqual(archive.read_bytes(), b'original')
                (folder / 'portability-mac24-another').mkdir()
                with self.assertRaises(ValueError): p.check_attempts(folder, 'mac24', True)

    def test_missing_changed_or_wrong_machine_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            with self.assertRaises(ValueError): p.check_attempts(folder, 'mac24', True)
            (folder / (p.FAILED_T7 + '.zip')).write_bytes(b'changed')
            with self.assertRaises(ValueError): p.check_attempts(folder, 'mac24', True)
            with self.assertRaises(ValueError): p.check_attempts(folder, 'other', True)
            with self.assertRaises(ValueError): p.check_attempts(folder, 'mac24', False)

    def test_airflow_preflight_is_read_only_and_requires_paused_dags(self):
        import json
        for states in ({'hm_build_release': True, 'hm_refresh_profiles': True}, {'hm_build_release': True, 'hm_refresh_profiles': False}):
            with patch.object(m, 'compose', side_effect=['ok', json.dumps(states)]) as call:
                if all(states.values()): m.check_airflow(Path('.'), {})
                else:
                    with self.assertRaises(ValueError): m.check_airflow(Path('.'), {})
                self.assertIn('/opt/airflow/entrypoint.py', call.call_args_list[0].args)
                self.assertIn('SET TRANSACTION READ ONLY', call.call_args_list[1].args[-1])
