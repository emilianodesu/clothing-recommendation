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


class PipelineReviewTests(unittest.TestCase):
    def test_build_proof_rejects_unrelated_run_and_wrong_hm_source(self):
        from pipeline_handoff import build_proof
        import hashlib
        cfg=dict(data_as_of='2020-09-09',synthetic=False,activate=False)
        raw=json.dumps(cfg).encode();sha=hashlib.sha256(raw).hexdigest()
        frozen=json.loads((Path(__file__).resolve().parents[2]/'configs/releases/history-v1.json').read_text())
        manifest=dict(commit='a'*40,release_id='h-'+'b'*64,data_as_of='2020-09-09',source_sha256=frozen['source_sha256'],dataset_manifest_sha256=frozen['dataset_manifest_sha256'],selection_archive_sha256=frozen['selection']['archive_sha256'],selection_archive_verified=True)
        receipt=dict(config_sha256=sha,release_id=manifest['release_id'],orchestrator_run_id='manual__test',manifest_sha256='c'*64)
        run=['manual__test','success','2026-10-23T01:00:00+00:00','2026-10-23T01:01:00+00:00']
        self.assertEqual(build_proof(raw,receipt,manifest,'c'*64,run,'a'*40,True,frozen)['run_id'],'manual__test')
        for change in ('run','source','synthetic','cutoff','manifest'):
            import copy
            c=copy.deepcopy(cfg);r=copy.deepcopy(receipt);m=copy.deepcopy(manifest);d=run[:]
            if change=='run':d[0]='manual__other'
            if change=='source':m['source_sha256']='d'*64
            if change=='synthetic':c['synthetic']=True
            if change=='cutoff':m['data_as_of']='2020-09-16'
            if change=='manifest':r['manifest_sha256']='e'*64
            encoded=json.dumps(c).encode();r['config_sha256']=hashlib.sha256(encoded).hexdigest()
            with self.assertRaises(ValueError):build_proof(encoded,r,m,'c'*64,d,'a'*40,True,frozen)

    def test_generation_gap_and_old_installed_code_are_rejected(self):
        from pipeline_handoff import validate_resources,validate_code,SERVICES
        samples=[]
        for phase in ('before','pre_restart','post_restart','after'):
            for n,(memory,cpu) in SERVICES.items():
                generation=2 if n=='airflow-scheduler' and phase in ('post_restart','after') else 1
                samples.append(dict(service=n,phase=phase,container_id='a'*64,image_id='sha256:'+'b'*64,started_at='2026-10-23T00:00:0%dZ'%generation,restart_count=0,cgroup_inode=generation,memory_bytes=memory,nano_cpus=cpu,peak_bytes=100,swap_peak_bytes=0,oom=0,oom_kill=0,max_events=0))
        validate_resources(samples)
        with self.assertRaises(ValueError):validate_resources([s for s in samples if s['phase']!='pre_restart'])
        samples[6]['swap_peak_bytes']=1
        with self.assertRaises(ValueError):validate_resources(samples)
        installed={n:dict(source_tree_sha256='a'*64,dag_tree_sha256='b'*64) for n in ('airflow-scheduler','airflow-dag-processor','airflow-api')}
        validate_code(installed,'a'*64,'b'*64)
        installed['airflow-scheduler']['source_tree_sha256']='c'*64
        with self.assertRaises(ValueError):validate_code(installed,'a'*64,'b'*64)
