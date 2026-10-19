import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from data_job import classify_result, validate_run_id


class DataJobTests(unittest.TestCase):
    def test_zero_exit_without_complete_result_is_interrupted(self):
        self.assertEqual(classify_result({'Running':False,'ExitCode':0}, None), 'interrupted')
        self.assertEqual(classify_result({'Running':False,'ExitCode':137,'OOMKilled':True}, {'status':'running'}), 'failed')
        self.assertEqual(classify_result({'Running':True}, {'status':'running'}), 'running')

    def test_success_requires_worker_verification_and_pipeline_success(self):
        state = {'Running':False,'ExitCode':0}
        self.assertEqual(classify_result(state, {'status':'passed','stage':'T2'}), 'interrupted')
        self.assertEqual(classify_result(state, {'status':'passed','stage':'T2','verification_exit_code':0}), 'passed')

    def test_run_id_cannot_escape_artifacts_directory(self):
        for name in ('../data-evil', 'data-x/child', 'bootstrap-mac24', ''):
            with self.assertRaises(ValueError):validate_run_id(name)
        self.assertEqual(validate_run_id('data-mac24-20261019T100000Z-a1b2c3d4'), 'data-mac24-20261019T100000Z-a1b2c3d4')
