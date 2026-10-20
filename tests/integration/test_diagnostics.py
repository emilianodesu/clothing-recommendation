import json
import pytest
from hm_recommender.data.ingest import load_dataset
from hm_recommender.model.demo import prepare_demo
from hm_recommender.model.inputs import Inputs, TrainConfig
from hm_recommender.model.train import train, load_checkpoint
from hm_recommender.model.two_tower import TwoTower
from hm_recommender.model.evaluate import evaluate_model
from hm_recommender.model.diagnostics import diagnose, DiagnosticConfig, verified_model


def test_diagnostic_replays_checkpoint_without_changing_training_and_preserves_unseen_truth(tmp_path):
    features = prepare_demo(tmp_path / 'fixture')
    config = TrainConfig(demo=True, min_free_bytes=0)
    train(features, config.model_dump(), tmp_path / 'model')
    inputs = Inputs(features, config)
    before = {p.name: p.read_bytes() for p in (tmp_path / 'model').iterdir() if p.is_file()}
    pointer = json.loads(before['latest.json'])
    source = {'checkpoint': pointer, 'input_identity': inputs.identity, 'rows_consumed': pointer['rows_consumed']}
    model = verified_model(tmp_path / 'model', inputs, source)
    dataset = load_dataset(tmp_path / 'fixture/dataset')
    expected = evaluate_model(model, inputs, population_size=33)
    result = diagnose(model, inputs, dataset, expected, DiagnosticConfig(sample_users=16))
    assert result['replay_verified'] is True and result['test_evaluated'] is False
    assert result['replay']['out_of_catalog_relevant_items'] == 1
    assert result['population']['users'] == 33 and result['sample']['users'] == 16
    assert set(result['baselines']) == {'historical', 'recent_7d', 'recent_28d'}
    assert set(result['controls']) == {'zero_user_id', 'permuted_user_id', 'permuted_history'}
    assert result['normal']['metrics']['recall_at_200'] == pytest.approx((32 + 2 / 3) / 33)  # one buyer has 2 known + 1 unavailable items
    assert 'customer_id' not in json.dumps(result)
    assert before == {p.name: p.read_bytes() for p in (tmp_path / 'model').iterdir() if p.is_file()}
    with pytest.raises(ValueError, match='identity'):
        verified_model(tmp_path / 'model', inputs, dict(source, input_identity={}))
    with pytest.raises(ValueError, match='checkpoint'):
        verified_model(tmp_path / 'model', inputs, dict(source, checkpoint=dict(pointer, sha256='0'*64)))
    with (tmp_path / 'model' / pointer['path']).open('ab') as stream:
        stream.write(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        verified_model(tmp_path / 'model', inputs, source)


def test_diagnostic_worker_records_failure_without_modifying_source(tmp_path):
    from hm_recommender.model.diagnostic_worker import run
    run_dir = tmp_path / 'data-jobs/diagnose-fixture'; run_dir.mkdir(parents=True)
    (run_dir/'environment.json').write_text(json.dumps({'source_commit': 'a'*40,
        'diagnostic_source': {'run_id': 'train-missing', 'archive_sha256': 'b'*64}}))
    config = tmp_path/'config.json'; config.write_text('{}')
    assert run(config, run_dir, verify=False) == 1
    result = json.loads((run_dir/'result.json').read_text())
    assert result['status'] == 'failed' and result['phase'] == 'source'
    assert result['job_resources']['peak_rss_bytes'] > 0
    assert result['verification_exit_code'] == 0
