"""Inner-week early stopping with project NDCG, followed by a fresh four-week refit."""
import gc
from pathlib import Path
import time
from typing import Literal

import lightgbm as lgb
import numpy as np
import polars as pl
import pyarrow.parquet as pq

from hm_recommender.data.io import locked, sha256
from hm_recommender.quality.contracts import LocalArtifact, FoldSpec, QualityConfig, QualityFile, digest
from hm_recommender.quality.temporal import existing, publish, verified_path, frame
from hm_recommender.reranking.features import FEATURE_NAMES


class RankerManifest(LocalArtifact):
    fold: FoldSpec
    pool: Literal['H', 'G']
    feature_names: list[str]
    best_iteration: int
    parameters: dict
    statistics: dict


def project_ndcg_evaluator(labels, groups, relevant):
    labels = np.asarray(labels)
    groups, relevant = np.asarray(groups), np.asarray(relevant)
    if (len(groups) != len(relevant) or np.any(groups <= 0) or np.any(relevant <= 0) or
            groups.sum() != len(labels) or not np.isin(labels, [0, 1]).all()):
        raise ValueError('Invalid NDCG groups, labels or relevant denominators')
    discounts = 1 / np.log2(np.arange(2, 14))
    idcg = np.cumsum(discounts)[np.minimum(relevant, 12).astype(int) - 1]
    offsets = np.r_[0, np.cumsum(groups)]
    equal = np.all(groups == groups[0])
    def evaluate(scores, _dataset):
        scores = np.asarray(scores)
        if scores.shape != labels.shape or not np.isfinite(scores).all():
            raise ValueError('Invalid inner evaluation predictions')
        if equal:
            # Rows are sorted by article ID within each buyer; stable ties preserve it.
            matrix = scores.reshape(len(groups), int(groups[0]))
            order = np.argsort(-matrix, axis=1, kind='stable')[:, :12]
            hits = np.take_along_axis(labels.reshape(matrix.shape), order, axis=1)
            dcg = (hits * discounts[:hits.shape[1]]).sum(axis=1)
        else:
            dcg = np.array([np.dot(labels[a:b][np.argsort(-scores[a:b], kind='stable')[:12]],
                                   discounts[:min(12, b-a)]) for a, b in zip(offsets[:-1], offsets[1:])])
        return 'project_ndcg_at_12', float(np.mean(dcg / idcg)), True
    return evaluate


def training_parameters(config):
    params = {k: v for k, v in config.ranker.items() if k not in
              ('weeks', 'negatives', 'evaluator', 'num_boost_round', 'early_stopping_rounds')}
    params.update(device_type='cpu', data_random_seed=42, feature_fraction_seed=42,
                  bagging_seed=42, drop_seed=42, extra_seed=42, objective_seed=42, verbosity=-1)
    return params


def binary_dataset(table, weeks, root, name, params, *, full=False, reference=None):
    """Copy Parquet batches once into a disk-backed matrix; retain binned binary."""
    paths = [verified_path(table, w['full_file' if full else 'sample_file']) for w in weeks]
    rows = sum(pq.ParquetFile(p).metadata.num_rows for p in paths)
    if not rows:
        raise ValueError('No trainable groups in ranker table')
    matrix_path, label_path = root / (name + '.features.mmap'), root / (name + '.labels.mmap')
    matrix = np.memmap(matrix_path, dtype=np.float32, mode='w+', shape=(rows, len(FEATURE_NAMES)))
    labels = np.memmap(label_path, dtype=np.float32, mode='w+', shape=(rows,))
    sizes, relevant, offset = [], [], 0
    for week, path in zip(weeks, paths):
        metadata = frame(table, week['groups_file'])
        if not full:
            metadata = metadata.filter(pl.col('sampled_rows') > 0)
        group_sizes = metadata['candidates' if full else 'sampled_rows'].to_numpy()
        if int(group_sizes.sum()) != pq.ParquetFile(path).metadata.num_rows:
            raise ValueError('Ranker group sizes differ from Parquet rows')
        sizes.extend(group_sizes)
        relevant.extend(metadata['relevant_items'].to_numpy())
        for arrow in pq.ParquetFile(path).iter_batches(batch_size=65536):
            batch = pl.from_arrow(arrow)
            end = offset + len(batch)
            matrix[offset:end] = batch.select(FEATURE_NAMES).to_numpy()
            labels[offset:end] = batch['label'].to_numpy()
            offset = end
    if offset != rows:
        raise ValueError('Incomplete ranker matrix')
    matrix.flush(); labels.flush()
    sizes = np.asarray(sizes, dtype=np.int32)
    relevant = np.asarray(relevant, dtype=np.int32)
    dataset = lgb.Dataset(matrix, label=labels, group=sizes, feature_name=list(FEATURE_NAMES),
                          params=params, reference=reference, free_raw_data=True)
    dataset.construct()
    binary = root / (name + '.bin')
    dataset.save_binary(str(binary))
    saved_labels = np.asarray(labels).copy() if full else None
    del matrix, labels
    gc.collect()
    matrix_path.unlink(); label_path.unlink()
    return dataset, sizes, relevant, saved_labels


