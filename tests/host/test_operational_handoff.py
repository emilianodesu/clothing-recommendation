"""Versioned acceptance keeps raw historical peaks and rejects dishonest evidence."""
import copy
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


def fixture():
    from test_events_memory import diagnostic_fixture
    from test_operational_timing import Clock,Guard
    from operational_timing import Monitor
    import events_memory
    report,env=diagnostic_fixture();r=report['result'];r['protocol']='events-operational-v1'
    c=Clock();c.wall=datetime(2026,10,22,tzinfo=timezone.utc).timestamp()
    begin=c.mono;m=Monitor(guard=Guard(),sample=c);m.start()
    for phase in events_memory.PHASES:
        if phase=='after_seed':m.perform('seed',1800,lambda left:c.advance(1))
        if phase=='after_replay':
            for label in ('health_live','health_ready','http_customers','http_catalog','http_recommend','http_profile','http_events','http_refresh','ui_health'):
                m.perform(label,600,lambda left:c.advance(.1))
        if phase=='after_restart':m.perform('restart',180,lambda left:c.advance(1))
        for service in events_memory.SERVICES:
            s=next(s for s in r['diagnostics']['snapshots'] if s['phase']==phase and s['service']==service)
            def capture(left):
                c.advance(.1);s['elapsed_seconds']=c.mono-begin;s['captured_at']=datetime.fromtimestamp(c.wall,timezone.utc).isoformat();s['sample_seconds']=.1
            m.perform('sample_'+phase+'_'+service,90,capture)
    m.finish();r['temporal']=m.evidence();r['duration_seconds']=r['temporal']['assessment']['monotonic_seconds']
    r['diagnostics']=events_memory.diagnostics(r['diagnostics']['snapshots'],[])
    r['t5_closed']=False
    r['cleanup']=dict(attempted=True,exit_code=0,error_type=None,utc_seconds=0.,monotonic_seconds=0.)
    report['started_at']='2026-10-22T00:00:00+00:00'
    report['finished_at']=datetime.fromtimestamp(c.wall,timezone.utc).isoformat()
    report['steps']=[dict(name='events-operational',log='job.log',status='passed',exit_code=0)]
    return report,env


class OperationalHandoffTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('operational_handoff'),'Missing operational contract')
        import operational_handoff
        return operational_handoff

    def package(self,root,report,env,log=None):
        from handoff import package_report,write_json
        folder=root/report['run_id'];folder.mkdir()
        write_json(folder/'result.json',report['result']);write_json(folder/'environment.json',env)
        (folder/'commit.txt').write_text((report['source_commit'] or 'unavailable')+'\n')
        (folder/'job.log').write_text(log or 'operational T5\nstatus: '+report['status']+'\n')
        return package_report(folder,report)

    def test_historical_peak_is_raw_diagnostic_only_in_new_version(self):
        h=self.module();report,env=fixture();r=report['result']
        import events_memory
        r['resources']['postgres']['cgroup_peak_bytes']=537141248
        for s in r['diagnostics']['snapshots']:
            if s['service']=='postgres':s['peak_bytes']=537141248
        r['diagnostics']=events_memory.diagnostics(r['diagnostics']['snapshots'],[])
        h.final_validation(report,r,env)
        from handoff import read_report
        with tempfile.TemporaryDirectory() as d:
            result=read_report(self.package(Path(d),report,env))['result']
            self.assertEqual(result['resources']['postgres']['cgroup_peak_bytes'],537141248)
            self.assertFalse(result['t5_closed'])
        from events_memory_handoff import final_validation,FinalValidationError
        old=copy.deepcopy(r);old.pop('temporal');old.pop('t5_closed');old.pop('cleanup');old['protocol']='events-memory-v1'
        with self.assertRaises(FinalValidationError):final_validation(dict(report,result=old),old,env)

    def test_swap_oom_limits_and_generation_remain_mandatory(self):
        h=self.module()
        import events_memory
        for field,value in [('swap_peak_bytes',1),('oom_killed',True),('cgroup_limit_bytes',2**30),('container_id','f'*64)]:
            report,env=fixture();r=report['result'];s=r['diagnostics']['snapshots'][5];s[field]=value
            r['diagnostics']=events_memory.diagnostics(r['diagnostics']['snapshots'],[])
            with self.subTest(field=field),self.assertRaises(ValueError):h.final_validation(report,r,env)

    def test_corrupted_incomplete_or_downgraded_trace_is_not_readable_success(self):
        self.module()
        changes=[lambda r:r['temporal']['assessment'].update(utc_seconds=999),
            lambda r:r['temporal']['marks'][2].update(utc_seconds=True),
            lambda r:r['temporal']['operations'].pop(0),
            lambda r:r['temporal']['operations'][0].update(timeout_seconds=9000),
            lambda r:r['temporal']['guard'].update(released=False),
            lambda r:r.update(protocol='events-v1'),
            lambda r:r['temporal']['host'].update(raw='PRIVATE')]
        for change in changes:
            report,env=fixture();change(report['result'])
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):self.package(Path(d),report,env)

    def test_failed_temporal_observations_are_preserved_without_becoming_success(self):
        h=self.module();report,env=fixture();r=report['result']
        from operational_timing import assessment
        r['temporal']['marks'][-1]['utc_seconds']+=900
        r['temporal']['assessment']=assessment(r['temporal']['marks']);r['temporal']['complete']=False
        report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        r.update(status='failed',phase='final-validation',error_type='OperationalFailure',failure={'component':'temporal','criterion':'continuity'})
        from handoff import read_report
        with tempfile.TemporaryDirectory() as d:
            got=read_report(self.package(Path(d),report,env))
            self.assertEqual(got['result']['temporal']['assessment']['reason'],'clock_discontinuity')
        r['failure']['criterion']='swap_zero'
        with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):self.package(Path(d),report,env)

    def test_private_attachments_and_partial_payloads_are_rejected(self):
        self.module();report,env=fixture()
        with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):self.package(Path(d),report,env,'PRIVATE token\n')
        report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        report['result'].update(status='failed',phase='seed',error_type='ValueError',customer_id='PRIVATE')
        with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):self.package(Path(d),report,env)

    def test_preflight_failure_closes_a_report_without_touching_docker(self):
        self.module();self.assertIsNotNone(importlib.util.find_spec('operational_check'))
        import operational_check
        from handoff import read_report
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch.object(operational_check,'read_configuration',return_value={'ARTIFACTS_DIR':'./artifacts','COMPOSE_PROJECT_NAME':'isolated','UI_PORT':'8501'}),patch.object(operational_check,'checkout_commit',side_effect=ValueError('dirty checkout')):
                self.assertEqual(operational_check.run(root,'test','a'*40),1)
            archives=list((root/'artifacts/handoff').glob('*.zip'));self.assertEqual(len(archives),1)
            r=read_report(archives[0]);self.assertEqual(r['status'],'failed');self.assertFalse(r['result']['temporal']['complete'])
            self.assertEqual(r['result']['phase'],'preflight')

    def test_operational_flag_dispatches_separately_and_excludes_memory_mode(self):
        self.module();import project,operational_check
        args=['project.py','events-check','--machine-id','mac24','--expected-commit','a'*40,'--source-run-id','release-full','--operational']
        with patch.object(sys,'argv',args),patch.object(operational_check,'run',return_value=17):self.assertEqual(project.main(),17)
        with patch.object(sys,'argv',args+['--memory-diagnostics']),self.assertRaises(SystemExit) as error:project.main()
        self.assertEqual(error.exception.code,2)
