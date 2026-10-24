#!/usr/bin/env python3
"""Explicit native Caddy probe: real TLS/auth/WebSocket, synthetic data, loopback only."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from server_gateway import caddyfile


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def probe(binary):
    seen=[]
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            seen.append(self.headers.get('Authorization'))
            if self.headers.get('Upgrade','').lower()=='websocket':
                accept=base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade')
                self.send_header('Sec-WebSocket-Accept',accept);self.end_headers();return
            body=b'NU_TEST synthetic upstream';self.send_response(200)
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    password='NU_TEST_'+os.urandom(16).hex()
    digest=subprocess.check_output([str(binary),'hash-password','--algorithm','bcrypt','--bcrypt-cost','14'],input=password+'\n',text=True).strip()
    http_port,tls_port,admin_port=port(),port(),port()
    with tempfile.TemporaryDirectory(prefix='hm-gateway-') as directory:
        folder=Path(directory)
        text=caddyfile('gateway.example.com',{'test':digest})
        text=text.replace('admin localhost:2019',f'admin 127.0.0.1:{admin_port}\n    skip_install_trust\n    default_bind 127.0.0.1\n    http_port {http_port}')
        text=text.replace('gateway.example.com {',f'https://localhost:{tls_port} {{\n    tls internal')
        text=text.replace('ui:8501','127.0.0.1:'+str(upstream.server_port))
        (folder/'Caddyfile').write_text(text)
        environment=dict(os.environ,XDG_DATA_HOME=str(folder/'data'),XDG_CONFIG_HOME=str(folder/'config'))
        with (folder/'caddy.log').open('w') as log:
            process=subprocess.Popen([str(binary),'run','--config',str(folder/'Caddyfile')],env=environment,stdout=log,stderr=log)
            try:
                ca=folder/'data/caddy/pki/authorities/local/root.crt'
                deadline=time.monotonic()+20
                while not ca.is_file():
                    if process.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError('Caddy did not start: '+(folder/'caddy.log').read_text()[-2000:])
                    time.sleep(.1)
                context=ssl.create_default_context(cafile=str(ca))
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self,*args,**kwargs):return None
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),NoRedirect())
                auth='Basic '+base64.b64encode(('test:'+password).encode()).decode()
                with opener.open(f'http://127.0.0.1:{admin_port}/config/',timeout=10) as response:
                    assert response.status==200
                def request(path,header=None):
                    req=urllib.request.Request(f'https://localhost:{tls_port}'+path,headers={'Authorization':header} if header else {})
                    try:response=opener.open(req,timeout=10)
                    except urllib.error.HTTPError as error:response=error
                    with response:return response.status,response.read(),response.headers
                for path in ('/','/_stcore/health','/_stcore/stream','/static/test.js'):
                    assert request(path)[0]==401,path
                assert request('/','Basic '+base64.b64encode(b'test:NU_TEST_wrong').decode())[0]==401
                assert request('/',auth)[0]==200
                assert request('/_stcore/health',auth)[0]==200
                with socket.create_connection(('127.0.0.1',tls_port),timeout=10) as sock:
                    with context.wrap_socket(sock,server_hostname='localhost') as tls:
                        key=base64.b64encode(os.urandom(16)).decode()
                        tls.sendall((f'GET /_stcore/stream HTTP/1.1\r\nHost: localhost:{tls_port}\r\nAuthorization: {auth}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n').encode())
                        assert b'101 Switching Protocols' in tls.recv(8192)
                try:opener.open(f'http://localhost:{http_port}/',timeout=10)
                except urllib.error.HTTPError as error:
                    with error:assert error.code==308 and error.headers['Location']==f'https://localhost:{tls_port}/', dict(error.headers)
                else:raise AssertionError('Missing HTTP redirect')
                assert len(seen)==3 and all(value is None for value in seen)
                return dict(status='passed',tls_verified_with_ephemeral_ca=True,unauthorized_paths=4,
                    wrong_password_rejected=True,authenticated_http=True,authenticated_websocket_101=True,
                    authorization_removed_upstream=True,http_redirect_308=True,
                    native_proxy_only=True,public_dns_acme_tested=False,docker_deployment_tested=False)
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                upstream.shutdown();upstream.server_close();thread.join(timeout=5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--caddy',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(probe(args.caddy.resolve()),indent=2))
