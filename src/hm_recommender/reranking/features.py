"""Twenty numeric features at the candidate week's cutoff, never its labels."""
from datetime import timedelta

import numpy as np
import polars as pl

from hm_recommender.data.ingest import scan
from hm_recommender.data.io import sha256
from hm_recommender.graph.retrieve import RetrievalContext
from hm_recommender.quality.temporal import CONTENT, frame

FEATURE_NAMES = (
    'pair_count28', 'pair_count90', 'pair_recency90',
    'user_count28', 'user_count90', 'user_recency90',
    'item_count7', 'item_count28', 'item_count90', 'item_trend',
    'affinity_type90', 'affinity_group90', 'affinity_color90',
    'graph_score', 'history_rank', 'neighbor_rank', 'popular_rank',
    'in_history', 'in_neighbors', 'in_popular',
)
FEATURE_VERSION = 1


class FeatureContext:
    """Hold compact historical aggregates; transform at most a buyer batch at once."""

    def __init__(self, dataset, snapshot, graph, customers):
        if (dataset.source_sha256 != snapshot.identity['source_sha256'] or
                sha256(dataset._root / 'manifest.json') != snapshot.identity['dataset_manifest_sha256']):
            raise ValueError('Feature dataset differs from snapshot')
        self.retrieval = RetrievalContext(snapshot, graph, customers)
        self.node = {article: i for i, article in enumerate(self.retrieval.ids)}
        self.popular = {node: rank for rank, node in enumerate(self.retrieval.popular, 1)}
        cutoff = snapshot.fold.cutoff
        start28, start7 = cutoff - timedelta(days=28), cutoff - timedelta(days=7)
        tx = scan(dataset).filter((pl.col('t_dat') >= cutoff - timedelta(days=90)) &
                                  (pl.col('t_dat') < cutoff))
        self.items = tx.group_by('article_id').agg(
            (pl.col('t_dat') >= start7).sum().alias('item_count7'),
            (pl.col('t_dat') >= start28).sum().alias('item_count28'),
            pl.len().alias('item_count90')).collect(engine='streaming')
        user_tx = tx.filter(pl.col('customer_id').is_in(customers))
        self.pairs = user_tx.group_by('customer_id', 'article_id').agg(
            (pl.col('t_dat') >= start28).sum().alias('pair_count28'),
            pl.len().alias('pair_count90'),
            (pl.lit(cutoff) - pl.col('t_dat').max()).dt.total_days().alias('pair_recency90')
        ).collect(engine='streaming')
        self.users = user_tx.group_by('customer_id').agg(
            (pl.col('t_dat') >= start28).sum().alias('user_count28'),
            pl.len().alias('user_count90'),
            (pl.lit(cutoff) - pl.col('t_dat').max()).dt.total_days().alias('user_recency90')
        ).collect(engine='streaming')
        # Null and empty source attributes share the declared missing category.
        self.catalog = frame(snapshot, 'catalog').select('article_id', *CONTENT).with_columns(
            pl.col(CONTENT).fill_null(''))
        content = self.pairs.select('customer_id', 'article_id', 'pair_count90').join(
            self.catalog, on='article_id', how='left')
        self.affinities = []
        for category, name in zip(CONTENT, FEATURE_NAMES[10:13]):
            self.affinities.append(content.group_by('customer_id', category).agg(
                pl.col('pair_count90').sum().alias(name)))

    def transform(self, pairs):
        if pairs.columns != ['customer_id', 'article_id'] or pairs.is_empty():
            raise ValueError('Nonempty customer/article pairs required')
        if pairs.null_count().sum_horizontal().item() or pairs.unique().height != pairs.height:
            raise ValueError('Null or duplicate candidate pairs')
        if not set(pairs['article_id']) <= self.node.keys():
            raise ValueError('Candidate outside past catalog')
        numeric = np.empty((len(pairs), 7), dtype=np.float32)
        # Input ordering is preserved through joins and the graph lookup.
        for group in pairs.with_row_index('_row').partition_by('customer_id', maintain_order=True):
            customer = group['customer_id'][0]
            nodes, scores = self.retrieval.neighbor_scores(customer)
            scores = dict(zip(nodes, scores))
            positions = {node: rank for rank, node in enumerate(nodes[:200], 1)}
            history = {node: rank for rank, node in enumerate(self.retrieval.history(customer), 1)}
            for row, article in group.select('_row', 'article_id').iter_rows():
                node = self.node[article]
                h, g, p = history.get(node, 201), positions.get(node, 201), self.popular.get(node, 201)
                numeric[row] = scores.get(node, 0), h, g, p, h <= 200, g <= 200, p <= 200
        result = pairs.with_row_index('_row').join(self.pairs, on=['customer_id', 'article_id'], how='left')
        result = result.join(self.users, on='customer_id', how='left').join(self.items, on='article_id', how='left')
        result = result.join(self.catalog, on='article_id', how='left')
        for category, affinity in zip(CONTENT, self.affinities):
            result = result.join(affinity, on=['customer_id', category], how='left')
        result = result.sort('_row').with_columns(
            pl.col('pair_recency90', 'user_recency90').fill_null(91),
            pl.col('pair_count28', 'pair_count90', 'user_count28', 'user_count90',
                   'item_count7', 'item_count28', 'item_count90', *FEATURE_NAMES[10:13]).fill_null(0),
        ).with_columns(
            (pl.col('item_count7') / (pl.col('item_count28') / 4 + 1)).alias('item_trend'),
            *[pl.when(pl.col('user_count90') > 0).then(pl.col(name) / pl.col('user_count90'))
              .otherwise(0).alias(name) for name in FEATURE_NAMES[10:13]],
            *[pl.Series(name, numeric[:, i]) for i, name in enumerate(FEATURE_NAMES[13:])],
        ).select('customer_id', 'article_id', *FEATURE_NAMES).with_columns(pl.col(FEATURE_NAMES).cast(pl.Float32))
        if not np.isfinite(result.select(FEATURE_NAMES).to_numpy()).all():
            raise ValueError('Nonfinite ranker features')
        return result
