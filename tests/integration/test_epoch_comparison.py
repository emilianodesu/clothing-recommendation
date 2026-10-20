import json
import pytest
from hm_recommender.data.ingest import load_dataset
from hm_recommender.model.demo import prepare_demo
from hm_recommender.model.inputs import Inputs, TrainConfig
from hm_recommender.model.train import train
from hm_recommender.model.diagnostics import verified_model
from hm_recommender.model.evaluate import evaluate_model
from hm_recommender.model.comparison import compare_models, ComparisonConfig
from hm_recommender.model.comparison_contract import validate_comparison_sources


def test_three_epoch_comparison_uses_same_buyers_and_never_permutes_unknown_ids(tmp_path):
    features = prepare_demo(tmp_path/'fixture')
    config = TrainConfig(demo=True, min_free_bytes=0)
    inputs = Inputs(features, config)
    sources, models = [], []
    for epochs in (1, 3):
        settings = config.model_copy(update={'epochs': epochs})
        model_dir = tmp_path/str(epochs)
        train(features, settings.model_dump(), model_dir)
        current_inputs = Inputs(features, settings)
        training = json.loads((model_dir/'training.json').read_text())
        source = {'run_id': 'train-fixture-'+str(epochs), 'config': settings.model_dump(),
                  'rows_consumed': training['rows_consumed'], 'input_identity': current_inputs.identity,
                  'uv_lock_sha256': 'a'*64, 'checkpoint': json.loads((model_dir/'latest.json').read_text())}
        model = verified_model(model_dir, current_inputs, source)
        source['evaluation'] = evaluate_model(model, current_inputs, population_size=33)
        models.append(model); sources.append(source)
    reference, candidate = sources
    validate_comparison_sources(candidate, reference)
    options = ComparisonConfig(sample_users=33, bootstrap_resamples=200, permutation_repeats=2)
    dataset = load_dataset(tmp_path/'fixture/dataset')
    result = compare_models(models[1], models[0], inputs, dataset, candidate, reference, options)
    assert result['completed'] is True and result['reference_replay_verified'] is True
    assert result['population']['users'] == 33
    assert result['known_user_controls']['users'] == 32
    assert result['known_user_controls']['excluded_unknown_users'] == 1
    assert result['candidate_vs_reference']['users'] == 33
    assert result['candidate_vs_reference']['metrics']['recall_at_200']['delta'] == pytest.approx(0)
    assert result['candidate_vs_recent_7d']['users'] == 33
    assert result['quality_accepted'] is False and result['test_evaluated'] is False
    assert 'customer_id' not in json.dumps(result)
    for model in ('candidate', 'reference'):
        control = result['known_user_controls'][model]
        assert len(control['permutations']) == 2
        assert all(row['unchanged_id_rows'] == 0 for row in control['permutations'])
    changed = dict(candidate, config=dict(candidate['config'], negatives=16))
    with pytest.raises(ValueError): validate_comparison_sources(changed, reference)
    with pytest.raises(ValueError): validate_comparison_sources(dict(candidate, rows_consumed=1), reference)
    with pytest.raises(ValueError): validate_comparison_sources(dict(candidate, input_identity={}), reference)
