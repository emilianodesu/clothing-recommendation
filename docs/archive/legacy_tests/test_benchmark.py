import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import benchmark as b

class BenchmarkTests(unittest.TestCase):
    def test_omissions_and_errors_are_in_denominator(self):
        r=b.summarize([{'group':'history','ok':True,'latency':.1,'service':.09},{'group':'history','ok':False,'latency':2.,'service':2.}],4,1.)
        self.assertEqual(r['omitted'],2)
        self.assertEqual(r['error_fraction'],.75)
        self.assertEqual(r['success_rps'],1.)
        self.assertEqual(r['latency_seconds']['p95'],2.)
    def test_no_samples_cannot_pass(self):
        r=b.summarize([],20,1.)
        self.assertEqual(r['error_fraction'],1)
        self.assertIsNone(r['latency_seconds']['p95'])
    def test_open_loop_does_not_wait_for_each_request(self):
        import time
        def slow(case):time.sleep(.05);return True
        r=b.phase([{'group':'history'}],rate=100,seconds=.1,workers=2,request=slow,max_lag=.03)
        self.assertEqual(r['scheduled'],10)
        self.assertGreater(r['omitted'],0)
        self.assertEqual(r['scheduled'],r['completed']+r['omitted'])
    def test_http_requires_exact_reference(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body=json.dumps({'request_id':'sample','release_id':'fixed'}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            self.assertTrue(b.http_request(server.server_port,{'request':{},'response':{'release_id':'fixed'}}))
            self.assertFalse(b.http_request(server.server_port,{'request':{},'response':{'release_id':'other'}}))
        finally:server.shutdown();server.server_close();thread.join()
    def test_failed_closed_report_rejects_private_fields(self):
        import tempfile
        from handoff import write_json,package_report,read_report
        from benchmark_handoff import validate
        import importlib.util
        r=dict(protocol='load-baseline-v1',status='failed',warmup=None,measurement=None,first_contact=None,resources_ok=False,source_preserved=False,error_type='ValueError',test_evaluated=False,training_performed=False,t8_closed=False,generator_cpu_seconds=0.,generator_max_rss_bytes=0,population={})
        env=dict(source_commit='a'*40,runner_tree_sha256='b'*64,config_sha256='c'*64,cases_sha256=None,release_id='h-'+'d'*64,source_state_sha256=None,source_tree_sha256='e'*64)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'benchmark-local-test';folder.mkdir()
            report=dict(schema_version=1,kind='benchmark-handoff',run_id=folder.name,machine_id='local',context='colima',source_commit='a'*40,source_clean=True,started_at='2026-10-23T00:00:00+00:00',finished_at='2026-10-23T00:00:01+00:00',status='failed',warnings=[],result=r,steps=[dict(name='benchmark',log='job.log',status='failed',exit_code=1),dict(name='resources',log='resources.log',status='failed',exit_code=1)])
            for name,value in [('result.json',r),('environment.json',env),('resources.log',[])]:write_json(folder/name,value)
            (folder/'commit.txt').write_text('a'*40+'\n');(folder/'job.log').write_text('benchmark T8-A\nstatus: failed\n')
            archive=package_report(folder,report);self.assertEqual(read_report(archive)['result'],r)
            spec=importlib.util.spec_from_file_location('gh_bench',Path(__file__).resolve().parents[2]/'scripts/github-handoff.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            self.assertEqual(mod.validate_archive(archive)['run_id'],folder.name)
            r['customer_id']='private'
            with self.assertRaises(ValueError):validate(report,r,env,[])
    def test_resource_acceptance_rejects_restart_swap_and_missing_samples(self):
        import copy
        from benchmark_resources import accepted
        rows=[]
        for i in range(73):
            for service,limit,cpu in [('api',4*2**30,2_000_000_000),('postgres',512*2**20,0),('ui',512*2**20,500_000_000)]:
                memory=dict(service=service,elapsed_seconds=i*10,container_id=service,started_at='same',restart_count=0,cgroup_inode=1,image_id='same',memory_bytes=limit,cgroup_limit_bytes=limit,nano_cpus=cpu,swap_current_bytes=0,swap_peak_bytes=0,oom_killed=False,events={'oom':0,'oom_kill':0,'oom_group_kill':0})
                rows.append(dict(memory=memory,cpu_usage_usec=i*100))
        self.assertTrue(accepted(rows))
        self.assertFalse(accepted(rows[:3]))
        for key,value in [('restart_count',1),('swap_current_bytes',1),('swap_peak_bytes',1),('memory_bytes',1)]:
            changed=copy.deepcopy(rows);changed[-1]['memory'][key]=value;self.assertFalse(accepted(changed))
