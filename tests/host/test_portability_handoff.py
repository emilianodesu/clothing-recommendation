import importlib.util
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class PortableReport(unittest.TestCase):
    def test_failed_envelope_rejects_private_extra_fields(self):
        self.assertIsNotNone(importlib.util.find_spec('portability_handoff'),'T7 handoff validator missing')
        import portability_handoff as p
        r=p.empty_result();report={'status':'failed','source_commit':'a'*40,'source_clean':True}
        env={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'runner_tree_sha256':'c'*64}
        p.validate(report,r,env)
        r['customer_id']='private'
        with self.assertRaises(ValueError):p.validate(report,r,env)
    def test_pass_requires_all_acceptance_controls(self):
        self.assertIsNotNone(importlib.util.find_spec('portability_handoff'),'T7 handoff validator missing')
        import portability_handoff as p
        r=p.empty_result();r['status']='passed'
        with self.assertRaises(ValueError):p.validate({'status':'passed','source_commit':'a'*40,'source_clean':True},r,{'source_commit':'a'*40,'source_tree_sha256':'b'*64,'runner_tree_sha256':'c'*64})
    def test_roundtrip_failed_report(self):
        import tempfile
        import portability_handoff as p
        from handoff import package_report,read_report,write_json
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'portability-local-example';folder.mkdir()
            r=p.empty_result();env={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'runner_tree_sha256':'c'*64}
            report=dict(schema_version=1,kind='portability-handoff',run_id=folder.name,machine_id='local',context='colima',source_commit='a'*40,source_clean=True,started_at='2026-10-23T00:00:00+00:00',finished_at='2026-10-23T00:00:01+00:00',status='failed',warnings=[],steps=[dict(name='portability',log='job.log',status='failed',exit_code=1)],result=r)
            write_json(folder/'result.json',r);write_json(folder/'environment.json',env)
            (folder/'commit.txt').write_text('a'*40+'\n');(folder/'job.log').write_text('portability T7\nstatus: failed\n')
            self.assertEqual(read_report(package_report(folder,report))['result'],r)
    def test_github_accepts_portability_prefix(self):
        path=Path(__file__).resolve().parents[2]/'scripts/github-handoff.py'
        spec=importlib.util.spec_from_file_location('gh_portable',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.tag_for('portability-local-example'),'handoff-portability-local-example')
