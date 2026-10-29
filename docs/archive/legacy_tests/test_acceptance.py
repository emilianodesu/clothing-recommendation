import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from acceptance import admit, summarize_junit, test_command

class AcceptanceTests(unittest.TestCase):
    def test_busy_engine_rejected(self):
        with self.assertRaises(ValueError):admit('existing-container',8*2**30,4)
    def test_insufficient_capacity_rejected(self):
        for mem,cpu in [(6*2**30,4),(8*2**30,1)]:
            with self.assertRaises(ValueError):admit('',mem,cpu)
        admit('',8*2**30,4)
    def test_skipped_or_empty_tests_cannot_pass(self):
        for xml in ['<testsuites/>','<testsuite tests="2" failures="0" errors="0" skipped="1"/>']:
            with self.assertRaises(ValueError):summarize_junit(xml)
        self.assertEqual(summarize_junit('<testsuite tests="3" failures="0" errors="0" skipped="0"/>')['tests'],3)
    def test_no_host_mounts_or_real_campaign(self):
        cmd=test_command('hmrec-acceptance-test','net','sha256:'+'a'*64)
        self.assertNotIn('-v',cmd);self.assertNotIn('--mount',cmd)
        self.assertIn('--read-only',cmd)
        self.assertNotIn('tests/integration/test_final_evaluation.py',cmd)
        self.assertIn('tests/integration/test_demo.py',cmd)

    def test_timeout_keeps_private_output(self):
        import tempfile,subprocess
        from unittest.mock import patch
        from acceptance import run_logged
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'log'
            with patch('acceptance.subprocess.run',side_effect=subprocess.TimeoutExpired(['docker'],1,output=b'partial',stderr=b'error')):
                with self.assertRaises(subprocess.TimeoutExpired):run_logged(['docker'],log)
            self.assertEqual(log.read_text(),'partialerror')
