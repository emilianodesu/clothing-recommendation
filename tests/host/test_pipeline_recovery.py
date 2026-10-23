from pathlib import Path
import copy,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class RecoveryTests(unittest.TestCase):
    def test_only_approved_generation_and_aggregates_can_bridge(self):
        from pipeline_recovery import APPROVED, approve, validate_proof
        p=copy.deepcopy(APPROVED)
        self.assertEqual(approve(p['previous_identity'],p['current_identity'],p['aggregates'],p['source_run_id'],p['source_archive_sha256']),p)
        validate_proof(p)
        for key in ('container_id','started_at','image_id','restart_count'):
            after=copy.deepcopy(p['current_identity']);after[key]=1 if key=='restart_count' else 'different'
            with self.assertRaises(ValueError):approve(p['previous_identity'],after,p['aggregates'],p['source_run_id'],p['source_archive_sha256'])
        for key in p['aggregates']:
            counts=copy.deepcopy(p['aggregates']);counts[key]='different'
            with self.assertRaises(ValueError):approve(p['previous_identity'],p['current_identity'],counts,p['source_run_id'],p['source_archive_sha256'])
        for key in ('source_run_id','source_archive_sha256'):
            changed=copy.deepcopy(p);changed[key]='different'
            with self.assertRaises(ValueError):validate_proof(changed)

    def test_extra_private_fields_are_rejected(self):
        from pipeline_recovery import APPROVED,validate_proof
        p=copy.deepcopy(APPROVED);p['customer_id']='private'
        with self.assertRaises(ValueError):validate_proof(p)

    def test_v1_remains_readable_and_v2_requires_transition_shape(self):
        from pipeline_handoff import empty_result,validate
        report=dict(run_id='pipeline-mac24-fixture',source_commit='a'*40,source_clean=True,status='failed')
        env=dict(source_commit='a'*40,source_tree_sha256='b'*64,runner_tree_sha256='c'*64)
        validate(report,empty_result(),env)
        v=empty_result('pipeline-v2');validate(report,v,env)
        v['postgres_transition']={'ignore_restart':True}
        with self.assertRaises(ValueError):validate(report,v,env)

    def test_v2_zip_preserves_transition_and_v1_reader_contract(self):
        import tempfile,json
        from pipeline_handoff import empty_result
        from pipeline_recovery import APPROVED
        from handoff import package_report,read_report,write_json
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'pipeline-mac24-recovery';folder.mkdir()
            value=empty_result('pipeline-v2');value.update(dataset='hm',postgres_transition=copy.deepcopy(APPROVED),source_run_id=APPROVED['source_run_id'],source_archive_sha256=APPROVED['source_archive_sha256'])
            env=dict(source_commit='a'*40,source_tree_sha256='b'*64,runner_tree_sha256='c'*64)
            report=dict(schema_version=1,kind='pipeline-handoff',run_id=folder.name,machine_id='mac24',context='colima-hm-project',source_commit='a'*40,source_clean=True,started_at='2026-10-23T20:00:00+00:00',finished_at='2026-10-23T20:01:00+00:00',status='failed',warnings=[],result=value,steps=[dict(name='pipeline',log='job.log',status='failed',exit_code=1)])
            write_json(folder/'result.json',value);write_json(folder/'environment.json',env)
            (folder/'commit.txt').write_text('a'*40+'\n');(folder/'job.log').write_text('pipeline T6\nstatus: failed\n')
            self.assertEqual(read_report(package_report(folder,report))['result']['postgres_transition'],APPROVED)

    def test_wrong_generation_stops_before_any_database_command(self):
        from pipeline_recovery import APPROVED,check
        from unittest.mock import Mock
        archive=Mock();archive.read_bytes.return_value=b'wrong archive'
        execute=Mock()
        with self.assertRaises(ValueError):check(Path('.'),{},dict(run_id=APPROVED['source_run_id']),archive,APPROVED['previous_identity'],dict(APPROVED['current_identity'],started_at='different'),execute=execute)
        execute.assert_not_called()
        self.assertIsNone(check(Path('.'),{},None,None,APPROVED['previous_identity'],APPROVED['previous_identity'],execute=execute))

    def test_passed_v2_cannot_drop_recovery_proof(self):
        import json
        from pipeline_recovery import APPROVED
        from pipeline_handoff import validate,FROZEN_SOURCE,FROZEN_DATASET
        fixture=json.loads((Path(__file__).resolve().parents[2]/'evidence/pipeline-local.json').read_text())
        v=fixture['result'];v.update(protocol='pipeline-v2',dataset='hm',source_run_id=APPROVED['source_run_id'],source_archive_sha256=APPROVED['source_archive_sha256'],postgres_transition=copy.deepcopy(APPROVED))
        v['build'].update(source_sha256=FROZEN_SOURCE,dataset_manifest_sha256=FROZEN_DATASET,synthetic=False,activate=False,selection_archive_verified=True)
        for sample in v['resources']:
            if sample['service']=='postgres':sample.update(APPROVED['current_identity'])
        report=dict(run_id=fixture['run_id'],source_commit=fixture['source_commit'],source_clean=True,status='passed')
        env=dict(source_commit=fixture['source_commit'],source_tree_sha256=v['installed_code']['airflow-api']['source_tree_sha256'],runner_tree_sha256='c'*64)
        validate(report,v,env)
        v['postgres_transition']=None
        with self.assertRaises(ValueError):validate(report,v,env)
