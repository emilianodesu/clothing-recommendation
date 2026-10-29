import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from h_cycle_worker import run_child


class CycleWorker(unittest.TestCase):
    def test_timeout_kills_child_before_publishing_exit_marker(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'worker.json';child=Mock()
            def wait(timeout=None):
                self.assertFalse(output.with_suffix('.exit.json').exists())
                if timeout is not None:raise subprocess.TimeoutExpired('build',timeout)
                return -9
            child.wait.side_effect=wait
            with patch('h_cycle_worker.subprocess.Popen',return_value=child):
                self.assertEqual(run_child('config',output),124)
            self.assertTrue(json.loads(output.with_suffix('.exit.json').read_text())['timed_out'])
            child.kill.assert_called_once();child.terminate.assert_called_once()

    def test_unknown_exit_does_not_authorize_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'worker.json';child=Mock();child.wait.side_effect=OSError('lost child')
            with patch('h_cycle_worker.subprocess.Popen',return_value=child):
                with self.assertRaises(OSError):run_child('config',output)
            self.assertFalse(output.with_suffix('.exit.json').exists())
