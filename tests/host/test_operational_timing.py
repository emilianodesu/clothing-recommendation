"""Observable deadlines and closed timing evidence, without exporting request data."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


class Clock:
    def __init__(self): self.wall=10000.; self.mono=100.
    def __call__(self): return self.wall,self.mono
    def advance(self,wall,mono=None): self.wall+=wall; self.mono+=wall if mono is None else mono


class Guard:
    def __init__(self): self.alive=True; self.released=False
    def __enter__(self): return self
    def check(self):
        if not self.alive: raise ValueError('owned process exited')
    def __exit__(self,*args): self.released=True


class OperationalTimingTests(unittest.TestCase):
    def monitor(self):
        self.assertIsNotNone(importlib.util.find_spec('operational_timing'),'Missing monitored operations')
        from operational_timing import Monitor
        clock=Clock();guard=Guard();monitor=Monitor(guard=guard,sample=clock)
        monitor.start();return monitor,clock,guard

    def test_operation_receives_decreasing_budget_and_retains_both_clocks(self):
        monitor,clock,guard=self.monitor()
        def work(left):
            self.assertEqual(left(),10);clock.advance(3);self.assertEqual(left(),7);return 'done'
        self.assertEqual(monitor.perform('seed',10,work),'done')
        monitor.finish();e=monitor.evidence()
        self.assertTrue(e['complete']);self.assertTrue(guard.released)
        self.assertEqual(e['assessment']['utc_seconds'],3)
        self.assertEqual(e['assessment']['monotonic_seconds'],3)
        self.assertEqual(e['operations'][0]['status'],'passed')

    def test_jump_regression_budget_and_lost_guard_never_become_success(self):
        for kind in ('jump','regression','budget','guard'):
            monitor,clock,guard=self.monitor()
            def work(left):
                if kind=='jump':clock.advance(900,1)
                if kind=='regression':clock.advance(-1,1)
                if kind=='budget':clock.advance(7201)
                if kind=='guard':guard.alive=False
            with self.subTest(kind=kind),self.assertRaises(ValueError):monitor.perform('seed',7200,work)
            monitor.finish();e=monitor.evidence()
            self.assertFalse(e['complete']);self.assertFalse(e['assessment']['coherent']);self.assertTrue(guard.released)
            self.assertGreaterEqual(len(e['marks']),2)

    def test_operation_timeout_and_exception_preserve_failed_trace(self):
        for interrupt in (False,True):
            monitor,clock,guard=self.monitor()
            def work(left):
                if interrupt:raise KeyboardInterrupt()
                clock.advance(11);left()
            with self.subTest(interrupt=interrupt),self.assertRaises((TimeoutError,KeyboardInterrupt)):
                monitor.perform('seed',10,work)
            monitor.finish();e=monitor.evidence()
            self.assertFalse(e['complete']);self.assertTrue(guard.released)
            self.assertEqual(e['operations'][0]['status'],'failed')

    def test_invalid_sample_and_trace_bound_are_reportable(self):
        monitor,clock,guard=self.monitor();clock.wall=float('nan')
        with self.assertRaises(ValueError):monitor.perform('seed',10,lambda left:None)
        monitor.finish();e=monitor.evidence()
        self.assertEqual(e['capture_error'],'invalid_clock')
        self.assertNotIn('NaN',json.dumps(e,allow_nan=False));self.assertFalse(e['complete'])
        monitor,clock,guard=self.monitor()
        with self.assertRaises(ValueError):
            for _ in range(5000):monitor.mark('poll')
        monitor.finish();self.assertEqual(monitor.evidence()['capture_error'],'trace_limit');self.assertTrue(guard.released)

    def test_cgroup_capture_propagates_bounded_transport(self):
        # If capture ignores the callback it would invoke Docker on the real host.
        import events_memory
        def unavailable(command,**kwargs):raise TimeoutError('deadline')
        with self.assertRaises(TimeoutError):events_memory.capture('a'*64,'api','before_seed',0,output=unavailable)

    def test_http_uses_local_transport_and_never_exports_payload_or_credentials(self):
        self.assertIsNotNone(importlib.util.find_spec('operational_io'),'Missing bounded HTTP')
        from operational_io import OperationalIO
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body=self.rfile.read(int(self.headers['Content-Length']))
                data=json.dumps({'received':json.loads(body)}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_GET(self):
                self.send_response(302);self.send_header('Location','http://example.invalid/');self.end_headers()
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            monitor,clock,guard=self.monitor()
            io=OperationalIO(Path('.'),{'API_PORT':str(server.server_port),'HM_APP_PASSWORD':'PRIVATE-TOKEN'},monitor)
            self.assertEqual(io.request('/events',{'customer_id':'PRIVATE-CUSTOMER'})['received']['customer_id'],'PRIVATE-CUSTOMER')
            with self.assertRaises(ValueError):io.request('/health/live')
            monitor.finish();encoded=json.dumps(monitor.evidence())
            self.assertNotIn('PRIVATE',encoded);self.assertNotIn('/events',encoded)
            self.assertFalse(monitor.evidence()['complete'])
        finally:server.shutdown();server.server_close();thread.join()

    def test_http_readiness_cap_and_slow_body_fail_with_closed_trace(self):
        self.assertIsNotNone(importlib.util.find_spec('operational_io'))
        from operational_io import OperationalIO
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading,time
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Length','10');self.end_headers()
                try:self.wfile.write(b'{');self.wfile.flush();time.sleep(.3);self.wfile.write(b'         ')
                except OSError:pass
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            monitor,clock,guard=self.monitor()
            io=OperationalIO(Path('.'),{'API_PORT':str(server.server_port),'HM_APP_PASSWORD':'secret'},monitor)
            started=time.monotonic()
            with self.assertRaises(TimeoutError):io.request('/health/ready',limit=.05)
            self.assertLess(time.monotonic()-started,1)
            monitor.finish();self.assertFalse(monitor.evidence()['complete'])
        finally:server.shutdown();server.server_close();thread.join()

    def test_retry_is_allowed_only_for_readiness_that_later_succeeds(self):
        monitor,clock,guard=self.monitor()
        with self.assertRaises(OSError):monitor.perform('health_ready',10,lambda left:(_ for _ in ()).throw(OSError('pending')))
        monitor.retry_ready();monitor.perform('health_ready',10,lambda left:'ready');monitor.finish()
        self.assertTrue(monitor.evidence()['complete']);self.assertEqual(monitor.evidence()['operations'][0]['status'],'failed')
        monitor,clock,guard=self.monitor()
        with self.assertRaises(OSError):monitor.perform('seed',10,lambda left:(_ for _ in ()).throw(OSError('failed')))
        with self.assertRaises(ValueError):monitor.retry_ready()
        monitor.finish();self.assertFalse(monitor.evidence()['complete'])

    def test_non_json_and_oversized_http_body_fail_without_exporting_contents(self):
        from operational_io import OperationalIO
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data=b'PRIVATE not json' if self.path=='/health/live' else b'x'*(2*1024*1024+1)
                self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers()
                try:self.wfile.write(data)
                except OSError:pass
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for path in ('/health/live','/health/ready'):
                monitor,clock,guard=self.monitor()
                io=OperationalIO(Path('.'),{'API_PORT':str(server.server_port),'HM_APP_PASSWORD':'secret'},monitor)
                with self.subTest(path=path),self.assertRaises(ValueError):io.request(path)
                monitor.finish();self.assertFalse(monitor.evidence()['complete'])
                self.assertNotIn('PRIVATE',json.dumps(monitor.evidence()))
        finally:server.shutdown();server.server_close();thread.join()

    def test_real_child_timeout_is_reaped_and_guard_is_released(self):
        self.assertIsNotNone(importlib.util.find_spec('operational_io'))
        from operational_io import OperationalIO
        import subprocess
        monitor,clock,guard=self.monitor();io=OperationalIO(Path('.'),{},monitor)
        with self.assertRaises(subprocess.TimeoutExpired):
            io.output([sys.executable,'-c','import time; time.sleep(2)'],timeout=.05,text=True)
        monitor.finish();self.assertTrue(guard.released);self.assertFalse(monitor.evidence()['complete'])
