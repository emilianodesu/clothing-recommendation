"""Detached T3 worker; checkpoints stay local, summary contains no customer IDs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import torch
from hm_recommender.data.io import atomic_json
from hm_recommender.model.demo import prepare_demo
from hm_recommender.model.inputs import TrainConfig, Inputs, load_features
from hm_recommender.model.train import train, load_checkpoint
from hm_recommender.model.two_tower import TwoTower
from hm_recommender.model.evaluate import evaluate_model


def run(config_path, run_dir, *, verify=True):
    metadata = json.loads((run_dir / 'environment.json').read_text())
    experiment = metadata['experiment_id']
    model_dir = run_dir.parent.parent / 'training' / experiment
    config = TrainConfig.model_validate_json(config_path.read_text())
    atomic_json(run_dir / 'config.resolved.json', config.model_dump())
    result = {'stage': 'T3', 'status': 'running', 'source_commit': metadata['source_commit'],
              'experiment_id': experiment, 'attempt': metadata['attempt'], 'verification_exit_code': None}
    code = 1
    def phase(name):
        result['phase'] = name
        atomic_json(run_dir / 'result.json', result)
    try:
        phase('verification')
        if verify:
            code = subprocess.run([sys.executable, '-m', 'pytest', 'tests/unit', 'tests/integration/test_training.py', '-q']).returncode
        else:
            code = 0
        result['verification_exit_code'] = code
        if code:
            raise ValueError('Model image tests failed')
        phase('inputs')
        features = prepare_demo(run_dir.parent.parent / 'training-demo-v1') if config.demo else load_features(run_dir.parent.parent / 'features/hm-v1/train')
        phase('training')
        train(features, config.model_dump(), model_dir, resume=metadata['attempt'] > 1)
        training = json.loads((model_dir / 'training.json').read_text())
        result['training'] = training
        if training['status'] != 'passed':
            result['status'] = 'interrupted'
            return 130
        # A completed training checkpoint can resume evaluation/tracking without another epoch.
        phase('evaluation')
        state = load_checkpoint(model_dir)
        model = TwoTower(**state['settings']); model.load_state_dict(state['model']); model.eval()
        del state  # release optimizer tensors before exact catalog scoring
        from hm_recommender.contracts import DatasetManifest
        dataset_path = (run_dir.parent.parent / 'training-demo-v1/dataset/manifest.json') if config.demo else Path('/data/processed/hm-v1/manifest.json')
        dataset = DatasetManifest.model_validate_json(dataset_path.read_text())
        inputs = Inputs(features, config)
        if dataset.dataset_id != features.dataset_id or dataset.source_sha256 != inputs.manifest['source_sha256']:
            raise ValueError('Customer population belongs to another dataset')
        evaluation = evaluate_model(model, inputs, population_size=dataset.counts['customers'])
        result['evaluation'] = evaluation
        atomic_json(model_dir / 'evaluation.json', evaluation)
        with (model_dir / 'model.pt.partial').open('wb') as stream:
            torch.save({'settings': model.settings, 'model': model.state_dict()}, stream)
            stream.flush(); os.fsync(stream.fileno())
        (model_dir / 'model.pt.partial').replace(model_dir / 'model.pt')
        for name in ('training.json', 'evaluation.json'):
            shutil.copyfile(model_dir / name, run_dir / name)
        if not config.demo:
            phase('tracking')
            from hm_recommender.model.tracking import log_run
            previous = json.loads((model_dir / 'mlflow.json').read_text())['run_id'] if (model_dir / 'mlflow.json').exists() else None
            result['mlflow'] = log_run(model_dir, result, model_dir / 'model.pt', previous)
        result['status'] = 'passed'
        return 0
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__, error='Inspect worker stage and input integrity; raw input values are omitted')
        # No secrets are passed to this worker. Raw rows/IDs are never logged.
        print('T3 failed: ' + type(error).__name__, flush=True)
        return 1
    finally:
        result['verification_exit_code'] = code
        atomic_json(run_dir / 'result.json', result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    return run(args.config, args.run_dir)


if __name__ == '__main__':
    sys.exit(main())
