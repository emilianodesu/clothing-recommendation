"""Host Docker regression: the actual Compose API must publish on loopback."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('HMREC_DOCKER_TESTS') == '1', 'host Docker integration only')
class ServingPublicationTests(unittest.TestCase):
    def test_actual_compose_publishes_api_but_keeps_database_private(self):
        sys.path.insert(0, str(ROOT/'scripts'))
        from bootstrap import prepare_configuration
        from project import compose
        location = ROOT/'.runtime/tests';location.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='serving publication ', dir=location) as d:
            root=Path(d)
            shutil.copy2(ROOT/'compose.yaml', root/'compose.yaml')
            shutil.copytree(ROOT/'docker/postgres', root/'docker/postgres')
            values=prepare_configuration(root)
            (root/'artifacts/serving').mkdir()
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0));values['API_PORT']=str(s.getsockname()[1])
            try:
                compose(root, values, 'up', '-d', '--wait', 'postgres', timeout=120)
                compose(root, values, 'run', '--rm', '--no-deps', '-T', 'jobs', 'hmrec', 'db', 'migrate', timeout=120)
                compose(root, values, '--profile', 'serve', 'up', '-d', '--wait', '--wait-timeout', '60', 'api', timeout=120)
                cid=compose(root, values, 'ps', '-q', 'api', capture=True).strip()
                ports=json.loads(subprocess.check_output(['docker','inspect',cid,'--format','{{json .NetworkSettings.Ports}}'],text=True))
                self.assertEqual(ports.get('8000/tcp'), [{'HostIp':'127.0.0.1','HostPort':values['API_PORT']}])
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                url='http://127.0.0.1:'+values['API_PORT']
                with opener.open(url+'/health/live', timeout=5) as r:
                    self.assertEqual(json.load(r)['status'], 'alive')
                with socket.socket() as s:
                    s.bind(('127.0.0.1',0));values['UI_PORT']=str(s.getsockname()[1])
                compose(root,values,'--profile','serve','up','-d','--wait','--wait-timeout','60','ui',timeout=120)
                uid=compose(root,values,'ps','-q','ui',capture=True).strip()
                ui_ports=json.loads(subprocess.check_output(['docker','inspect',uid,'--format','{{json .NetworkSettings.Ports}}'],text=True))
                self.assertEqual(ui_ports.get('8501/tcp'),[{'HostIp':'127.0.0.1','HostPort':values['UI_PORT']}])
                with opener.open('http://127.0.0.1:'+values['UI_PORT']+'/_stcore/health',timeout=5) as r:
                    self.assertEqual(r.status,200)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    opener.open(url+'/health/ready', timeout=5)
                self.assertEqual(error.exception.code, 503)  # No package activated in this fixture.
                error.exception.close()
                pg=compose(root, values, 'ps', '-q', 'postgres', capture=True).strip()
                networks=json.loads(subprocess.check_output(['docker','inspect',pg,'--format','{{json .NetworkSettings.Networks}}'],text=True))
                self.assertEqual(len(networks), 1)
                for network in networks:
                    self.assertEqual(subprocess.check_output(['docker','network','inspect',network,'--format','{{.Internal}}'],text=True).strip(), 'true')
                bindings=json.loads(subprocess.check_output(['docker','inspect',pg,'--format','{{json .HostConfig.PortBindings}}'],text=True))
                self.assertFalse(bindings)
            finally:
                compose(root, values, '--profile', '*', 'down', '--volumes', timeout=120)


if __name__ == '__main__':unittest.main()
