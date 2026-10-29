import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from capacity_handoff import decision,validate,POLICY,GIB
from handoff import package_report,read_report,write_json

class Capacity(unittest.TestCase):
    def fixture(self):
        containers=[dict(container_id='a'*64,project_sha256='b'*64,scope='original',service='api',memory_bytes=4*GIB,nano_cpus=2*10**9,image_id='sha256:'+'c'*64)]
        engine=dict(cpus=4,memory_bytes=14*GIB)
        r=dict(protocol='capacity-v1',status='passed',engine=engine,containers=containers,policy=POLICY,decision=decision(engine,containers),baseline_run_id='benchmark-mac24-20261023T213836Z-a7a170b5',load_started=False,test_evaluated=False)
        env=dict(source_commit='d'*40,runner_tree_sha256='e'*64,baseline_archive_sha256='f'*64)
        report=dict(schema_version=1,kind='capacity-handoff',run_id='capacity-local-test',machine_id='local',context='local',source_commit='d'*40,source_clean=True,started_at='2026-10-23T00:00:00+00:00',finished_at='2026-10-23T00:00:01+00:00',status='passed',warnings=[],steps=[dict(name='capacity',log='job.log',status='passed',exit_code=0)],result=r)
        return report,r,env
    def test_all_projects_count_and_margin_is_required(self):
        _,r,_=self.fixture();r['containers'].append(dict(r['containers'][0],container_id='1'*64,scope='other',memory_bytes=6*GIB))
        d=decision(r['engine'],r['containers'])
        self.assertEqual(d['required_with_job_bytes'],15*GIB)
        self.assertEqual(d['admission'],'sequential_policy_required')
    def test_unlimited_is_not_free_capacity(self):
        _,r,_=self.fixture();r['containers'][0]['memory_bytes']=0
        self.assertEqual(decision(r['engine'],r['containers'])['admission'],'inspect_unknown_limits')
    def test_rejects_forged_admission_and_private_fields(self):
        report,r,env=self.fixture()
        for key,value in [('load_started',True),('customer_id','private'),('decision',{})]:
            changed=copy.deepcopy(r);changed[key]=value
            with self.assertRaises(ValueError):validate(report,changed,env)
    def test_roundtrip_and_github_validation(self):
        report,r,env=self.fixture()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/report['run_id'];folder.mkdir()
            write_json(folder/'result.json',r);write_json(folder/'environment.json',env)
            (folder/'commit.txt').write_text(env['source_commit']+'\n');(folder/'job.log').write_text('capacity T8-B\nstatus: passed\n')
            archive=package_report(folder,report)
            self.assertEqual(read_report(archive)['result'],r)
            spec=importlib.util.spec_from_file_location('gh_capacity',Path(__file__).resolve().parents[2]/'scripts/github-handoff.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            self.assertEqual(mod.validate_archive(archive)['kind'],'capacity-handoff')
            self.assertIn('capacity',mod.marker_for(report['run_id']))
