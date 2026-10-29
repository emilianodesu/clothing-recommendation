import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import server


class ServerRunner(unittest.TestCase):
    def test_build_admission_failure_does_not_prepare_or_start(self):
        with patch.object(server,'source_commit',return_value='a'*40),patch.object(server,'guarded',side_effect=ValueError('RAM')),patch.object(server,'prepare_start_configuration') as prepare:
            with self.assertRaisesRegex(ValueError,'RAM'):server.build(Path('/unused'))
            prepare.assert_not_called()

    def test_publish_requires_local_health_before_any_gateway_command(self):
        with patch.object(server,'guarded'),patch.object(server,'local_check',side_effect=ValueError('not ready')),patch.object(server.gateway,'compose') as compose:
            with self.assertRaises(ValueError):server.publish(Path('/unused'))
            compose.assert_not_called()

    def test_doctor_does_not_create_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(server,'inventory',return_value=dict(system='Darwin')),patch('builtins.print'):
                self.assertEqual(server.main(['--root',tmp,'doctor']),0)
            self.assertEqual(list(root.iterdir()),[])

    def test_public_check_rejects_an_open_site_without_reading_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.runtime/server').mkdir(parents=True)
            (root/'.runtime/server/gateway.json').write_text(json.dumps(dict(domain='team.example.com',users={'ana':'hash'})))
            with patch.object(server,'http',return_value=(200,{},b'')),patch.object(server.getpass,'getpass') as password:
                with self.assertRaisesRegex(ValueError,'rechazar'):server.check_public(root,'ana')
                password.assert_not_called()

    def test_public_check_checks_auth_health_and_redirect_without_recording_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.runtime/server').mkdir(parents=True)
            (root/'.runtime/server/gateway.json').write_text(json.dumps(dict(domain='team.example.com',users={'ana':'hash'})))
            replies=[(401,{},b''),(401,{},b''),(200,{},b''),(200,{},b'ok'),(401,{},b''),(308,{'Location':'https://team.example.com/'},b'')]
            with patch.object(server,'http',side_effect=replies) as http,patch.object(server.getpass,'getpass',return_value='private password phrase'):
                result=server.check_public(root,'ana')
            self.assertEqual(http.call_count,6)
            self.assertFalse(result['browser_websocket_verified'])
            self.assertNotIn('password',(root/'.runtime/server/public-check.json').read_text())
            self.assertNotIn('Authorization',(root/'.runtime/server/public-check.json').read_text())
