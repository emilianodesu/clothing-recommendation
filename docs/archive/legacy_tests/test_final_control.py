import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class FinalControlTests(unittest.TestCase):
    def test_reservation_survives_failure_and_blocks_second_attempt(self):
        from final_control import reserve
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'once.json'
            reserve(p,{'source_commit':'a'*40})
            with self.assertRaises(FileExistsError):reserve(p,{'source_commit':'b'*40})
            self.assertEqual(json.loads(p.read_text())['source_commit'],'a'*40)

    def test_live_worker_without_completion_blocks_recovery(self):
        from final_control import recovery_allowed
        self.assertFalse(recovery_allowed(True,True,False))
        self.assertTrue(recovery_allowed(True,True,True))
        self.assertTrue(recovery_allowed(True,False,False))
        self.assertTrue(recovery_allowed(False,True,False))

    def test_maintenance_targets_exclude_original_database_and_api(self):
        from final_control import maintenance_targets
        rows=[dict(scope='original',service=s,container_id=s) for s in ('api','postgres','ui','mlflow','airflow-api','airflow-scheduler','airflow-dag-processor')]
        rows += [dict(scope='other',service=s,container_id='other-'+s) for s in ('api','postgres')]
        chosen=maintenance_targets(rows)
        self.assertEqual({r['container_id'] for r in chosen},{'mlflow','airflow-api','airflow-scheduler','airflow-dag-processor','other-api','other-postgres'})
        with self.assertRaises(ValueError):maintenance_targets(rows+[dict(scope='other',service='postgres',container_id='foreign')])

    def test_restore_preserves_ids_and_starts_scheduler_last(self):
        from unittest.mock import patch
        import final_control as f
        names=('airflow-scheduler','airflow-dag-processor','airflow-api','api','mlflow','postgres')
        rows=[dict(scope='other' if s in ('api','postgres') else 'original',service=s,container_id=s,image_id='image',memory_bytes=1,nano_cpus=1) for s in names]
        live=set()
        def inspect(cid):return dict({k:v for k,v in next(r for r in rows if r['container_id']==cid).items() if k not in ('scope','service')},running=cid in live,health='healthy')
        def command(args,**kwargs):live.add(args[-1])
        with patch.object(f,'inspect',side_effect=inspect),patch.object(f,'command',side_effect=command):
            result=f.restore_maintenance(rows)
        self.assertEqual([r['service'] for r in result],['postgres','api','mlflow','airflow-api','airflow-dag-processor','airflow-scheduler'])
        self.assertEqual(live,set(names))

    def test_launcher_refuses_recovery_while_real_child_might_run(self):
        from unittest.mock import patch
        import mac24_t8_final as f
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'go.json').write_text('{}')
            saved=dict(worker_name='worker',image_id='image',private=str(root))
            with patch.object(f,'command',side_effect=['','id']),patch.object(f,'inspect',return_value=dict(image_id='image',running=True)),patch.object(f,'restore_maintenance') as restore:
                with self.assertRaises(ValueError):f.recover({'aux':[]},saved)
                restore.assert_not_called()
