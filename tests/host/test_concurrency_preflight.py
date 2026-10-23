import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import mac24_t8_concurrent as m

class ConcurrencyPreflight(unittest.TestCase):
    def test_diagnostic_never_calls_load_or_recovery(self):
        with patch.object(sys,'argv',['run','--expected-commit','a'*40,'--preflight-only']),patch.object(m,'preflight') as check,patch.object(m,'run') as run,patch.object(m,'recovery') as recovery,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(),0)
        check.assert_called_once_with('a'*40);run.assert_not_called();recovery.assert_not_called()
    def test_error_stage_does_not_disclose_payload(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaises(ValueError):
            with m.stage('power'):raise ValueError('private password')
        self.assertIn('FALLO: power (ValueError)',output.getvalue());self.assertNotIn('password',output.getvalue())
    def test_failed_preflight_does_not_continue_into_load(self):
        with patch.object(m,'preflight',side_effect=ValueError),patch.object(m.STATE.__class__,'mkdir') as mkdir:
            with self.assertRaises(ValueError):m.run('a'*40)
            mkdir.assert_not_called()
