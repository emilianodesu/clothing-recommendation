import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import mac24_ui_recovery as r
from test_ui_recovery_control import inventory
from ui_recovery_control import select_targets, G


class Guard:
    def check(self):pass


class RunnerFailures(unittest.TestCase):
    def run_fixture(self, mode):
        rows=inventory();aux=select_targets(rows,'original','restored-postgres')
        kept={cid:x for cid,x in rows.items() if cid in ('original-api','original-ui','original-postgres')}
        state=dict(before=copy.deepcopy(rows),aux=copy.deepcopy(aux),kept=copy.deepcopy(kept),engine={'memory':14*G},
            ready={'status':'ready'},builder_container='builder',probe_name='probe',run_id='test',
            image_verified=False,recovery_complete=False,auxiliaries_restored=False)
        stopped=[]
        def docker(*args,**kwargs):
            cid=args[-1]
            if args[0]=='stop':
                stopped.append(cid);rows[cid]['running']=False
                if mode=='stop_failure':raise RuntimeError('Interrupted stop')
            elif args[0]=='start':
                if mode=='recovery_failure' and cid=='original-mlflow':raise RuntimeError('start failed')
                rows[cid]['running']=True
            else:raise AssertionError(args)
        def construct(s):
            if mode=='build_failure':raise RuntimeError('build failed')
            s['image_verified']=True
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(r,'STATE',Path(directory)),patch.object(r,'snapshot',side_effect=lambda:copy.deepcopy(rows)),patch.object(r,'inspect',side_effect=lambda cid:copy.deepcopy(rows[cid])),patch.object(r,'docker',side_effect=docker),patch.object(r,'named',return_value=None),patch.object(r,'airflow'),patch.object(r,'ready',return_value={'status':'ready'}),patch.object(r,'construct',side_effect=construct),contextlib.redirect_stdout(io.StringIO()):
                r.execute_prepared(state,{},Guard())
            report=json.loads((Path(directory)/'summary.json').read_text())
        self.assertTrue(set(stopped)<= {a['container_id'] for a in aux})
        for cid in kept:self.assertEqual(rows[cid],kept[cid])
        return report,rows

    def test_build_failure_recovers_all_and_persists_failed_report(self):
        report,rows=self.run_fixture('build_failure')
        self.assertEqual(report['status'],'failed');self.assertTrue(report['recovery_complete'])
        self.assertTrue(all(x['running'] for x in rows.values()))
        self.assertFalse(report['image_verified'])

    def test_partial_stop_failure_recovers_and_never_builds(self):
        report,rows=self.run_fixture('stop_failure')
        self.assertEqual(report['status'],'failed');self.assertTrue(report['recovery_complete'])
        self.assertFalse(report['image_verified']);self.assertTrue(all(x['running'] for x in rows.values()))

    def test_one_restart_failure_does_not_block_later_recovery(self):
        report,rows=self.run_fixture('recovery_failure')
        self.assertEqual(report['stage'],'recovery_pending');self.assertFalse(report['recovery_complete'])
        self.assertFalse(rows['original-mlflow']['running'])
        self.assertTrue(rows['original-airflow-scheduler']['running'])
        self.assertEqual([v['role'] for v in report['restoration'] if not v['restored']],['mlflow'])

    def test_lost_ssh_stdout_does_not_interrupt_recovery(self):
        with patch('builtins.print',side_effect=BrokenPipeError('SSH closed')):
            report,rows=self.run_fixture('build_failure')
        self.assertTrue(report['recovery_complete'])
        self.assertTrue(all(x['running'] for x in rows.values()))

    def test_success_requires_image_and_recovery(self):
        report,rows=self.run_fixture('success')
        self.assertEqual(report['status'],'passed');self.assertTrue(report['image_verified'])
        self.assertTrue(report['recovery_complete'])

