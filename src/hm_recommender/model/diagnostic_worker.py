"""Detached read-only diagnosis of an existing, completed T3 checkpoint."""
import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

from hm_recommender.data.io import atomic_json, safe_file, sha256
from hm_recommender.data.ingest import load_dataset
from hm_recommender.model.inputs import TrainConfig, Inputs, load_features
from hm_recommender.model.diagnostics import DiagnosticConfig, diagnose, verified_model
from hm_recommender.model.train import telemetry
from hm_recommender.model.comparison import ComparisonConfig, compare_models
from hm_recommender.model.comparison_contract import validate_comparison_sources
from hm_recommender.model.recent_history import HistoryConfig, validate_recent_history


def run(config_path, run_dir, *, verify=True):
    metadata = json.loads((run_dir / 'environment.json').read_text())
    source = metadata['diagnostic_source']
    reference = metadata.get('diagnostic_reference')
    protocol = metadata.get('diagnostic_protocol', 'standard')
    artifacts = run_dir.parent.parent
    result = {'stage': 'T3-diagnostics', 'status': 'running', 'source_commit': metadata['source_commit'],
              'source_run_id': source['run_id'], 'verification_exit_code': None,
              'comparison_required': bool(reference), 'reference_run_id': reference['run_id'] if reference else None,
              'diagnostic_protocol': protocol}
    started = time.monotonic()
    code = None
    def phase(name):
        result['phase'] = name
        atomic_json(run_dir / 'result.json', result)
        print('Diagnostic phase: ' + name, flush=True)
    try:
        phase('verification')
        if protocol not in ('standard','recent-history') or (protocol == 'recent-history' and reference):
            raise ValueError('Incompatible diagnostic protocol')
        config = (HistoryConfig if protocol == 'recent-history' else ComparisonConfig if reference else DiagnosticConfig).model_validate_json(config_path.read_text())
        atomic_json(run_dir / 'config.resolved.json', config.model_dump())
        code = subprocess.run([sys.executable, '-m', 'pytest', 'tests/unit', 'tests/integration/test_diagnostics.py', 'tests/integration/test_epoch_comparison.py', 'tests/integration/test_recent_history.py', '-q']).returncode if verify else 0
        result['verification_exit_code'] = code
        if code:
            raise ValueError('Diagnostic image tests failed')
        phase('source')
        archive = safe_file(artifacts, 'handoff/' + source['run_id'] + '.zip')
        if sha256(archive) != source['archive_sha256']:
            raise ValueError('Source archive checksum changed')
        if sha256(Path('/opt/hmrec/uv.lock')) != source['uv_lock_sha256']:
            raise ValueError('Dependency lock changed')
        training_config = TrainConfig.model_validate(source['config'])
        feature_root = artifacts / ('training-demo-v1/features/train' if training_config.demo else 'features/hm-v1/train')
        features = load_features(feature_root)
        inputs = Inputs(features, training_config)
        model_dir = safe_file(artifacts, 'training/' + source['experiment_id'])
        model = verified_model(model_dir, inputs, source)
        dataset = load_dataset(artifacts / 'training-demo-v1/dataset' if training_config.demo else Path('/data/processed/hm-v1'))
        if protocol == 'recent-history':
            validation = validate_recent_history(model, inputs, dataset, source, config, progress=phase)
            result['history_validation'] = validation
            result['diagnostic'] = {'replay_verified':validation['model_replay_verified'], 'replay':validation['replay'],
                                    'test_evaluated':False, 'quality_accepted':False}
        else:
            result['diagnostic'] = diagnose(model, inputs, dataset, source['evaluation'], config, progress=phase)
        if reference:
            phase('reference_integrity')
            validate_comparison_sources(source, reference)
            reference_archive = safe_file(artifacts, 'handoff/' + reference['run_id'] + '.zip')
            if sha256(reference_archive) != reference['archive_sha256']:
                raise ValueError('Reference archive checksum changed')
            reference_inputs = Inputs(features, TrainConfig.model_validate(reference['config']))
            reference_model = verified_model(safe_file(artifacts, 'training/' + reference['experiment_id']), reference_inputs, reference)
            del reference_inputs
            result['comparison'] = compare_models(model, reference_model, inputs, dataset, source, reference, config, progress=phase)
        result['status'] = 'passed'
        phase('complete')
        return 0
    except KeyboardInterrupt:
        result['status'] = 'interrupted'
        return 130
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__,
                      error='Inspect phase and source integrity; raw values are omitted')
        print('Diagnostic failed: ' + type(error).__name__, flush=True)
        return 1
    finally:
        result['job_resources'] = telemetry()
        result['job_duration_seconds'] = time.monotonic() - started
        result['verification_exit_code'] = code
        atomic_json(run_dir / 'result.json', result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    return run(args.config, args.run_dir)


if __name__ == '__main__':
    sys.exit(main())
