import copy
import json
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import mac24_h_cycle as h
from test_ui_recovery_control import inventory


class CycleLifecycle(unittest.TestCase):
    def execution(self,mode):
        rows=inventory();before=copy.deepcopy(rows)
        aux=[copy.deepcopy(before[cid]) for cid in ('restored-api','original-mlflow','restored-postgres')]
        kept={cid:row for cid,row in before.items() if cid not in {a['container_id'] for a in aux}}
        state=dict(protocol='h-cycle-v1',run_id='h-cycle-mac24-test',source_commit='a'*40,
            runner_tree_sha256='b'*64,engine={'memory':14*h.G},admitted_bytes=7*h.G,
            baseline_sha256='b'*64,historical_t8b_sha256='b'*64,ui_report_sha256='b'*64,
            cases_sha256='b'*64,state_before_sha256='b'*64,protected={},ready={},
            worker_started=False,auxiliaries_restored=False,before=before,aux=aux,kept=kept)
        stops=[]
        def docker(*args,**kwargs):
            cid=args[-1]
            if args[0]=='stop':
                stops.append(cid);rows[cid]['running']=False
                if mode=='stop_failure':raise RuntimeError('connection lost during stop')
            elif args[0]=='start':rows[cid]['running']=True
            else:raise AssertionError(args)
        def measure(state,values,cases,art,report,*_):
            if mode=='measure_failure':raise KeyboardInterrupt('SSH disconnected')
            report.update(resources_ok=True,worker_ok=True,measurement=dict(error_fraction=0.,latency_seconds={'p95':.01}),
                cycle=None)
        def signature(state):
            if mode=='changed_during_recovery' and stops and all(row['running'] for row in rows.values()):return 'c'*64
            return 'b'*64
        with tempfile.TemporaryDirectory() as d,ExitStack() as stack:
            art=Path(d);(art/'handoff').mkdir()
            stack.enter_context(patch.object(h,'STATE',art/'state'));h.reserve(state)
            for name,value in [('signature',signature),('check_airflow',lambda *a:None),
                               ('restored_ready',lambda:None),('measure',measure)]:
                stack.enter_context(patch.object(h,name,side_effect=value))
            for name,value in [('snapshot',lambda:copy.deepcopy(rows)),('inspect',lambda cid:copy.deepcopy(rows[cid])),
                               ('healthy',lambda cid:copy.deepcopy(rows[cid])),('docker',docker),
                               ('ready',lambda v:{}),('airflow',lambda v:None),('emit',lambda m:None)]:
                stack.enter_context(patch.object(h.r,name,side_effect=value))
            class Guard:
                def check(self):pass
            report=h.execute(state,{},[],art,Guard())
            self.assertTrue(all(row['running'] for row in rows.values()))
            self.assertTrue(set(stops)<={row['container_id'] for row in aux})
            for cid in kept:self.assertEqual(rows[cid],before[cid])
            with zipfile.ZipFile(state['archive']) as z:
                self.assertEqual(set(z.namelist()),h.FILES)
                self.assertNotIn(str(art),z.read('report.json').decode())
            return report

    def test_interrupted_measurement_recovers_and_exports_failed_aggregate(self):
        report=self.execution('measure_failure')
        self.assertEqual(report['status'],'failed');self.assertTrue(report['auxiliaries_restored'])

    def test_partial_stop_does_not_leave_originals_or_auxiliaries_stopped(self):
        report=self.execution('stop_failure')
        self.assertEqual(report['status'],'failed');self.assertTrue(report['auxiliaries_restored'])

    def test_inconclusive_observation_still_recovers_and_preserves_old_failure(self):
        report=self.execution('success')
        self.assertEqual(report['status'],'inconclusive')
        self.assertEqual(report['historical_t8b_status'],'failed')
        self.assertFalse(report['sustained_concurrency_proven'])

    def test_state_is_checked_again_after_auxiliaries_recover(self):
        report=self.execution('changed_during_recovery')
        self.assertEqual(report['status'],'failed')
        self.assertFalse(report['source_preserved'])
        self.assertTrue(report['auxiliaries_restored'])

    def test_exclusive_reservation_keeps_first_state(self):
        with tempfile.TemporaryDirectory() as d,patch.object(h,'STATE',Path(d)/'state'):
            h.reserve({'run_id':'first'})
            with self.assertRaises(FileExistsError):h.reserve({'run_id':'second'})
            self.assertEqual(json.loads((h.STATE/'state.json').read_text())['run_id'],'first')

    def test_worker_without_exit_prevents_restore(self):
        with tempfile.TemporaryDirectory() as d:
            state=dict(worker_started=True,worker_output=str(Path(d)/'worker.json'))
            with patch.object(h,'restore_services') as restore:
                with self.assertRaises(ValueError):h.recover(state,{})
                restore.assert_not_called()

    def test_inventory_keeps_new_ui_generation_and_original_services(self):
        before=inventory();after=copy.deepcopy(before)
        aux=[before[cid] for cid in ('original-mlflow','restored-api','restored-postgres')]
        after['restored-api']['started_at']='later'
        h.verify_after(before,after,aux)
        after['original-ui']['image_id']='different'
        with self.assertRaises(ValueError):h.verify_after(before,after,aux)

    def test_recovery_attempts_all_auxiliaries_and_checks_final_inventory(self):
        rows=inventory();before=copy.deepcopy(rows)
        aux=[before[cid] for cid in ('restored-api','restored-postgres','original-mlflow')]
        for row in aux:rows[row['container_id']]['running']=False
        state=dict(worker_started=False,aux=aux,before=before)
        attempted=[]
        def docker(*args,**kwargs):
            self.assertEqual(args[0],'start');cid=args[-1];attempted.append(cid)
            if cid=='restored-postgres':raise RuntimeError('database failed')
            rows[cid]['running']=True
        with patch.object(h.r,'inspect',side_effect=lambda cid:rows[cid]),patch.object(h.r,'docker',side_effect=docker),patch.object(h.r,'healthy',side_effect=lambda cid:rows[cid]),patch.object(h.r,'snapshot',side_effect=lambda:rows),patch.object(h,'save'):
            with self.assertRaises(ValueError):h.recover(state,{})
        self.assertEqual(attempted,['restored-postgres','restored-api','original-mlflow'])
        self.assertFalse(state['auxiliaries_restored'])

    def test_closed_archive_can_only_include_aggregate_files(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'report.json').write_text('{}')
            (folder/'resources.json').write_text('[]');(folder/'trace.json').write_text('{}')
            (folder/'worker.json').write_text('{}');(folder/'cases.json').write_text('private')
            with self.assertRaises(ValueError):h.close_archive(folder)

    def test_first_publication_checks_missing_release_and_does_not_run_work(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'report.zip';archive.write_bytes(b'aggregate zip')
            archive.with_suffix('.zip.sha256').write_text(h.sha(archive)+'  report.zip\n')
            state=dict(archive=str(archive),archive_sha256=h.sha(archive),run_id='h-cycle-mac24-test',source_commit='a'*40)
            commands=[]
            def command(args,**kwargs):
                commands.append(args)
                if args[:3]==['gh','repo','view']:return json.dumps(dict(nameWithOwner=h.REPO,isPrivate=False))
                if args[:3]==['gh','release','create']:return ''
                raise AssertionError(args)
            missing=subprocess.CompletedProcess([],1,'','gh: Not Found (HTTP 404)')
            with patch.object(h.r,'run_command',side_effect=command),patch('subprocess.run',return_value=missing),patch.object(h.r,'emit'):
                h.publish(state)
            self.assertEqual(commands[-1][:3],['gh','release','create'])

    def test_partial_publication_uploads_only_missing_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'report.zip';archive.write_bytes(b'zip')
            sidecar=archive.with_suffix('.zip.sha256');sidecar.write_text(h.sha(archive)+'  report.zip\n')
            state=dict(archive=str(archive),archive_sha256=h.sha(archive),run_id='h-cycle-mac24-test')
            existing=subprocess.CompletedProcess([],0,json.dumps(dict(assets=[dict(name='report.zip',digest='sha256:'+h.sha(archive),size=3)])),'')
            commands=[]
            def command(args,**kwargs):
                commands.append(args)
                if args[:3]==['gh','repo','view']:return json.dumps(dict(nameWithOwner=h.REPO,isPrivate=True))
                return ''
            with patch.object(h.r,'run_command',side_effect=command),patch('subprocess.run',return_value=existing),patch.object(h.r,'emit'):
                h.publish(state)
            upload=commands[-1]
            self.assertEqual(upload[:3],['gh','release','upload'])
            self.assertIn(str(sidecar),upload);self.assertNotIn(str(archive),upload)
            self.assertNotIn('--clobber',upload)

    def test_auth_failure_must_not_be_treated_as_missing_release(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'report.zip';archive.write_bytes(b'zip')
            archive.with_suffix('.zip.sha256').write_text(h.sha(archive)+'  report.zip\n')
            state=dict(archive=str(archive),archive_sha256=h.sha(archive),run_id='h-cycle-mac24-test')
            denied=subprocess.CompletedProcess([],1,'','Bad credentials (HTTP 401)')
            with patch.object(h.r,'run_command',return_value=json.dumps(dict(nameWithOwner=h.REPO,isPrivate=True))),patch('subprocess.run',return_value=denied):
                with self.assertRaises(ValueError):h.publish(state)
