"""Weekly candidate groups, deterministic negative sampling and private tables."""
from collections import Counter
import hashlib
from pathlib import Path
import time
from typing import Literal

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from hm_recommender.data.ingest import scan
from hm_recommender.data.io import locked, sha256
from hm_recommender.graph.build import build_graph
from hm_recommender.graph.retrieve import rank_graph
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.contracts import LocalArtifact, FoldSpec, QualityConfig, QualityFile, digest
from hm_recommender.quality.temporal import build_snapshot, read_labels, verified_path, existing, publish, frame
from hm_recommender.reranking.features import FeatureContext, FEATURE_NAMES, FEATURE_VERSION


class TrainingTable(LocalArtifact):
    fold: FoldSpec
    pool: Literal['H', 'G']
    feature_names: list[str]
    weeks: list[dict]
    statistics: dict


def sample_group(week, customer, articles, labels):
    positives = np.flatnonzero(labels)
    negatives = np.flatnonzero(np.asarray(labels) == 0)
    if not len(positives) or not len(negatives):
        return np.array([], dtype=np.int64)
    def key(i):
        return hashlib.sha256(f'42:{week}:{customer}:{articles[i]}'.encode()).digest(), articles[i]
    selected = list(positives) + sorted(negatives, key=key)[:20]
    return np.array(sorted(selected, key=lambda i: articles[i]), dtype=np.int64)


