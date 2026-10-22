"""Exercise the host checker with real TCP and independently constructed replies."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'src')]
CODE='c'*64;RELEASE='h-'+'a'*64
BASE={'release_id':RELEASE,'strategy':'recent_then_popular','features_timestamp':'2020-09-09',
      'fallback':True,'items':[{'article_id':'000123','rank':1,'reason':'popular_7d'}]}
EMPTY={**BASE,'items':[]}
EXPECTED=hashlib.sha256((''.join(json.dumps(x,sort_keys=True,separators=(',',':')) for x in (BASE,EMPTY))).encode()).hexdigest()


class HostProbeTests(unittest.TestCase):
    def setUp(self):
        self.mode='good';self.requests=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,body):
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps(body).encode())
            def do_GET(self):
                owner.requests.append(self.path)
                if owner.mode=='redirect':
                    self.send_response(302);self.send_header('Location','/redirect-target');self.end_headers();return
                if self.path=='/health/live':self.reply({'status':'alive','source_tree_sha256':'d'*64 if owner.mode=='code' else CODE})
                else:self.reply({'status':'ready','release_id':'h-'+'b'*64 if owner.mode=='version' else RELEASE})
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(data)
                body=copy.deepcopy(EMPTY if data['filters'] else BASE)
                if owner.mode=='ranking':body['items']=[]
                body['request_id']='synthetic-request'
                if owner.mode=='request-id':body.pop('request_id')
                self.reply(body)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.port=self.server.server_port
        self.bindings={'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':str(self.port)}]}

    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def probe(self,**kwargs):
        from serving_host import probe_host
        return probe_host(kwargs.get('port',self.port),RELEASE,CODE,EXPECTED,kwargs.get('bindings',self.bindings))

    def test_reads_loopback_without_proxy_and_exports_only_aggregates(self):
        with patch.dict('os.environ',{'HTTP_PROXY':'http://127.0.0.1:1','http_proxy':'http://127.0.0.1:1','NO_PROXY':'','no_proxy':''}):
            result=self.probe()
        self.assertEqual(result['checked_requests'],2)
        self.assertEqual(result['response_sha256'],EXPECTED)
        self.assertTrue(result['http_parity'] and result['binding_verified'])
        self.assertNotIn('000123',json.dumps(result))
        self.assertNotIn('customer_id',json.dumps(result))
        self.assertEqual([r['filters'] for r in self.requests if isinstance(r,dict)], [{},{'colour_group_name':'__hmrec_empty_filter__'}])

    def test_wrong_version_code_or_recommendations_fail(self):
        for mode in ('version','code','ranking','request-id'):
            with self.subTest(mode=mode):
                self.mode=mode
                with self.assertRaises(ValueError):self.probe()

    def test_missing_wrong_or_public_binding_fails_before_requests(self):
        for binding in (None,[],[{'HostIp':'0.0.0.0','HostPort':str(self.port)}],[{'HostIp':'127.0.0.1','HostPort':'1'}]):
            with self.subTest(binding=binding), self.assertRaises(ValueError):self.probe(bindings={'8000/tcp':binding})
        self.assertEqual(self.requests,[])

    def test_refuses_redirects(self):
        from urllib.error import HTTPError
        self.mode='redirect'
        with self.assertRaises(HTTPError):self.probe()
        self.assertEqual(self.requests,['/health/live'])

    def test_unreachable_host_is_not_success(self):
        from urllib.error import URLError
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            with self.assertRaises(URLError):self.probe(port=port,bindings={'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':str(port)}]})
