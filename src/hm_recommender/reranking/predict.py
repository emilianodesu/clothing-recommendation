"""Score and reorder the existing pool, with deterministic article-ID ties."""
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import pyarrow.parquet as pq

from hm_recommender.data.io import locked, sha256
from hm_recommender.quality.contracts import RankingArtifact, QualityFile, digest
from hm_recommender.quality.temporal import existing, publish, verified_path
from hm_recommender.reranking.features import FeatureContext, FEATURE_NAMES


def rerank(model, candidates, snapshot, graph, dataset, output_dir):
    if (model.feature_names != list(FEATURE_NAMES) or model.fold != snapshot.fold or
            candidates.fold != snapshot.fold or candidates.method != model.pool or
            candidates.identity['snapshot'] != digest(snapshot.model_dump(mode='json')) or
            model.identity['dataset_manifest_sha256'] != sha256(dataset._root / 'manifest.json') or
            model.identity['source_sha256'] != dataset.source_sha256):
        raise ValueError('Ranker schema, source, fold or candidate pool mismatch')
    source = verified_path(candidates, 'ranking')
    buyers = pl.scan_parquet(source).select('customer_id').unique().sort('customer_id').collect()['customer_id'].to_list()
    if len(buyers) != candidates.customers or digest(buyers) != candidates.identity['customers_sha256']:
        raise ValueError('Candidate population differs from manifest')
    root = Path(output_dir)
    identity = {'snapshot': digest(snapshot.model_dump(mode='json')), 'customers_sha256': digest(buyers),
        'method': 'R' + model.pool, 'model': digest(model.model_dump(mode='json')),
        'candidate_ranking': digest(candidates.model_dump(mode='json'))}
    with locked(root):
        saved = existing(root, identity, RankingArtifact)
        if saved:
            return saved
        booster = lgb.Booster(model_file=str(verified_path(model, 'model')))
        if booster.feature_name() != list(FEATURE_NAMES):
            raise ValueError('Booster feature schema mismatch')
        context = FeatureContext(dataset, snapshot, graph, buyers)
        file = pq.ParquetFile(source)
        path = root / 'ranking.parquet.partial'
        writer, count, observed = None, 0, set()
        try:
            for i in range(file.num_row_groups):
                batch = pl.from_arrow(file.read_row_group(i)).select('customer_id', 'article_id')
                for group in batch.partition_by('customer_id', maintain_order=True):
                    customer = group['customer_id'][0]
                    if customer in observed or group.height != context.retrieval.k:
                        raise ValueError('Incomplete or split candidate group')
                    observed.add(customer)
                features = context.transform(batch)
                scores = booster.predict(features.select(FEATURE_NAMES).to_numpy(), num_threads=2)
                if not np.isfinite(scores).all():
                    raise ValueError('Nonfinite ranker scores')
                result = batch.with_columns(pl.Series('score', scores)).sort(
                    ['customer_id', 'score', 'article_id'], descending=[False, True, False])
                result = result.with_columns(pl.col('article_id').cum_count().over('customer_id').cast(pl.UInt16).alias('rank'),
                    pl.lit('learned_' + model.pool).alias('source')).select('customer_id', 'rank', 'article_id', 'score', 'source')
                arrow = result.to_arrow()
                if writer is None:
                    writer = pq.ParquetWriter(path, arrow.schema, compression='zstd')
                writer.write_table(arrow)
                count += len(result)
        finally:
            if writer:
                writer.close()
        if observed != set(buyers) or count != candidates.files['ranking'].rows:
            raise ValueError('Ranker omitted candidate rows or buyers')
        final = root / 'ranking.parquet'
        path.replace(final)
        return publish(root, RankingArtifact(identity=identity, files={'ranking': QualityFile(
            path=final.name, sha256=sha256(final), rows=count, size_bytes=final.stat().st_size)},
            fold=snapshot.fold, method='R' + model.pool, customers=len(buyers)))
