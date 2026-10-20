import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


class QualityBudgetTests(unittest.TestCase):
    def test_summary_declaration_requires_unique_sources_and_explicit_availability(self):
        from quality_summary import validate_declaration
        from copy import deepcopy
        campaign={'schema_version':1,'protocol':'quality-v1','size':'full',
            'folds':{f:{'Q1':'quality-'+f,'Q2':None,'RH':None,'RG':None} for f in ('V1','V2','V3')}}
        validate_declaration(campaign)
        for mutation in ('duplicate','missing','pilot','orphan'):
            bad=deepcopy(campaign)
            if mutation=='duplicate':bad['folds']['V2']['Q1']='quality-V1'
            elif mutation=='missing':del bad['folds']['V1']['RG']
            elif mutation=='pilot':bad['size']='pilot'
            else:bad['folds']['V1']['RH']='quality-rh'
            with self.assertRaises(ValueError):validate_declaration(bad)

    def test_summary_dispatches_separate_budget_and_requires_campaign(self):
        from quality_job import main
        from unittest.mock import patch
        args=['summarize','--campaign','campaign.json','--expected-commit','a'*40]
        with patch('quality_job.start') as start:
            self.assertEqual(main(args),0)
            self.assertEqual(start.call_args.kwargs['quality_options'],
                {'stage':'summary','size':'summary','campaign_file':'campaign.json'})
        from quality_budget import reserve,settle
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(reserve(Path(tmp),'quality-summary','summary'),1800)
            settle(Path(tmp),'quality-summary',1800)
            with self.assertRaises(ValueError):reserve(Path(tmp),'quality-summary-again','summary')

    def test_budget_counts_failures_and_cannot_reset_by_run_id(self):
        from quality_budget import reserve,settle
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            first=reserve(root,'quality-one','pilot')
            self.assertEqual(first,7200)
            with self.assertRaises(ValueError): reserve(root,'quality-two','pilot')
            settle(root,'quality-one',7199)
            self.assertEqual(reserve(root,'quality-two','pilot'),1)
            settle(root,'quality-two',1)
            with self.assertRaises(ValueError): reserve(root,'quality-three','pilot')
            with self.assertRaises(ValueError): reserve(root,'quality-one','pilot')

    def test_corrupt_budget_fails_closed(self):
        from quality_budget import reserve
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/'quality-v1.json').write_text('{}')
            with self.assertRaises(ValueError): reserve(root,'quality-a','full')

    def test_quality_cli_rejects_unimplemented_stage_before_docker(self):
        from quality_job import main
        self.assertEqual(main(['start','--stage','ranker','--fold','V3','--size','demo','--config','quality-v1','--expected-commit','a'*40]),1)

    def test_abandoned_launcher_recovers_reservation_and_charges_unknown_runtime(self):
        from quality_budget import reserve
        from data_job import settle_quality
        with tempfile.TemporaryDirectory() as tmp:
            artifacts=Path(tmp);directory=artifacts/'data-jobs/quality-abandoned';directory.mkdir(parents=True)
            reserve(artifacts/'quality-budget',directory.name,'pilot')
            run={'run_id':directory.name,'quality':{'stage':'baselines'},'launch_status':'running'}
            settle_quality(directory,run,{'Status':'not-created','Running':False})
            self.assertEqual(run['quality_budget']['pilot'],7200)
            self.assertEqual(run['quality_budget_seconds'],7200)

    def test_completed_container_budget_uses_docker_times(self):
        from quality_budget import reserve
        from data_job import settle_quality
        with tempfile.TemporaryDirectory() as tmp:
            artifacts=Path(tmp);directory=artifacts/'data-jobs/quality-complete';directory.mkdir(parents=True)
            allowance=reserve(artifacts/'quality-budget',directory.name,'full')
            run={'run_id':directory.name,'quality':{'stage':'baselines'},'quality_budget_seconds':allowance}
            settle_quality(directory,run,{'Status':'exited','Running':False,'StartedAt':'2026-10-20T10:00:00.000000000Z','FinishedAt':'2026-10-20T10:01:30.500000000Z'})
            self.assertEqual(run['quality_budget']['full'],90.5)

    def test_graph_cli_requires_reference_and_dispatches_stage(self):
        from quality_job import main
        from unittest.mock import patch
        args=['start','--stage','graph','--fold','V1','--size','full','--expected-commit','a'*40]
        with patch('quality_job.start') as start:
            self.assertEqual(main(args),1);start.assert_not_called()
            self.assertEqual(main(args+['--reference-run-id','quality-q1']),0)
            self.assertEqual(start.call_args.kwargs['quality_options']['stage'],'graph')

    def test_ranker_requires_pool_and_Q2_reference_and_dispatches(self):
        from quality_job import main
        from unittest.mock import patch
        args=['start','--stage','ranker','--fold','V1','--size','full','--expected-commit','a'*40]
        with patch('quality_job.start') as start:
            self.assertEqual(main(args),1)
            self.assertEqual(main(args+['--pool','H']),1)
            self.assertEqual(main(args+['--pool','H','--reference-run-id','quality-q2']),0)
            self.assertEqual(start.call_args.kwargs['quality_options']['pool'],'H')
            self.assertEqual(start.call_count,1)
            self.assertEqual(main(['start','--stage','graph','--fold','V1','--size','full',
                '--expected-commit','a'*40,'--pool','G','--reference-run-id','quality-q1']),1)
