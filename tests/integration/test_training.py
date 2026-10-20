import json
import numpy as np
import pytest
import torch
from hm_recommender.model.demo import prepare_demo
from hm_recommender.model.train import train, load_checkpoint, sample_negatives
from hm_recommender.model.inputs import Inputs, TrainConfig, load_features
from hm_recommender.model.evaluate import evaluate_model
from hm_recommender.model.two_tower import TwoTower


def test_checkpoint_resume_preserves_progress(tmp_path):
    features = prepare_demo(tmp_path / 'fixture')
    config = {'batch_size': 16, 'epochs': 2, 'min_free_bytes': 0, 'checkpoint_steps': 4}
    complete = train(features, config, tmp_path / 'complete')
    stopped = train(features, config, tmp_path / 'resumed', stop_after_steps=3)
    assert stopped.rows_consumed == 48 and stopped.epochs == 0
    assert json.loads((tmp_path / 'resumed/training.json').read_text())['status'] == 'interrupted'
    resumed = train(features, config, tmp_path / 'resumed', resume=True)
    assert resumed.rows_consumed == complete.rows_consumed > stopped.rows_consumed
    a, b = load_checkpoint(tmp_path / 'complete'), load_checkpoint(tmp_path / 'resumed')
    assert a['step'] == b['step']
    for key in a['model']:
        torch.testing.assert_close(a['model'][key], b['model'][key], rtol=0, atol=0)
    assert len(list((tmp_path / 'resumed').glob('checkpoint-*.pt'))) <= 2
    with pytest.raises(ValueError, match='changed'):
        train(features, dict(config, seed=7), tmp_path / 'resumed', resume=True)
    model = TwoTower(**b['settings']); model.load_state_dict(b['model'])
    evaluation = evaluate_model(model, Inputs(features, TrainConfig.model_validate(config)))
    assert evaluation['evaluated_users'] == 33
    assert evaluation['segments']['new_user']['users'] == 1
    assert evaluation['out_of_catalog_relevant_items'] == 1
    assert evaluation['test_evaluated'] is False
    assert evaluation['recall_at_200'] < 1  # unavailable item remains in denominator


def test_negatives_exclude_all_weekly_positives_and_unavailable_items():
    negatives = sample_negatives(np.random.default_rng(2), np.array([1, 2]), np.array([0, 1, 2]),
                                np.array([1 * 5 + 0, 1 * 5 + 1, 2 * 5 + 2]), 5, 100)
    assert set(negatives[0]) == {2}
    assert set(negatives[1]) <= {0, 1}


def test_corrupt_features_and_checkpoint_are_rejected(tmp_path):
    features = prepare_demo(tmp_path / 'fixture')
    train(features, {'min_free_bytes': 0}, tmp_path / 'run', stop_after_steps=1)
    pointer = json.loads((tmp_path / 'run/latest.json').read_text())
    with (tmp_path / 'run' / pointer['path']).open('ab') as stream:
        stream.write(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        load_checkpoint(tmp_path / 'run')
    with (features._root / 'profiles.parquet').open('ab') as stream:
        stream.write(b'corrupt')
    with pytest.raises(ValueError, match='integrity'):
        load_features(features._root)
