"""Render real Compose without starting Docker or printing interpolated secrets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from bootstrap import prepare_configuration
import server_gateway
ROOT=Path(__file__).resolve().parents[2]

class GatewayCompose(unittest.TestCase):
    @unittest.skipUnless(shutil.which('docker'),'Docker CLI needed; no daemon used')
    def test_gateway_resolves_application_network_not_its_own_project(self):
        with tempfile.TemporaryDirectory(prefix='server space ') as temp:
            root=Path(temp);v=prepare_configuration(root);(root/'deploy').mkdir()
            shutil.copyfile(ROOT/'deploy/compose.gateway.yaml',root/'deploy/compose.gateway.yaml')
            real_run=subprocess.run;captured=[]
            def run(args,**kwargs):
                result=real_run(args,**dict(kwargs,text=True,capture_output=True))
                captured.append(result.stdout);return result
            with patch('subprocess.run',side_effect=run):
                server_gateway.compose(root,v,'config','--format','json')
            data=json.loads(captured[0])
            self.assertEqual(data['networks']['application']['name'],v['COMPOSE_PROJECT_NAME']+'_api-publication')
            self.assertEqual(set(data['services']),{'gateway'})
            c=data['services']['gateway']
            self.assertEqual({p['published'] for p in c['ports']},{'80','443'})
            self.assertEqual(int(c['mem_limit']),256*1024**2)
