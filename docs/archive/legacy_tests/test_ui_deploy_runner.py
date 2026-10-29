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
import mac24_ui_deploy as d
from test_ui_recovery_control import inventory
from ui_recovery_control import select_targets,G


class Guard:
    def check(self):pass


class DeploymentFailures(unittest.TestCase):
    def fixture(self,mode):
        rows=inventory();before=copy.deepcopy(rows);aux=select_targets(rows,'original','restored-postgres')
        state=dict(run_id='run',procedure_commit='a'*40,before=before,aux=copy.deepcopy(aux),
            kept={k:v for k,v in before.items() if k in ('original-api','original-postgres','original-ui')},
            original_ui='original-ui',engine={'memory':14*G},ready={},state_signature='signature',
            image_verified=False,regression={'passed':False},deployment_started=False,deployment_verified=False,
            rollback_started=False,rollback_verified=False,maintenance_started=False,recovery_complete=False,
            ui_outcome='unchanged',historical_image='rollback',image_id='candidate',regression_name='reg',
            probe_name='probe',builder_container='builder')
        actions=[]
        def docker(*args,**kwargs):
            cid=args[-1]
            if args[0]=='stop':rows[cid]['running']=False;actions.append(('stop',cid))
            elif args[0]=='start':
                rows[cid]['running']=True;rows[cid]['started_at']='recovered';actions.append(('start',cid))
                if cid=='original-airflow-scheduler' and mode=='late_health':rows['ui-candidate']['health']='unhealthy'
                if cid=='original-airflow-scheduler' and mode=='late_generation':rows['ui-candidate']['started_at']='external-restart'
            else:raise AssertionError(args)
        def construct(s):
            actions.append(('build','candidate'))
            if mode=='build':raise ValueError('build failed')
            s['image_verified']=True;s['candidate_image']='candidate'
        def regression(s):
            if mode=='regression':raise ValueError('test failed')
            s['regression']={'passed':True,'tests':1}
        def apply(s,v,which):
            actions.append(('apply',which))
            cid=next(k for k,x in rows.items() if x['project']=='original' and x['service']=='ui')
            row=rows.pop(cid);new='ui-'+which
            row.update(container_id=new,image_id=which,started_at='new',health='healthy',running=True);rows[new]=row
            if which=='candidate' and mode in ('candidate','rollback','interrupt_rollback'):
                row['health']='unhealthy';raise ValueError('candidate failed')
            if which=='rollback' and mode=='interrupt_rollback':raise KeyboardInterrupt('SSH hangup')
            if which=='rollback' and mode=='rollback':row['health']='unhealthy';raise ValueError('rollback failed')
            return new
        def stop(s):s.update(temporary_stopped=True,temporary_cleanup_complete=True,cleanup_errors=[])
        def registry(s):
            if mode=='registry':raise ValueError('registry failed')
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(d,'STATE',Path(folder)),patch.object(d.r,'STATE',Path(folder)), \
                 patch.object(d.r,'snapshot',side_effect=lambda:copy.deepcopy(rows)), \
                 patch.object(d.r,'inspect',side_effect=lambda cid:copy.deepcopy(rows[cid])), \
                 patch.object(d.r,'healthy',side_effect=lambda cid:copy.deepcopy(rows[cid])), \
                 patch.object(d.r,'docker',side_effect=docker),patch.object(d.r,'named',return_value=None), \
                 patch.object(d.r,'prepare_public_registry',side_effect=registry),patch.object(d,'before_maintenance'), \
                 patch.object(d.r,'construct',side_effect=construct),patch.object(d,'run_regression',side_effect=regression), \
                 patch.object(d,'stop_temporaries',side_effect=stop),patch.object(d,'prepare_overrides'), \
                 patch.object(d,'apply_ui',side_effect=apply),patch.object(d,'signature',return_value='signature'), \
                 patch.object(d.r,'airflow'),patch.object(d.r,'ready',return_value={}), \
                 patch.object(d,'verify_ui',side_effect=lambda s,v,which:next(k for k,x in rows.items() if x['project']=='original' and x['service']=='ui')), \
                 contextlib.redirect_stdout(io.StringIO()):
                d.execute(state,{'COMPOSE_PROJECT_NAME':'original'},Guard())
                if mode=='restore_late_health':
                    rows['ui-candidate']['health']='unhealthy'
                    d.finish(state,{'COMPOSE_PROJECT_NAME':'original'})
                    d.write_json(Path(folder)/'summary.json',d.control.summary(state))
            report=json.loads((Path(folder)/'summary.json').read_text())
        for cid in ('original-api','original-postgres'):self.assertEqual(rows[cid],before[cid])
        self.assertTrue(all(rows[x['container_id']]['running'] for x in aux))
        return report,actions

    def test_registry_failure_does_not_start_maintenance(self):
        report,actions=self.fixture('registry')
        self.assertEqual(actions,[]);self.assertFalse(report['maintenance_started'])
        self.assertEqual(report['status'],'failed');self.assertTrue(report['recovery_complete'])

    def test_failed_build_and_regression_leave_original_ui(self):
        for mode in ('build','regression'):
            with self.subTest(mode=mode):
                report,actions=self.fixture(mode)
                self.assertFalse(any(a[0]=='apply' for a in actions))
                self.assertEqual(report['ui_outcome'],'unchanged');self.assertEqual(report['status'],'failed')
                self.assertTrue(report['recovery_complete'])

    def test_candidate_failure_rolls_back_and_recovers_auxiliaries(self):
        report,actions=self.fixture('candidate')
        self.assertEqual([a for a in actions if a[0]=='apply'],[('apply','candidate'),('apply','rollback')])
        self.assertTrue(report['rollback_verified']);self.assertTrue(report['recovery_complete'])
        self.assertEqual(report['status'],'failed');self.assertEqual(report['ui_outcome'],'rollback')

    def test_rollback_failure_still_recovers_all_auxiliaries(self):
        report,actions=self.fixture('rollback')
        self.assertFalse(report['recovery_complete']);self.assertEqual(report['stage'],'recovery_pending')
        self.assertEqual(len([a for a in actions if a[0]=='start']),6)
        self.assertEqual(report['ui_outcome'],'rollback_pending')

    def test_success_changes_only_ui_and_checks_final_preservation(self):
        report,actions=self.fixture('success')
        self.assertEqual(report['status'],'passed');self.assertEqual(report['ui_outcome'],'candidate')
        self.assertTrue(report['state_preserved']);self.assertTrue(report['final_joint_health_verified'])
        self.assertFalse(report['rollback_started'])

    def test_candidate_failure_after_auxiliary_recovery_rolls_back(self):
        for mode in ('late_health','late_generation'):
            with self.subTest(mode=mode):
                report,actions=self.fixture(mode)
                self.assertEqual([a for a in actions if a[0]=='apply'],[('apply','candidate'),('apply','rollback')])
                self.assertTrue(report['recovery_complete']);self.assertTrue(report['rollback_verified'])
                self.assertFalse(report['deployment_verified']);self.assertEqual(report['status'],'failed')

    def test_interrupted_rollback_still_attempts_all_auxiliaries(self):
        report,actions=self.fixture('interrupt_rollback')
        self.assertEqual(len([a for a in actions if a[0]=='start']),6)
        self.assertEqual(report['ui_outcome'],'rollback_pending');self.assertFalse(report['recovery_complete'])

    def test_restore_only_rechecks_candidate_without_another_build(self):
        report,actions=self.fixture('restore_late_health')
        self.assertEqual([a for a in actions if a[0]=='build'],[('build','candidate')])
        self.assertEqual([a for a in actions if a[0]=='apply'],[('apply','candidate'),('apply','rollback')])
        self.assertTrue(report['rollback_verified']);self.assertFalse(report['deployment_verified'])
        self.assertEqual(report['status'],'failed')
        self.assertTrue(report['recovery_complete'])