class CleanupFailures(unittest.TestCase):
    def test_log_capture_failure_does_not_prevent_builder_shutdown(self):
        live={'probe':True,'builder':True}
        def docker(*args,**kwargs):
            if args[0]=='stop':live[args[-1]]=False
            elif args[0]=='logs':raise RuntimeError('log unavailable')
            elif args[0]=='rm':pass
            else:raise AssertionError(args)
        state={'probe_name':'probe','builder_container':'builder','run_id':'run','builder_id':'builder','image_id':'image'}
        with patch.object(r,'named',side_effect=lambda name:name),patch.object(r,'query',return_value='{"hmrec.ui-recovery":"run"}'),patch.object(r,'inspect',side_effect=lambda cid:dict(container_id=cid,image_id='image',running=live[cid])),patch.object(r,'docker',side_effect=docker):
            try:r.stop_temporary(state)
            except RuntimeError:pass
        self.assertFalse(live['probe']);self.assertFalse(live['builder'])

    def test_removal_failure_does_not_prevent_builder_shutdown(self):
        live={'probe':True,'builder':True}
        def docker(*args,**kwargs):
            if args[0]=='stop':live[args[-1]]=False
            elif args[0]=='logs':pass
            elif args[0]=='rm':raise RuntimeError('removal failed')
            else:raise AssertionError(args)
        state={'probe_name':'probe','builder_container':'builder','run_id':'run','builder_id':'builder','image_id':'image'}
        with patch.object(r,'named',side_effect=lambda name:name),patch.object(r,'query',return_value='{"hmrec.ui-recovery":"run"}'),patch.object(r,'inspect',side_effect=lambda cid:dict(container_id=cid,image_id='image',running=live[cid])),patch.object(r,'docker',side_effect=docker):
            try:r.stop_temporary(state)
            except RuntimeError:pass
        self.assertFalse(live['probe']);self.assertFalse(live['builder'])

class BuildContextPermissions(unittest.TestCase):
    def test_checkout_files_remain_readable_for_container_uid_10001(self):
        import os
        import hashlib
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'repo';root.mkdir()
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,stderr=subprocess.DEVNULL,text=True).strip()
            git('init');git('config','commit.gpgsign','false');git('config','core.hooksPath','/dev/null');git('config','user.name','Test');git('config','user.email','test@example.invalid')
            source=root/'src/hm_recommender/ui/app.py';source.parent.mkdir(parents=True);source.write_text('print("fixture")\n')
            git('add','src');git('commit','-m','fixture');commit=git('rev-parse','HEAD')
            private=Path(directory)/'private';private.mkdir(mode=0o700)
            state=dict(builder='b',image_tag='t')
            previous=os.umask(0o077)
            try:
                with patch.object(r,'ROOT',root),patch.object(r,'STATE',private),patch.object(r,'HISTORY',commit),patch.object(r,'UI_SHA',hashlib.sha256(source.read_bytes()).hexdigest()),patch.object(r,'docker',side_effect=RuntimeError('stop before building')):
                    with self.assertRaises(RuntimeError):r.construct(state)
                checked=private/'source/src/hm_recommender/ui/app.py'
                self.assertEqual(checked.stat().st_mode & 0o004,0o004)
                self.assertEqual(checked.parent.stat().st_mode & 0o005,0o005)
                self.assertEqual(private.stat().st_mode & 0o077,0)
                actual=os.umask(0o077);self.assertEqual(actual,0o077)
            finally:os.umask(previous)

class RestoreCli(unittest.TestCase):
    def test_restore_only_can_publish_failed_attempt_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);state=dict(procedure_commit='a'*40,runner_files={},status='failed')
            (folder/'state.json').write_text(json.dumps(state))
            def recover(s,values):s['recovery_complete']=True
            def publish(s):(folder/'published.json').write_text(json.dumps(s))
            args=['runner','--restore-only','--publish','--expected-commit','a'*40]
            with patch.object(r,'STATE',folder),patch.object(r,'read_configuration',return_value={'ARTIFACTS_DIR':directory}),patch.object(r,'AwakeGuard',return_value=contextlib.nullcontext(Guard())),patch.object(r.signal,'signal'),patch.object(r,'recover',side_effect=recover),patch.object(r,'publish',side_effect=publish),patch.object(r,'construct',side_effect=AssertionError('must not build')),patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(r.main(),0)
            published=json.loads((folder/'published.json').read_text())
            self.assertEqual(published['status'],'failed');self.assertTrue(published['recovery_complete'])
