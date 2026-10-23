import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class PortabilityHost(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('restore'),'restore launcher missing')
        import restore
        return restore
    def test_destination_existing_rejected_before_writes(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'sentinel').write_text('keep')
            with self.assertRaises(ValueError):m.prepare_destination(Path(__file__).resolve().parents[2],p,18009)
            self.assertEqual((p/'sentinel').read_text(),'keep')
    def test_no_secrets_or_runtime_copied(self):
        m=self.module();root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'new user space';v=m.prepare_destination(root,p,18009)
            self.assertTrue((p/'compose.yaml').is_file())
            self.assertEqual(v['API_PORT'],'18009')
            self.assertFalse((p/'artifacts/serving').exists())
            self.assertFalse((p/'.git').exists())
            self.assertEqual((p/'.env').stat().st_mode&0o777,0o600)
    def test_restored_source_can_prepare_another_installation(self):
        m=self.module();root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            a,b=Path(d)/'first',Path(d)/'second'
            one=m.prepare_destination(root,a,18009)
            two=m.prepare_destination(a,b,18010)
            self.assertNotEqual(one['HM_APP_PASSWORD'],two['HM_APP_PASSWORD'])
            self.assertEqual((a/'src/hm_recommender/portability.py').read_bytes(),(b/'src/hm_recommender/portability.py').read_bytes())
