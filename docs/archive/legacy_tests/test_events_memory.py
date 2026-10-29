"""Regressions for attributing cgroup pressure without erasing lifetime evidence."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


def snapshot(phase='before_seed', service='postgres', elapsed=1):
    return dict(phase=phase, service=service, elapsed_seconds=elapsed,
        captured_at='2026-10-22T00:00:00+00:00', sample_seconds=0.1,
        container_id='a'*64, started_at='2026-10-21T00:00:00Z', restart_count=0,
        cgroup_inode=1234, image_id='sha256:'+'b'*64,
        memory_bytes=2**29, memory_swap_bytes=2**30, nano_cpus=0, oom_killed=False,
        current_bytes=400000000, peak_bytes=537141248, swap_current_bytes=0, swap_peak_bytes=0,
        cgroup_limit_bytes=2**29, cgroup_swap_limit_bytes=2**29,
        events=dict(low=0, high=0, max=45963, oom=0, oom_kill=0, oom_group_kill=0),
        stat=dict(anon=100000000, file=280000000, kernel=20000000, shmem=10000000),
        pressure_us=dict(some=10000, full=1000))


class EventsMemoryTests(unittest.TestCase):
    def module(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('events_memory'), 'Missing phase diagnostic implementation')
        import events_memory
        return events_memory

    def test_historical_peak_is_preserved_without_claiming_interval_growth(self):
        m=self.module();a=snapshot();b=snapshot('after_seed',elapsed=5)
        d=m.interval(a,b)
        self.assertTrue(d['comparable'])
        self.assertEqual(d['events_delta']['max'],0)
        self.assertEqual(d['peak_growth_bytes'],0)
        self.assertEqual(d['pressure_delta_us'],{'some':0,'full':0})
        self.assertTrue(d['boundary_reached_before'])
        self.assertEqual(a['peak_bytes'],537141248)

    def test_new_pressure_oom_and_swap_are_separate_signals(self):
        m=self.module();a=snapshot();b=snapshot('after_seed',elapsed=5)
        b['events'].update(max=45966,oom=1,oom_kill=1)
        b['pressure_us'].update(some=10200,full=1050)
        b['swap_peak_bytes']=4096
        d=m.interval(a,b)
        self.assertEqual(d['events_delta']['max'],3)
        self.assertEqual(d['events_delta']['oom_kill'],1)
        self.assertEqual(d['pressure_delta_us'],{'some':200,'full':50})
        self.assertEqual(d['swap_peak_growth_bytes'],4096)

    def test_generation_change_never_subtracts_reset_counters(self):
        m=self.module();a=snapshot();b=snapshot('after_restart',elapsed=5)
        b.update(started_at='2026-10-22T00:00:01Z',cgroup_inode=5678,peak_bytes=1000000)
        b['events']['max']=0
        d=m.interval(a,b)
        self.assertFalse(d['comparable'])
        self.assertEqual(d['reason'],'generation_changed')
        self.assertIsNone(d['events_delta']);self.assertIsNone(d['peak_growth_bytes'])

    def test_counter_decrease_with_stable_identity_is_invalid(self):
        m=self.module();a=snapshot();b=snapshot('after_seed',elapsed=5)
        b['events']['max']-=1
        with self.assertRaises(ValueError):m.interval(a,b)

    def test_missing_metrics_and_unbounded_fields_are_rejected(self):
        m=self.module()
        for edit in [lambda s:s['events'].pop('oom'),lambda s:s.update(current_bytes=True),
                     lambda s:s.update(extra='untrusted'),lambda s:s.update(peak_bytes=-1),
                     lambda s:s['pressure_us'].update(some=float('nan'))]:
            s=snapshot();edit(s)
            with self.subTest(s=s),self.assertRaises(ValueError):m.validate_snapshot(s)

    def test_different_service_or_nonincreasing_time_is_invalid(self):
        m=self.module();a=snapshot()
        for b in [snapshot('after_seed',service='api',elapsed=5),snapshot('after_seed',elapsed=1)]:
            with self.assertRaises(ValueError):m.interval(a,b)

    def test_docker_nanosecond_timestamp_on_python39(self):
        m=self.module();s=snapshot()
        s['started_at']='2026-10-22T04:22:20.334799345Z'
        m.validate_snapshot(s)


def diagnostic_fixture():
    from test_events_handoff import fixture
    import events_memory as m
    report,env=fixture();result=report['result'];result['protocol']='events-memory-v1'
    env['runner_tree_sha256']='c'*64
    observations=[]
    for pi,phase in enumerate(m.PHASES):
        for si,service in enumerate(m.SERVICES):
            s=snapshot(phase,service,elapsed=pi*5+si+1)
            r=result['resources'][service]
            s.update(memory_bytes=r['memory_bytes'],cgroup_limit_bytes=r['memory_bytes'],
                     nano_cpus=r['nano_cpus'],image_id=r['image_id'],peak_bytes=1000000,current_bytes=500000)
            if service=='api' and phase=='after_restart':
                s.update(started_at='2026-10-22T00:00:01Z',cgroup_inode=5678)
            observations.append(s)
    result['diagnostics']=m.diagnostics(observations,[])
    return report,env


class EventsMemoryReportTests(unittest.TestCase):
    def ready(self):
        import events_memory as m
        self.assertTrue(hasattr(m,'diagnostics'),'Missing diagnostic envelope')
        import events_memory_handoff as h
        return m,h

    def test_valid_report_roundtrip_and_cli_opt_in(self):
        self.ready()
        from test_events_handoff import EventsHandoffTests
        import tempfile,project,events_check
        from unittest.mock import patch
        from handoff import read_report
        report,env=diagnostic_fixture()
        with tempfile.TemporaryDirectory() as d:
            path=EventsHandoffTests().package(Path(d),report,env)
            self.assertTrue(read_report(path)['result']['diagnostics']['complete'])
        args=['project.py','events-check','--machine-id','mac24','--expected-commit','a'*40,
              '--source-run-id','release-test-full','--memory-diagnostics']
        with patch.object(sys,'argv',args),patch.object(events_check,'run',return_value=0) as run:
            self.assertEqual(project.main(),0)
        self.assertTrue(run.call_args.kwargs['memory_diagnostics'])

    def test_failed_report_rejects_fabricated_deltas_or_private_fields(self):
        m,h=self.ready()
        from test_events_handoff import EventsHandoffTests
        import tempfile
        for edit in [lambda d:d['intervals'][0]['events_delta'].update(max=123),
                     lambda d:d['intervals'][0]['events_delta'].update(max=False),
                     lambda d:d['snapshots'][0].update(secret='bad'),
                     lambda d:d.update(complete=False)]:
            report,env=diagnostic_fixture();report['status']='failed';report['result'].update(status='failed',phase='final-validation',error_type='ValueError')
            report['steps'][0].update(status='failed',exit_code=1);edit(report['result']['diagnostics'])
            with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):
                EventsHandoffTests().package(Path(d),report,env)

    def test_final_gate_names_postgres_and_preserves_v1_failure(self):
        m,h=self.ready();report,env=diagnostic_fixture();r=report['result']
        r['resources']['postgres']['cgroup_peak_bytes']=537141248
        for observed in r['diagnostics']['snapshots']:
            if observed['service']=='postgres':observed['peak_bytes']=537141248
        r['diagnostics']=m.diagnostics(r['diagnostics']['snapshots'],[])
        with self.assertRaises(h.FinalValidationError) as raised:h.final_validation(report,r,env)
        self.assertEqual(raised.exception.failure,{'component':'postgres','criterion':'peak_below_limit','observed':537141248,'expected':536870912})
        self.assertEqual(r['resources']['postgres']['cgroup_peak_bytes'],537141248)

    def test_historical_high_peak_is_not_diagnostic_success(self):
        m,h=self.ready();report,env=diagnostic_fixture()
        report['result']['resources']['postgres']['cgroup_peak_bytes']=537141248
        for s in report['result']['diagnostics']['snapshots']:
            if s['service']=='postgres':s['peak_bytes']=537141248
        report['result']['diagnostics']=m.diagnostics(report['result']['diagnostics']['snapshots'],[])
        with self.assertRaises(h.FinalValidationError):h.final_validation(report,report['result'],env)

    def test_preflight_failure_still_packages_partial_diagnostic(self):
        self.ready()
        import tempfile,events_check,subprocess
        from unittest.mock import patch
        from handoff import read_report
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch.object(events_check,'read_configuration',return_value={'ARTIFACTS_DIR':'./artifacts','COMPOSE_PROJECT_NAME':'isolated','UI_PORT':'8501'}),patch.object(events_check.subprocess,'check_output',side_effect=['a'*40,'',subprocess.TimeoutExpired('docker context inspect',30)]),patch.object(events_check.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
                self.assertEqual(events_check.run(root,'test','a'*40,memory_diagnostics=True),1)
            paths=list((root/'artifacts/handoff').glob('*.zip'));self.assertEqual(len(paths),1)
            result=read_report(paths[0])['result']
            self.assertEqual(result['protocol'],'events-memory-v1')
            self.assertFalse(result['diagnostics']['complete']);self.assertEqual(result['phase'],'preflight')

    def test_post_restart_api_peak_cannot_escape_the_resource_gate(self):
        m,h=self.ready();report,env=diagnostic_fixture()
        for s in report['result']['diagnostics']['snapshots']:
            if s['service']=='api' and s['phase']=='after_restart':s['peak_bytes']=s['memory_bytes']+4096
        report['result']['diagnostics']=m.diagnostics(report['result']['diagnostics']['snapshots'],[])
        with self.assertRaises(h.FinalValidationError) as raised:h.final_validation(report,report['result'],env)
        self.assertEqual(raised.exception.failure['component'],'api')
        self.assertEqual(raised.exception.failure['criterion'],'peak_below_limit')

    def test_partial_capture_failure_is_readable_but_never_complete(self):
        m,h=self.ready()
        from test_events_handoff import EventsHandoffTests
        import tempfile
        from handoff import read_report
        report,env=diagnostic_fixture();r=report['result']
        r.update(status='failed',phase='memory-before_seed',error_type='TimeoutExpired',resources={})
        r['diagnostics']=m.diagnostics(r['diagnostics']['snapshots'][:1],[dict(phase='before_seed',service='ui',error_type='TimeoutExpired')])
        report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        with tempfile.TemporaryDirectory() as d:
            checked=read_report(EventsHandoffTests().package(Path(d),report,env))
            self.assertFalse(checked['result']['diagnostics']['complete'])
            self.assertEqual(len(checked['result']['diagnostics']['snapshots']),1)

    def test_failed_peak_report_preserves_diagnostics_in_closed_zip(self):
        m,h=self.ready()
        from test_events_handoff import EventsHandoffTests
        import tempfile
        from handoff import read_report
        report,env=diagnostic_fixture();r=report['result']
        r['resources']['postgres']['cgroup_peak_bytes']=537141248
        for s in r['diagnostics']['snapshots']:
            if s['service']=='postgres':s['peak_bytes']=537141248
        r['diagnostics']=m.diagnostics(r['diagnostics']['snapshots'],[])
        try:h.final_validation(report,r,env)
        except h.FinalValidationError as error:r['failure']=error.failure
        r.update(status='failed',phase='final-validation',error_type='FinalValidationError')
        report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        with tempfile.TemporaryDirectory() as d:
            checked=read_report(EventsHandoffTests().package(Path(d),report,env))
            self.assertEqual(checked['result']['failure']['observed'],537141248)
            self.assertTrue(checked['result']['diagnostics']['complete'])
            self.assertEqual(checked['result']['diagnostics']['intervals'][2]['events_delta']['max'],0)

    def test_failed_diagnostic_cannot_downgrade_or_omit_protocol(self):
        m,h=self.ready()
        from test_events_handoff import EventsHandoffTests
        import tempfile
        for protocol in ['events-v1',None,'unknown-v1']:
            report,env=diagnostic_fixture();r=report['result']
            report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
            r.update(status='failed',phase='final-validation',error_type='ValueError')
            if protocol is None:r.pop('protocol')
            else:r['protocol']=protocol
            r['diagnostics']['intervals'][0]['events_delta']['max']=999
            with self.subTest(protocol=protocol),tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):
                EventsHandoffTests().package(Path(d),report,env)

    def test_failed_report_cause_and_resources_must_match_observations(self):
        m,h=self.ready()
        from test_events_handoff import EventsHandoffTests
        import tempfile
        for alter_resources in (False,True):
            report,env=diagnostic_fixture();r=report['result']
            report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
            r.update(status='failed',phase='final-validation',error_type='FinalValidationError',
                failure=dict(component='postgres',criterion='peak_below_limit',observed=537141248,expected=536870912))
            if alter_resources:r['resources']['postgres']['cgroup_peak_bytes']=537141248
            with self.subTest(alter_resources=alter_resources),tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):
                EventsHandoffTests().package(Path(d),report,env)

    def test_failed_old_protocol_still_rejects_unknown_protocol_name(self):
        self.ready()
        from test_events_handoff import EventsHandoffTests,fixture
        import tempfile
        report,env=fixture();report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        report['result']=dict(stage='T5',status='failed',phase='preflight',error_type='ValueError',protocol='unknown-v1')
        with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):
            EventsHandoffTests().package(Path(d),report,env)

    def test_missing_result_is_rejected_as_invalid_instead_of_crashing_reader(self):
        self.ready()
        import tempfile
        from test_events_handoff import fixture
        from handoff import package_report,write_json
        report,env=fixture();report['status']='failed';report['steps'][0].update(status='failed',exit_code=1)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/report['run_id'];folder.mkdir()
            write_json(folder/'environment.json',env)
            (folder/'commit.txt').write_text(report['source_commit']+'\n')
            (folder/'job.log').write_text('failed\n')
            with self.assertRaises(ValueError):package_report(folder,report)
