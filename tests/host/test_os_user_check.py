import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import os_user_check as m

class OtherUserTests(unittest.TestCase):
    def test_requires_different_nonroot_user_and_own_home(self):
        with tempfile.TemporaryDirectory(prefix='new user ') as d:
            home=Path(d).resolve();root=home/'Project space';root.mkdir()
            m.check_identity(root,home,502,501,502)
            for uid,original,owner in [(0,501,0),(501,501,501),(502,501,501)]:
                with self.assertRaises(ValueError):m.check_identity(root,home,uid,original,owner)
            with self.assertRaises(ValueError):m.check_identity(Path('/tmp/outside'),home,502,501,502)
    def test_only_own_socket_and_empty_engine_are_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)
            endpoint='unix://'+str(home/'.colima/hm-portability-user/docker.sock')
            m.check_engine(endpoint,home,{'Architecture':'aarch64','NCPU':3,'MemTotal':6*2**30},False)
            for bad,busy in [('unix:///Users/original/.colima/hm-project/docker.sock',False),(endpoint,True),('tcp://localhost:2375',False)]:
                with self.assertRaises(ValueError):m.check_engine(bad,home,{'Architecture':'aarch64','NCPU':3,'MemTotal':6*2**30},busy)
    def test_closed_report_is_aggregate_and_repeat_is_rejected(self):
        import contextlib
        import json
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        from handoff import read_report
        with tempfile.TemporaryDirectory(prefix='other user ') as d:
            home=Path(d).resolve();root=home/'Project space';root.mkdir()
            engine={'Architecture':'aarch64','NCPU':3,'MemTotal':6*2**30}
            endpoint='unix://'+str(home/'.colima/hm-portability-user/docker.sock')
            with contextlib.ExitStack() as stack:
                for target,value in [('ROOT',root),('AwakeGuard',contextlib.nullcontext)]:stack.enter_context(patch.object(m,target,value))
                stack.enter_context(patch.dict(os.environ,{'HOME':str(home),'DOCKER_CONFIG':str(home/'.docker')}))
                stack.enter_context(patch.object(sys,'argv',['os_user_check.py','--expected-commit','a'*40]))
                stack.enter_context(patch.object(m.platform,'system',return_value='Darwin'))
                stack.enter_context(patch.object(m.platform,'machine',return_value='arm64'))
                stack.enter_context(patch.object(m.pwd,'getpwuid',return_value=SimpleNamespace(pw_dir=str(home))))
                stack.enter_context(patch.object(m.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid()+1)))
                stack.enter_context(patch.object(m,'checkout_commit'))
                stack.enter_context(patch.object(m,'prepare_configuration',return_value={}))
                stack.enter_context(patch.object(m,'compose',return_value=''))
                stack.enter_context(patch.object(m.subprocess,'check_output',side_effect=['AC Power',endpoint,json.dumps(engine),'','']))
                run=stack.enter_context(patch.object(m.subprocess,'run'))
                self.assertEqual(m.main(),0)
                report=read_report(next((root/'artifacts/handoff').glob('*.zip')))
                self.assertEqual(report['status'],'passed')
                self.assertEqual([s['name'] for s in report['steps']],['build','postgres','verify-portability'])
                self.assertEqual(run.call_args_list[-1].args[0][2],'upload')
                with self.assertRaises(ValueError):m.main()
    def test_socket_symlink_cannot_escape_home(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);home=root/'user';outside=root/'other';home.mkdir();outside.mkdir()
            (home/'.colima').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError):m.check_socket('unix://'+str(home/'.colima/hm-portability-user/docker.sock'),home)
