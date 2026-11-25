import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import project

class Pins(unittest.TestCase):
    def test_pinned_override_is_used_without_changes_to_generic_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.runtime').mkdir()
            pin=root/'.runtime/server-images.json'
            pin.write_text(json.dumps({'services':{'api':{'image':'sha256:'+'a'*64}}}))
            with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'')) as run:
                project.compose(root,{'COMPOSE_PROJECT_NAME':'test'},'up','api')
            self.assertIn(str(pin),run.call_args.args[0])

    def test_override_cannot_inject_ports_or_mutable_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.runtime').mkdir()
            for body in ({'services':{'api':{'image':'latest'}}}, {'services':{'api':{'image':'sha256:'+'a'*64,'ports':['8000:8000']}}}):
                (root/'.runtime/server-images.json').write_text(json.dumps(body))
                with patch('subprocess.run') as run,self.assertRaises(ValueError):
                    project.compose(root,{'COMPOSE_PROJECT_NAME':'test'},'up','api')
                run.assert_not_called()
