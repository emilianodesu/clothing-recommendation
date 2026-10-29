import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class FinalHandoffTests(unittest.TestCase):
    def test_failed_empty_report_valid_but_private_keys_rejected(self):
        from final_handoff import validate,empty_result
        r=empty_result(); env={k:'a'*64 for k in ('runner_tree_sha256','worker_source_sha256','image_sha256','dataset_manifest_sha256','source_sha256','selection_archive_sha256','baseline_archive_sha256','concurrency_archive_sha256','state_before_sha256','state_after_sha256')}
        env['source_commit']='b'*40
        report=dict(status='failed',source_commit='b'*40)
        validate(report,r,env,[])
        bad=copy.deepcopy(r);bad['customer_id']='private'
        with self.assertRaises(ValueError):validate(report,bad,env,[])
        report['status']='passed';r['status']='passed'
        with self.assertRaises(ValueError):validate(report,r,env,[])

    def test_failed_package_roundtrip_and_github_allowlist(self):
        import tempfile
        import importlib.util
        from final_handoff import empty_result
        from handoff import package_report,read_report,write_json
        r=empty_result();env={k:'a'*64 for k in ('runner_tree_sha256','worker_source_sha256','image_sha256','dataset_manifest_sha256','source_sha256','selection_archive_sha256','baseline_archive_sha256','concurrency_archive_sha256','state_before_sha256','state_after_sha256')};env['source_commit']='b'*40
        report=dict(schema_version=1,kind='final-handoff',run_id='final-mac24-fixture',machine_id='fixture',context='fixture',source_commit='b'*40,source_clean=True,status='failed',started_at='x',finished_at='x',warnings=[],steps=[dict(name='final',log='job.log',status='failed',exit_code=1),dict(name='resources',log='resources.log',status='failed',exit_code=1)],result=r)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/report['run_id'];root.mkdir()
            for name,value in [('result.json',r),('environment.json',env),('resources.log',[])]:write_json(root/name,value)
            (root/'commit.txt').write_text('b'*40+'\n');(root/'job.log').write_text('final T8-C\nstatus: failed\n')
            archive=package_report(root,report)
            self.assertEqual(read_report(archive)['result'],r)
            spec=importlib.util.spec_from_file_location('gh_final',Path(__file__).resolve().parents[2]/'scripts/github-handoff.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            self.assertEqual(mod.validate_archive(archive)['kind'],'final-handoff')
