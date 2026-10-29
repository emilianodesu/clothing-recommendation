"""Publishing a completed old run never enters configuration/Docker/load paths."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import mac24_h_cycle as h


class PublicationContinuation(unittest.TestCase):
    def fixture(self,root):
        def git(*args):
            return subprocess.check_output(['git','-c','commit.gpgsign=false',*args],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        git('init','-q');git('config','user.email','fixture@example.invalid');git('config','user.name','Fixture')
        (root/'.gitignore').write_text('.runtime/\nartifacts/\n')
        git('add','.gitignore');git('commit','-qm','original runner');old=git('rev-parse','HEAD')
        git('commit','--allow-empty','-qm','publication fix');new=git('rev-parse','HEAD')
        folder=root/'artifacts/handoff/h-cycle-mac24-fixture';folder.mkdir(parents=True)
        report=dict(protocol='h-cycle-v1',run_id=folder.name,source_commit=old,status='passed')
        for name in h.FILES:(folder/name).write_text(json.dumps(report if name=='report.json' else {}))
        archive=h.close_archive(folder)
        state=dict(report,archive=str(archive),archive_sha256=h.sha(archive))
        state_path=root/'.runtime/h-cycle-v1/state.json';state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps(state))
        return old,new,state_path,archive

    def test_new_checkout_publishes_old_report_without_env_docker_or_state_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);old,new,state_path,archive=self.fixture(root)
            original_state=state_path.read_bytes();original_zip=archive.read_bytes();commands=[]
            def command(args,**kwargs):
                commands.append(args)
                if args[:3]==['gh','repo','view']:return json.dumps(dict(nameWithOwner=h.REPO,isPrivate=False))
                if args[:3]==['gh','release','create']:return ''
                raise AssertionError(args)
            real_run=subprocess.run
            def run(args,**kwargs):
                if args[:2]==['gh','api']:return subprocess.CompletedProcess(args,1,'','gh: Not Found (HTTP 404)')
                return real_run(args,**kwargs)
            with patch.object(h,'ROOT',root),patch.object(h,'STATE',state_path.parent),patch.object(sys,'argv',['runner','--expected-commit',new,'--publish-only']),patch.object(h,'read_configuration',side_effect=AssertionError('Publication must not need .env')),patch.object(h,'AwakeGuard',side_effect=AssertionError('No execution guard needed')),patch.object(h,'execute',side_effect=AssertionError('Do not run load')),patch.object(h.r,'run_command',side_effect=command),patch('subprocess.run',side_effect=run),patch.object(h.r,'emit'):
                self.assertEqual(h.main(),0)
            self.assertEqual(archive.read_bytes(),original_zip);self.assertEqual(state_path.read_bytes(),original_state)
            create=commands[-1];self.assertEqual(create[create.index('--target')+1],old)

    def test_report_origin_mismatch_is_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);old,new,state_path,archive=self.fixture(root)
            state=json.loads(state_path.read_text());state['source_commit']=new
            state_path.write_text(json.dumps(state))
            with patch.object(h,'ROOT',root),patch.object(h,'STATE',state_path.parent),patch.object(h,'publish') as publish:
                self.assertTrue(hasattr(h,'publish_saved'))
                with self.assertRaises(ValueError):h.publish_saved(new)
                publish.assert_not_called()
