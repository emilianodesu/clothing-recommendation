"""Q3 orchestration with immutable Q2/Q1 ancestry and unchanged outer pools."""
import json
from pathlib import Path
import sys
import time
from zipfile import ZipFile

from hm_recommender.data.io import sha256
from hm_recommender.graph.build import GraphManifest
from hm_recommender.graph.retrieve import GraphRankingArtifact, rank_graph
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.contracts import FoldEvaluation, digest
from hm_recommender.quality.evaluate import evaluate_ranking
from hm_recommender.quality.graph_stage import FIELDS, load_local, load_reference
from hm_recommender.reranking.examples import build_ranking_examples
from hm_recommender.reranking.features import FEATURE_VERSION
from hm_recommender.reranking.predict import rerank
from hm_recommender.reranking.train import fit_ranker


def load_ranker_reference(reference, dataset, fold, size, config, customers, labels):
    archive = Path(reference['path'])
    if sha256(archive) != reference['archive_sha256']:
        raise ValueError('Q2 archive changed')
    with ZipFile(archive) as bundle:
        result = json.loads(bundle.read('result.json'))
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
    sys.path.insert(0, '/opt/hmrec/scripts')
    from quality_handoff import validate_quality_result
    validate_quality_result(result)
    if result['stage'] != 'Q2' or result['fold'] != fold.model_dump(mode='json') or result['size'] != size:
        raise ValueError('Q2 fold, size or stage mismatch')
    baseline = reference['baseline_reference']
    if any(result['reference'][key] != baseline[key] for key in ('run_id', 'archive_sha256')):
        raise ValueError('Q2/Q1 ancestry mismatch')
    snapshot, rankings, q1 = load_reference(baseline, dataset, fold, size, config, customers, labels)
    if (result['inputs'] != q1['inputs'] or result['population'] != q1['population'] or
            any(result['methods'][m] != q1['methods'][m] or result['artifacts'][m] != q1['artifacts'][m] for m in ('P', 'H'))):
        raise ValueError('Q2 reference baselines differ from Q1')
    work = Path(reference['work_path'])
    graph = load_local(work / 'graph', GraphManifest)
    ranking = load_local(work / 'ranking-G', GraphRankingArtifact)
    evaluation = load_local(work / 'evaluation-G', FoldEvaluation)
    if (graph.fold != fold or graph.identity['snapshot'] != result['inputs']['snapshot_sha256'] or
            {k: v.sha256 for k, v in graph.files.items()} != result['graph_artifacts'] or graph.statistics != result['graph'] or
            ranking.fold != fold or ranking.method != 'G' or ranking.customers != len(customers) or
            ranking.identity['customers_sha256'] != digest(customers) or
            ranking.identity['snapshot'] != result['inputs']['snapshot_sha256'] or
            ranking.identity['graph'] != digest(graph.model_dump(mode='json')) or
            ranking.files['ranking'].sha256 != result['artifacts']['G']['ranking_sha256'] or
            evaluation.fold != fold or evaluation.method != 'G' or
            evaluation.identity['ranking'] != digest(ranking.model_dump(mode='json')) or
            evaluation.identity['labels_sha256'] != digest(labels.to_dicts()) or
            evaluation.files['metrics'].sha256 != result['artifacts']['G']['metrics_sha256'] or
            {k: getattr(evaluation, k) for k in FIELDS} != result['methods']['G']):
        raise ValueError('Q2 private graph or evaluation changed')
    rankings['G'] = ranking
    return snapshot, graph, rankings, result


def run_ranker_stage(environment, dataset, fold, config, customers, labels, total, work):
    pool = environment['quality'].get('pool')
    if pool not in ('H', 'G'):
        raise ValueError('Q3 requires H or G candidate pool')
    started = time.monotonic()
    reference = environment['quality_reference']
    snapshot, graph, rankings, previous = load_ranker_reference(
        reference, dataset, fold, environment['quality']['size'], config, customers, labels)
    costs = {'reference_verification_seconds': time.monotonic() - started}
    table = build_ranking_examples(dataset, fold, pool, config, work / 'training', size=environment['quality']['size'])
    model = fit_ranker(table, config, work / 'model')
    # Replay retrieval only, not Q1/Q2 evaluations: measure preparation and verify
    # the pool remains byte-identical to the predeclared closed reference.
    started = time.monotonic()
    candidate = (rank_baseline(snapshot, customers, 'H', work / 'external-pool') if pool == 'H' else
                 rank_graph(snapshot, graph, customers, work / 'external-pool'))
    costs['external_retrieval_seconds'] = time.monotonic() - started
    if candidate.files['ranking'].sha256 != rankings[pool].files['ranking'].sha256:
        raise ValueError('External candidate replay differs from Q1/Q2')
    started = time.monotonic()
    ranked = rerank(model, candidate, snapshot, graph, dataset, work / ('ranking-R' + pool))
    costs['prediction_seconds'] = time.monotonic() - started
    started = time.monotonic()
    evaluated = evaluate_ranking(ranked, labels.lazy(), snapshot, work / ('evaluation-R' + pool))
    costs['evaluation_seconds'] = time.monotonic() - started
    if evaluated.metrics['recall_at_200'] != previous['methods'][pool]['metrics']['recall_at_200']:
        raise ValueError('Ranker changed external Recall@200')
    costs.update(training_preparation_seconds=table.statistics['preparation_seconds'],
        binary_construction_seconds=model.statistics['binary_construction_seconds'],
        fit_seconds=model.statistics['fit_seconds'], refit_seconds=model.statistics['refit_seconds'],
        external_graph_build_seconds=graph.statistics['build_seconds'])
    costs['candidate_cost_seconds'] = sum(v for k, v in costs.items() if k not in
                                        ('reference_verification_seconds', 'evaluation_seconds'))
    methods = dict(previous['methods'], **{'R' + pool: {k: getattr(evaluated, k) for k in FIELDS}})
    artifacts = dict(previous['artifacts'], **{'R' + pool: {
        'ranking_sha256': ranked.files['ranking'].sha256, 'metrics_sha256': evaluated.files['metrics'].sha256}})
    return {'methods': methods, 'artifacts': artifacts, 'population': previous['population'],
        'inputs': previous['inputs'], 'parity': previous.get('parity'), 'costs': costs,
        'reference': {'verified': True, 'stage': 'Q2', 'run_id': reference['run_id'],
            'archive_sha256': reference['archive_sha256'], 'private_artifacts_verified': True,
            'baseline': {k: reference['baseline_reference'][k] for k in ('run_id', 'archive_sha256')}},
        'ranker': {'pool': pool, 'feature_version': FEATURE_VERSION, 'feature_names': model.feature_names,
            'best_iteration': model.best_iteration, 'parameters': model.parameters,
            'lightgbm_version': model.identity['lightgbm_version'], 'weeks': table.weeks,
            'training_counts': table.statistics, 'statistics': model.statistics,
            'training_table_sha256': digest(table.model_dump(mode='json')),
            'model_manifest_sha256': digest(model.model_dump(mode='json')),
            'model_artifacts': {k: v.sha256 for k, v in model.files.items()},
            'outer_graph_artifacts': previous['graph_artifacts'],
            'reference_pool_ranking_sha256': rankings[pool].files['ranking'].sha256,
            'candidate_set_verified': True, 'pool_replay_verified': True}}
