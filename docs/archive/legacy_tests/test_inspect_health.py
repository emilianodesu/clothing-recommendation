"""Read-only regression against an explicitly supplied no-healthcheck container."""
import os,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class InspectHealth(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('HMREC_INSPECT_FIXTURE'),'requires explicit synthetic Docker fixture')
    def test_container_without_healthcheck_can_be_inspected(self):
        from concurrency_transition import inspect
        value=inspect(os.environ['HMREC_INSPECT_FIXTURE'])
        self.assertEqual(value['health'],'none')
        self.assertEqual(len(value['container_id']),64)
        self.assertIsInstance(value['running'],bool)
