import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class PortabilityHost(unittest.TestCase):
    def test_backup_uses_configured_airflow_entrypoint_and_restores_pause_state(self):
        import json
        from unittest.mock import patch
        import backup
        calls = []
        def compose(root, values, *args, **kwargs):
            calls.append(args)
            if args[0] == 'ps':
                return 'running'
            if '-c' in args:
                program = args[args.index('-c') + 1]
                return json.dumps({'hm_build_release': True, 'hm_refresh_profiles': False}) if 'is_paused' in program else '0'
            if args[0] == 'run':
                raise RuntimeError('simulated backup failure')
            return ''
        with tempfile.TemporaryDirectory() as d:
            with patch.object(backup, 'read_configuration', return_value={'ARTIFACTS_DIR': d}), patch.object(backup, 'compose', side_effect=compose):
                with self.assertRaisesRegex(RuntimeError, 'simulated backup failure'):
                    backup.create(Path(d))
        dag_calls = [args for args in calls if 'dags' in args]
        prefix = ('exec', '-T', 'airflow-scheduler', '/opt/airflow-env/bin/python', '/opt/airflow/entrypoint.py', 'dags')
        self.assertEqual(dag_calls, [prefix + ('pause', 'hm_build_release'), prefix + ('pause', 'hm_refresh_profiles'), prefix + ('unpause', 'hm_refresh_profiles')])

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('restore'),'restore launcher missing')
        import restore
        return restore
    def test_destination_existing_rejected_before_writes(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'sentinel').write_text('keep')
            with self.assertRaises(ValueError):m.prepare_destination(Path(__file__).resolve().parents[2],p,18009)
            self.assertEqual((p/'sentinel').read_text(),'keep')
    def test_no_secrets_or_runtime_copied(self):
        m=self.module();root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'new user space';v=m.prepare_destination(root,p,18009)
            self.assertTrue((p/'compose.yaml').is_file())
            self.assertEqual(v['API_PORT'],'18009')
            self.assertFalse((p/'artifacts/serving').exists())
            self.assertFalse((p/'.git').exists())
            self.assertEqual((p/'.env').stat().st_mode&0o777,0o600)
    def test_restored_source_can_prepare_another_installation(self):
        m=self.module();root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            a,b=Path(d)/'first',Path(d)/'second'
            one=m.prepare_destination(root,a,18009)
            two=m.prepare_destination(a,b,18010)
            self.assertNotEqual(one['HM_APP_PASSWORD'],two['HM_APP_PASSWORD'])
            self.assertEqual((a/'src/hm_recommender/portability.py').read_bytes(),(b/'src/hm_recommender/portability.py').read_bytes())
    def test_network_reservation_handles_null_ipam(self):
        from unittest.mock import patch
        m=self.module()
        with patch.object(m.subprocess,'check_output',side_effect=['one','[{"IPAM":{"Config":null}}]']),patch.object(m.subprocess,'run') as run:
            chosen=m.reserve_networks({'COMPOSE_PROJECT_NAME':'hmrec_example'})
            self.assertEqual(len(set(chosen.values())),2)
            self.assertEqual(run.call_count,2)
    def test_copy_rechecks_expected_hash(self):
        m=self.module()
        self.assertTrue(hasattr(m,'copy_verified'))
        with tempfile.TemporaryDirectory() as d:
            a,b=Path(d)/'a',Path(d)/'b';a.write_bytes(b'changed')
            with self.assertRaises(ValueError):m.copy_verified(a,b,'0'*64)
