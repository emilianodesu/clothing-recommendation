"""Exercise the probe's real HTTP path without Docker or private data."""
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import sys,threading,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class PipelineHTTPTests(unittest.TestCase):
    def test_json_probe_uses_real_http_and_rejects_unexpected_status(self):
        from pipeline_check import request_json
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path=='/health/live' else 404);self.end_headers();self.wfile.write(b'{"ready":true}')
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.assertEqual(request_json(server.server_port,'fixture','/health/live'),{'ready':True})
            with self.assertRaises(ValueError):request_json(server.server_port,'fixture','/missing')
        finally:server.shutdown();server.server_close();thread.join()
