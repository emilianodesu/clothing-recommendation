import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from handoff import package_report, read_report
spec = importlib.util.spec_from_file_location('github_training_handoff', Path(__file__).resolve().parents[2] / 'scripts/github-handoff.py')
github = importlib.util.module_from_spec(spec); spec.loader.exec_module(github)


class TrainingHandoffTests(unittest.TestCase):
    def test_training_report_round_trip_and_no_arbitrary_weight_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'train-mac24-fixture'; directory.mkdir()
            (directory / 'job.log').write_text('Training completed\n')
            (directory / 'result.json').write_text(json.dumps({'stage': 'T3', 'status': 'passed', 'verification_exit_code': 0}))
            report = dict(schema_version=1, kind='training-handoff', run_id=directory.name,
                machine_id='mac24', context='colima-hm-project', source_commit='a' * 40, source_clean=True,
                status='passed', steps=[dict(name='train', log='job.log', status='passed', exit_code=0)])
            archive = package_report(directory, report)
            self.assertEqual(github.validate_archive(archive)['kind'], 'training-handoff')
            self.assertEqual(read_report(archive)['status'], 'passed')
            self.assertEqual(github.tag_for(directory.name), 'handoff-' + directory.name)
