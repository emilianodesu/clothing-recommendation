import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import mac24_t8_baseline as m

class PreflightDiagnostics(unittest.TestCase):
    def test_preflight_only_never_runs_load_or_reserves(self):
        with patch.object(sys,'argv',['baseline','--expected-commit','a'*40,'--preflight-only']), patch.object(m,'preflight',return_value={}) as check, patch.object(m,'execute_load') as load, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(),0)
        check.assert_called_once_with('a'*40)
        load.assert_not_called()
    def test_stage_failure_prints_no_exception_payload(self):
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):
            with self.assertRaises(ValueError):
                with m.check_stage('host_tests'):
                    raise ValueError('secret-token-not-for-output')
        self.assertIn('host_tests',stream.getvalue())
        self.assertNotIn('secret-token',stream.getvalue())
