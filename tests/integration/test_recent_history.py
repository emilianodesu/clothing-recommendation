import json
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from hm_recommender.data.ingest import load_dataset
from hm_recommender.model.demo import prepare_demo
from hm_recommender.model.inputs import Inputs, TrainConfig
from hm_recommender.model.train import train
from hm_recommender.model.diagnostics import verified_model
from hm_recommender.model.evaluate import evaluate_model
from hm_recommender.model.recent_history import validate_recent_history, HistoryConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from handoff import history_complete


def test_recent_history_replays_model_and_keeps_all_buyers_and_unseen_truth(tmp_path):
    features = prepare_demo(tmp_path/'fixture')
    config = TrainConfig(demo=True, min_free_bytes=0)
    directory = tmp_path/'model'
    train(features, config.model_dump(), directory)
    inputs = Inputs(features, config)
    before = {p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
    pointer = json.loads(before['latest.json'])
    source = {'run_id':'train-fixture', 'checkpoint':pointer, 'input_identity':inputs.identity,
              'rows_consumed':pointer['rows_consumed']}
    model = verified_model(directory, inputs, source)
    source['evaluation'] = evaluate_model(model, inputs, population_size=33)
    result = validate_recent_history(model, inputs, load_dataset(tmp_path/'fixture/dataset'), source,
        HistoryConfig())
    assert result['completed'] and result['model_replay_verified']
    assert result['population'] == {'users':33, 'catalog_size':24, 'with_recent_history':32, 'without_recent_history':1}
    # 31 repeat buyers get both items first; one has an unavailable third item;
    # the new buyer's article 0 is fifth in recent popularity.
    assert result['methods']['recent_then_popular']['metrics']['map_at_12'] == pytest.approx((31+2/3+1/5)/33)
    for name, method in result['methods'].items():
        assert method['metrics']['recall_at_200'] == pytest.approx((32+2/3)/33)
        assert method['segments']['without_recent_history']['users'] == 1
    controls = result['history_controls']
    assert controls['users'] == 32 and controls['excluded_without_history'] == 1
    assert len(controls['recent_then_popular']['permutations']) == 10
    assert all(v['unchanged_owner_rows']==0 for v in controls['recent_then_popular']['permutations'])
    assert result['paired']['recent_model_vs_recent_popular']['users'] == 33
    assert result['paired']['recent_popular_vs_popular']['metrics']['map_at_12']['ci95'][0] > 0
    assert result['quality_accepted'] is False and result['test_evaluated'] is False
    assert 'customer_id' not in json.dumps(result)
    assert before == {p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
    envelope = {'source_run_id':source['run_id'],'history_validation':result}
    assert history_complete(envelope)
    # Each mutation represents a closed but incomplete/contradictory report.
    for path, replacement in [
        (('methods','recent_then_popular','metrics'), None),
        (('methods','model','metrics','map_at_12'),float('nan')),
        (('paired','recent_popular_vs_popular'), None),
        (('paired','recent_popular_vs_popular','metrics','map_at_12','ci95'), []),
        (('paired','recent_popular_vs_popular','users'),32),
        (('config','permutation_repeats'),2),
        (('history_controls','recent_then_model','permutations'),controls['recent_then_model']['permutations'][:2]),
        (('history_controls','recent_then_model','permutations',0,'seed'),100),
        (('population','without_recent_history'),2),
    ]:
        broken = deepcopy(result)
        target = broken
        for key in path[:-1]: target=target[key]
        target[path[-1]] = replacement
        assert not history_complete({'source_run_id':source['run_id'],'history_validation':broken}), path
