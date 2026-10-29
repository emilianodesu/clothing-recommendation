"""Host preparation must keep existing services and expose no credentials."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class PipelineCliTests(unittest.TestCase):
    def test_pipeline_has_non_destructive_prepare_and_status_commands(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('pipeline_control'))
        import pipeline_control as p
        with patch.object(p,'compose',return_value='pg') as compose:
            p.require_postgres(Path('/tmp'),{})
        self.assertEqual(compose.call_args.args[2:],('ps','--status','running','-q','postgres'))

    def test_prepare_rejects_missing_postgres_without_up(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('pipeline_control'))
        import pipeline_control as p
        with patch.object(p,'compose',return_value='') as compose:
            with self.assertRaises(ValueError):p.require_postgres(Path('/tmp'),{})
        self.assertEqual(compose.call_count,1)

    def test_pipeline_check_flags_dispatch_without_extra_separator(self):
        import project,pipeline_check
        args=['project.py','pipeline-check','--machine-id','mac24','--expected-commit','a'*40,'--source-run-id','events-mac24-original']
        with patch.object(sys,'argv',args),patch.object(pipeline_check,'run',return_value=17):
            self.assertEqual(project.main(),17)
