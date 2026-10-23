import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class PipelineHandoffTests(unittest.TestCase):
    def test_closed_failed_pipeline_report_is_valid_but_private_fields_are_rejected(self):
        self.assertIsNotNone(importlib.util.find_spec('pipeline_handoff'))
        from pipeline_handoff import empty_result,validate
        value=empty_result();report={'run_id':'pipeline-mac24-test','source_commit':'a'*40,'source_clean':True,'status':'failed'}
        env={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'runner_tree_sha256':'c'*64}
        validate(report,value,env)
        value['customer_id']='private'
        with self.assertRaises(ValueError):validate(report,value,env)

    def test_pass_requires_freshness_and_every_control(self):
        self.assertIsNotNone(importlib.util.find_spec('pipeline_handoff'))
        from pipeline_handoff import empty_result,validate
        value=empty_result();value['status']='passed'
        report={'run_id':'pipeline-mac24-test','source_commit':'a'*40,'source_clean':True,'status':'passed'}
        env={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'runner_tree_sha256':'c'*64}
        with self.assertRaises(ValueError):validate(report,value,env)

    def test_failed_pipeline_zip_roundtrip_and_transport(self):
        from pipeline_handoff import empty_result
        from handoff import package_report,read_report,write_json
        import importlib.util
        spec=importlib.util.spec_from_file_location('github_pipeline',Path(__file__).resolve().parents[2]/'scripts/github-handoff.py');gh=importlib.util.module_from_spec(spec);spec.loader.exec_module(gh)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'pipeline-mac24-test';root.mkdir()
            result=empty_result();env={'source_commit':'a'*40,'source_tree_sha256':'b'*64,'runner_tree_sha256':'c'*64}
            report=dict(schema_version=1,kind='pipeline-handoff',run_id=root.name,machine_id='mac24',context='colima-hm-project',source_commit='a'*40,source_clean=True,started_at='2026-10-23T00:00:00+00:00',finished_at='2026-10-23T00:00:01+00:00',status='failed',warnings=[],result=result,steps=[dict(name='pipeline',log='job.log',status='failed',exit_code=1)])
            write_json(root/'result.json',result);write_json(root/'environment.json',env)
            (root/'commit.txt').write_text('a'*40+'\n');(root/'job.log').write_text('pipeline T6\nstatus: failed\n')
            archive=package_report(root,report)
            self.assertEqual(read_report(archive)['result'],result)
            self.assertEqual(gh.validate_archive(archive)['kind'],'pipeline-handoff')
            self.assertIn('pipeline',gh.marker_for(root.name))