def write_week_examples(context, ranking, labels, output_dir, *, keep_full):
    """Labels are attached only after FeatureContext has produced the features."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False)
    truth = {c: set(items) for c, items in labels.group_by('customer_id').agg('article_id').iter_rows()}
    counts = Counter(groups_total=0, groups_trainable=0, groups_without_positives=0,
                     groups_without_negatives=0, candidate_rows=0, sampled_rows=0,
                     relevant_items=0, positives_recovered=0, positives_not_recovered=0)
    groups, writers, rows = [], {}, Counter()
    observed = set()
    try:
        source = pq.ParquetFile(verified_path(ranking, 'ranking'))
        for number in range(source.num_row_groups):
            batch = pl.from_arrow(source.read_row_group(number)).select('customer_id', 'article_id')
            batch = batch.sort('customer_id', 'article_id')
            features = context.transform(batch)
            sample_indices, binary_labels = [], []
            offset = 0
            for group in features.partition_by('customer_id', maintain_order=True):
                customer = group['customer_id'][0]
                if customer in observed or customer not in truth:
                    raise ValueError('Split, duplicate or unexpected ranking group')
                observed.add(customer)
                articles = group['article_id'].to_list()
                binary = np.array([int(a in truth[customer]) for a in articles], dtype=np.uint8)
                selected = sample_group(str(ranking.fold.cutoff), customer, articles, binary)
                positive = int(binary.sum())
                counts.update(groups_total=1, groups_trainable=int(len(selected) > 0),
                    groups_without_positives=int(positive == 0),
                    groups_without_negatives=int(positive == len(articles)),
                    candidate_rows=len(articles), sampled_rows=len(selected),
                    relevant_items=len(truth[customer]), positives_recovered=positive,
                    positives_not_recovered=len(truth[customer]) - positive)
                groups.append((customer, str(ranking.fold.cutoff), len(articles), len(truth[customer]),
                               positive, len(selected)))
                binary_labels.extend(binary)
                sample_indices.extend(offset + selected)
                offset += len(articles)
            features = features.with_columns(pl.Series('label', binary_labels, dtype=pl.UInt8))
            tables = {'sample': features[sample_indices]}
            if keep_full:
                tables['full'] = features
            for name, table in tables.items():
                arrow = table.to_arrow()
                if name not in writers:
                    writers[name] = pq.ParquetWriter(root / (name + '.parquet.partial'), arrow.schema, compression='zstd')
                writers[name].write_table(arrow)
                rows[name] += len(table)
    finally:
        for writer in writers.values():
            writer.close()
    if observed != set(truth):
        raise ValueError('Candidate table omitted buyers')
    files = {}
    for name in writers:
        path = root / (name + '.parquet')
        path.with_suffix('.parquet.partial').replace(path)
        files[name] = QualityFile(path=path.name, sha256=sha256(path), rows=rows[name], size_bytes=path.stat().st_size)
    table = pl.DataFrame(groups, schema={'customer_id': pl.String, 'week_start': pl.String,
        'candidates': pl.UInt32, 'relevant_items': pl.UInt32, 'positives': pl.UInt32, 'sampled_rows': pl.UInt32}, orient='row')
    path = root / 'groups.parquet'
    table.write_parquet(path, compression='zstd')
    files['groups'] = QualityFile(path=path.name, sha256=sha256(path), rows=len(table), size_bytes=path.stat().st_size)
    return {'files': files, 'counts': dict(counts)}


def build_ranking_examples(dataset, fold, pool, config, output_dir, *, size='full'):
    if (fold.role != 'evaluation' or pool not in ('H', 'G') or size not in ('demo', 'pilot', 'full') or
            config.model_dump() != QualityConfig().model_dump()):
        raise ValueError('Fixed ranker protocol and evaluation parent required')
    root = Path(output_dir)
    identity = {'dataset_manifest_sha256': sha256(dataset._root / 'manifest.json'),
        'source_sha256': dataset.source_sha256, 'protocol_sha256': digest(config.model_dump()),
        'fold': fold.model_dump(mode='json'), 'pool': pool, 'size': size,
        'feature_version': FEATURE_VERSION, 'feature_names': list(FEATURE_NAMES)}
    with locked(root):
        saved = existing(root, identity, TrainingTable)
        if saved:
            return saved
        started = time.monotonic()
        files, weeks, total = {}, [], Counter()
        for index, distance in enumerate((4, 3, 2, 1)):
            week = FoldSpec.training(fold.fold_id, distance)
            print(f'Q3: {pool} training week {week.cutoff}', flush=True)
            week_root = root / f'week-{index}'
            before = time.monotonic()
            snapshot = build_snapshot(dataset, week, week_root / 'snapshot', config)
            graph = build_graph(dataset, snapshot, config, week_root / 'graph')
            labels = read_labels(dataset, week).collect(engine='streaming')
            buyers = labels['customer_id'].unique().sort().to_list()
            original_buyers = len(buyers)
            if size == 'pilot':
                buyers = sorted(sorted(buyers, key=lambda c: (hashlib.sha256(b'42:' + c.encode()).digest(), c))[:config.pilot_users])
                labels = labels.filter(pl.col('customer_id').is_in(buyers))
            if not buyers:
                raise ValueError('Empty ranker training week')
            ranking = (rank_baseline(snapshot, buyers, 'H', week_root / 'ranking') if pool == 'H' else
                       rank_graph(snapshot, graph, buyers, week_root / 'ranking'))
            context = FeatureContext(dataset, snapshot, graph, buyers)
            result = write_week_examples(context, ranking, labels, week_root / 'examples', keep_full=distance == 1)
            del context
            names = {}
            for name, entry in result['files'].items():
                key = f'week-{index}-{name}'
                files[key] = entry.model_copy(update={'path': f'week-{index}/examples/{entry.path}'})
                names[name + '_file'] = key
            total.update(result['counts'])
            label_window = scan(dataset).filter((pl.col('t_dat') >= week.cutoff) &
                (pl.col('t_dat') < week.label_end)).select(pl.len().alias('rows'), pl.col('t_dat').max()).collect()
            weeks.append({'cutoff': str(week.cutoff), 'label_end': str(week.label_end),
                'role': 'early_stopping' if distance == 1 else 'inner_training',
                'buyers_before_sampling': original_buyers, 'buyers': len(buyers),
                'catalog_size': snapshot.counts['catalog'],
                'sampled_out_buyers': original_buyers - len(buyers),
                'label_transactions_all_buyers': label_window['rows'].item(),
                'max_feature_date': str(frame(snapshot, 'profiles')['last_purchase'].max()),
                'max_label_date': str(label_window['t_dat'].item()),
                'history_transactions90': graph.statistics['transactions_window'],
                'snapshot_sha256': digest(snapshot.model_dump(mode='json')),
                'graph_sha256': digest(graph.model_dump(mode='json')),
                'ranking_sha256': ranking.files['ranking'].sha256, 'counts': result['counts'],
                'preparation_seconds': time.monotonic() - before, **names})
        total['preparation_seconds'] = time.monotonic() - started
        total['private_table_bytes'] = sum(entry.size_bytes for entry in files.values())
        return publish(root, TrainingTable(identity=identity, files=files, fold=fold, pool=pool,
            feature_names=list(FEATURE_NAMES), weeks=weeks, statistics=dict(total)))
