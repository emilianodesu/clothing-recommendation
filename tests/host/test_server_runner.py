import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
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
            replies=[
                (401,{},b''),
                (401,{},b''),
                (200,{},b''),
                (200,{},b'ok'),
                (401,{},b''),
                (200,{},b'<form method="POST" action="/login"><input name="username"><input name="password"></form>'),
                (307,{'Location':'/login'},b''),
                (303,{'Set-Cookie':'hmrec_session=mock-session-token; HttpOnly; Path=/'},b''),
                (200,{},b''),
                (308,{'Location':'https://team.example.com/'},b''),
            ]
            ws_sock=MagicMock()
            ws_sock.recv.return_value=b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n'
            ssl_ctx=MagicMock()
            ssl_ctx.wrap_socket.return_value.__enter__.return_value=ws_sock
            with (
                patch.object(server,'http',side_effect=replies) as http,
                patch.object(server.getpass,'getpass',return_value='private password phrase'),
                patch('socket.create_connection',return_value=MagicMock()) as create_conn,
                patch('ssl.create_default_context',return_value=ssl_ctx),
            ):
                result=server.check_public(root,'ana')
            self.assertEqual(http.call_count,10)
            create_conn.assert_called_once_with(('team.example.com',443),timeout=15)
            ssl_ctx.wrap_socket.assert_called_once()
            sent_ws=ws_sock.sendall.call_args.args[0]
            self.assertIn(b'GET /_stcore/stream HTTP/1.1',sent_ws)
            self.assertIn(b'Cookie: hmrec_session=mock-session-token',sent_ws)
            self.assertTrue(result['browser_websocket_verified'])
            self.assertTrue(result['websocket_auth_guard'])
            recorded=(root/'.runtime/server/public-check.json').read_text()
            self.assertNotIn('password',recorded)
            self.assertNotIn('Authorization',recorded)
            self.assertNotIn('mock-session-token',recorded)

    def test_public_check_rejects_incomplete_login_portal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.runtime/server').mkdir(parents=True)
            (root/'.runtime/server/gateway.json').write_text(json.dumps(dict(domain='team.example.com',users={'ana':'hash'})))
            replies=[
                (401,{},b''),
                (401,{},b''),
                (200,{},b''),
                (200,{},b'ok'),
                (401,{},b''),
                (200,{},b'ok'),
            ]
            with (
                patch.object(server,'http',side_effect=replies),
                patch.object(server.getpass,'getpass',return_value='private password phrase'),
            ):
                with self.assertRaisesRegex(ValueError,'Portal de login'):
                    server.check_public(root,'ana')

    def test_public_check_rejects_failed_websocket_handshake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.runtime/server').mkdir(parents=True)
            (root/'.runtime/server/gateway.json').write_text(json.dumps(dict(domain='team.example.com',users={'ana':'hash'})))
            replies=[
                (401,{},b''),
                (401,{},b''),
                (200,{},b''),
                (200,{},b'ok'),
                (401,{},b''),
                (200,{},b'<form method="POST" action="/login"><input name="username"><input name="password"></form>'),
                (307,{'Location':'/login'},b''),
                (303,{'Set-Cookie':'hmrec_session=mock-session-token; HttpOnly; Path=/'},b''),
                (200,{},b''),
            ]
            ws_sock=MagicMock()
            ws_sock.recv.return_value=b'HTTP/1.1 403 Forbidden\r\n\r\n'
            ssl_ctx=MagicMock()
            ssl_ctx.wrap_socket.return_value.__enter__.return_value=ws_sock
            with (
                patch.object(server,'http',side_effect=replies),
                patch.object(server.getpass,'getpass',return_value='private password phrase'),
                patch('socket.create_connection',return_value=MagicMock()),
                patch('ssl.create_default_context',return_value=ssl_ctx),
            ):
                with self.assertRaisesRegex(ValueError,'WebSocket'):
                    server.check_public(root,'ana')
