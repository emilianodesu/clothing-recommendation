import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import mac24_t8_align_api as m

class ApiAlignment(unittest.TestCase):
    def test_wrong_image_prevents_recreation(self):
        with patch.object(m,'command',return_value='other'),patch.object(m,'compose') as compose:
            with self.assertRaises(ValueError):m.replace_api({},'expected')
            compose.assert_not_called()
    def test_recreation_targets_only_api_without_build_pull_or_dependencies(self):
        with patch.object(m,'command',return_value='expected'),patch.object(m,'compose') as compose:
            m.replace_api({},'expected')
            compose.assert_called_once_with(m.ROOT,{},'up','-d','--no-deps','--no-build','--pull','never','api',capture=True,timeout=180)
    def test_rejects_postgres_restart_and_state_change(self):
        d=dict(container_id='id',started_at='start',restart_count=0,image_id='image')
        before={'api':dict(d),'postgres':dict(d),'ui':dict(d)}
        after={k:dict(v) for k,v in before.items()};after['postgres']['restart_count']=1
        with self.assertRaises(ValueError):m.verify_preserved(before,after,'a','a')
        with self.assertRaises(ValueError):m.verify_preserved(before,before,'a','b')
        m.verify_preserved(before,before,'a','a')