def load_model(root):
    root = Path(root)
    model = RankerManifest.model_validate_json((root / 'manifest.json').read_text())
    model._root = root
    if model.feature_names != list(FEATURE_NAMES):
        raise ValueError('Ranker feature schema mismatch')
    for name in model.files:
        verified_path(model, name)
    booster = lgb.Booster(model_file=str(verified_path(model, 'model')))
    if booster.feature_name() != list(FEATURE_NAMES):
        raise ValueError('Model feature schema mismatch')
    return model


def fit_ranker(table, config, output_dir):
    expected_weeks = [FoldSpec.training(table.fold.fold_id, n) for n in (4, 3, 2, 1)]
    if (config.model_dump() != QualityConfig().model_dump() or table.feature_names != list(FEATURE_NAMES) or
            [(w['cutoff'], w['label_end']) for w in table.weeks] !=
            [(str(w.cutoff), str(w.label_end)) for w in expected_weeks]):
        raise ValueError('Ranker schema or temporal training windows differ from protocol')
    root = Path(output_dir)
    params = training_parameters(config)
    identity = {'training_table': digest(table.model_dump(mode='json')), 'parameters': params,
                'lightgbm_version': lgb.__version__,
                'source_sha256': table.identity['source_sha256'],
                'dataset_manifest_sha256': table.identity['dataset_manifest_sha256'],
                'protocol_sha256': table.identity['protocol_sha256']}
    with locked(root):
        saved = existing(root, identity, RankerManifest)
        if saved:
            return load_model(root)
        started = time.monotonic()
        training, groups, _, _ = binary_dataset(table, table.weeks[:3], root, 'inner-training', params)
        validation, valid_groups, relevant, labels = binary_dataset(
            table, table.weeks[3:], root, 'inner-validation', params, full=True, reference=training)
        evaluator = project_ndcg_evaluator(labels, valid_groups, relevant)
        construction = time.monotonic() - started
        started = time.monotonic()
        history = {}
        selected = lgb.train(params, training, num_boost_round=config.ranker['num_boost_round'],
            valid_sets=[validation], valid_names=['inner'], feval=evaluator,
            callbacks=[lgb.early_stopping(config.ranker['early_stopping_rounds'], first_metric_only=True, verbose=False),
                       lgb.record_evaluation(history)])
        best = selected.best_iteration
        if not 1 <= best <= config.ranker['num_boost_round']:
            raise ValueError('Invalid internally selected iteration')
        selected.save_model(str(root / 'inner-model.txt'), num_iteration=best)
        inner_groups, inner_rows = len(groups), int(groups.sum())
        inner_score = selected.best_score['inner']['project_ndcg_at_12']
        fit_seconds = time.monotonic() - started
        del selected, validation, training, labels, evaluator
        gc.collect()
        started = time.monotonic()
        refit, refit_groups, _, _ = binary_dataset(table, table.weeks, root, 'refit', params)
        construction += time.monotonic() - started
        started = time.monotonic()
        model = lgb.train(params, refit, num_boost_round=best)
        model.save_model(str(root / 'model.txt'))
        refit_seconds = time.monotonic() - started
        stats = dict(inner_training_weeks=3, refit_training_weeks=4,
            early_stopping_cutoff=table.weeks[-1]['cutoff'], outer_labels_used=False,
            inner_training_groups=inner_groups, inner_training_rows=inner_rows,
            inner_evaluation_groups=len(valid_groups), inner_evaluation_rows=int(valid_groups.sum()),
            refit_groups=len(refit_groups), refit_rows=int(refit_groups.sum()),
            refit_requested_iterations=best, actual_trees=model.current_iteration(),
            inner_best_ndcg=inner_score, evaluated_iterations=len(history['inner']['project_ndcg_at_12']),
            binary_construction_seconds=construction, fit_seconds=fit_seconds, refit_seconds=refit_seconds,
            inner_ndcg_history=history['inner']['project_ndcg_at_12'])
        files = {}
        for name, filename in [('model', 'model.txt'), ('inner_model', 'inner-model.txt'),
                               ('inner_training', 'inner-training.bin'), ('inner_validation', 'inner-validation.bin'), ('refit', 'refit.bin')]:
            path = root / filename
            files[name] = QualityFile(path=filename, sha256=sha256(path), rows=0, size_bytes=path.stat().st_size)
        publish(root, RankerManifest(identity=identity, files=files, fold=table.fold, pool=table.pool,
            feature_names=list(FEATURE_NAMES), best_iteration=best, parameters=params, statistics=stats))
        return load_model(root)
