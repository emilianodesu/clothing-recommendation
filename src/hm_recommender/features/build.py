"""Past-only profiles and Wednesday-aligned weekly examples; bounded by one week."""
from datetime import date, timedelta
import json
from pathlib import Path

import polars as pl

from hm_recommender.contracts import FeatureManifest
from hm_recommender.data.ingest import scan, write_partition
from hm_recommender.data.io import atomic_json, locked, sha256

VALIDATION = date(2020, 9, 9)
TEST = date(2020, 9, 16)
END = date(2020, 9, 23)
CONTENT = ['product_type_name', 'product_group_name', 'colour_group_name']


def aggregate(rows):
    return rows.group_by('customer_id').agg(pl.len().cast(pl.UInt64).alias('purchase_count'),
        pl.col('price').sum().alias('price_sum'),
        (pl.col('sales_channel_id') == 2).sum().cast(pl.UInt64).alias('online_count'),
        pl.col('t_dat').max().alias('last_purchase'))


def finish_profiles(state, cutoff):
    return state.with_columns(
        (pl.col('price_sum') / pl.col('purchase_count')).alias('mean_price'),
        (pl.col('online_count') / pl.col('purchase_count')).alias('online_share'),
        (pl.lit(cutoff) - pl.col('last_purchase')).dt.total_days().alias('recency_days'),
    ).drop('price_sum', 'online_count').sort('customer_id')


def build_features(dataset, data_as_of: date, output_dir: Path, *, vocabulary: Path | None = None) -> FeatureManifest:
    """Snapshot strictly before cutoff. Freeze training vocabulary for later snapshots."""
    with locked(output_dir):
        identity = {'dataset_id': dataset.dataset_id, 'source_sha256':dataset.source_sha256,
                    'data_as_of':str(data_as_of), 'feature_schema_version':1,
                    'vocabulary_sha256':sha256(vocabulary) if vocabulary else None}
        marker = output_dir / 'identity.json'
        if marker.exists() and json.loads(marker.read_text()) != identity:
            raise ValueError('Destino de features pertenece a otro corte o vocabulario')
        atomic_json(marker, identity)
        history = scan(dataset).filter(pl.col('t_dat') < data_as_of)
        profiles = finish_profiles(aggregate(history).collect(engine='streaming'), data_as_of)
        available = history.group_by('article_id').agg(pl.col('t_dat').min().alias('first_seen'), pl.len().alias('purchase_count')).collect(engine='streaming')
        catalog = available.join(scan(dataset, 'articles').select('article_id', *CONTENT).collect(), on='article_id', how='left').sort('article_id')
        if vocabulary:
            vocab = pl.read_parquet(vocabulary)
            if vocab.filter(pl.col('fit_before') > data_as_of).height:
                raise ValueError('El vocabulario fue ajustado con datos futuros')
        else:
            entries = []
            for field in ['article_id', *CONTENT]:
                # Index 0 = OOV; 1 = missing. Ordering is deterministic.
                entries.extend({'field':field, 'value':value, 'index':i + 2, 'fit_before':data_as_of}
                               for i, value in enumerate(sorted(catalog[field].drop_nulls().unique())))
            vocab = pl.DataFrame(entries, schema={'field':pl.String,'value':pl.String,'index':pl.UInt32,'fit_before':pl.Date})
        for field in ['article_id', *CONTENT]:
            mapping = vocab.filter(pl.col('field') == field).select(pl.col('value').alias(field), 'index')
            column = 'item_index' if field == 'article_id' else field + '_index'
            catalog = catalog.join(mapping, on=field, how='left').with_columns(
                pl.when(pl.col(field).is_null()).then(pl.lit(1)).otherwise(pl.col('index').fill_null(0)).cast(pl.UInt32).alias(column)).drop('index')
        files = {name:write_partition(frame.to_arrow(), output_dir, name + '.parquet') for name, frame in
                 [('profiles',profiles), ('catalog',catalog), ('vocabulary',vocab)]}
        manifest = FeatureManifest(dataset_id=dataset.dataset_id, data_as_of=data_as_of,
            counts={'profiles':profiles.height, 'catalog':catalog.height, 'vocabulary':vocab.height}, files=files)
        atomic_json(output_dir / 'manifest.json', manifest.model_dump(mode='json'))
        return manifest


def build_examples(dataset, output_dir: Path, *, validation_start=VALIDATION, test_start=TEST, test_end=END):
    """All weekly labels, including cold users/unavailable items, with past-only profiles.

    Every window is seven days and aligned to validation_start. The first can
    contain a partial observed history. We retain it and explicitly mark eligibility.
    """
    if test_start - validation_start != timedelta(days=7) or test_end - test_start != timedelta(days=7):
        raise ValueError('Se requieren ventanas consecutivas de siete días')
    with locked(output_dir):
        identity = {'dataset_id':dataset.dataset_id, 'source_sha256':dataset.source_sha256,
                    'validation_start':str(validation_start), 'test_start':str(test_start), 'test_end':str(test_end), 'version':1}
        marker = output_dir / 'identity.json'
        if marker.exists() and json.loads(marker.read_text()) != identity:
            raise ValueError('Destino de ejemplos incompatible')
        atomic_json(marker, identity)
        first_seen = scan(dataset).group_by('article_id').agg(pl.col('t_dat').min().alias('first_seen')).collect(engine='streaming')
        state = pl.DataFrame(schema={'customer_id':pl.String, 'purchase_count':pl.UInt64, 'price_sum':pl.Float64,
                                     'online_count':pl.UInt64, 'last_purchase':pl.Date})
        week = dataset.min_date - timedelta(days=(dataset.min_date - validation_start).days % 7)
        files, counts = [], dict(transactions=0, labels=0, eligible_train_labels=0, out_of_catalog_labels=0, cold_user_labels=0)
        while week < min(test_end, dataset.max_date + timedelta(days=1)):
            end = week + timedelta(days=7)
            current = scan(dataset).filter((pl.col('t_dat') >= week) & (pl.col('t_dat') < end)).collect(engine='streaming')
            if current.height:
                profiles = finish_profiles(state, week)
                split = 'train' if week < validation_start else ('validation' if week < test_start else 'test')
                labels = current.group_by('customer_id', 'article_id').agg(pl.len().alias('frequency'))
                examples = labels.join(profiles, on='customer_id', how='left').join(first_seen, on='article_id', how='left').with_columns(
                    pl.col('purchase_count').fill_null(0), pl.col('mean_price').fill_null(0.0), pl.col('online_share').fill_null(0.0),
                    pl.col('recency_days').fill_null(-1), (pl.col('first_seen') < week).alias('in_catalog'),
                    pl.lit(week).alias('week_start'), pl.lit(split).alias('split')).with_columns(
                    ((pl.col('purchase_count') > 0) & pl.col('in_catalog')).alias('eligible')).drop('first_seen').sort('customer_id', 'article_id')
                file = write_partition(examples.to_arrow(), output_dir, f'{split}/week={week}/examples.parquet')
                files.append(file.model_dump())
                counts['transactions'] += current.height
                counts['labels'] += examples.height
                counts['out_of_catalog_labels'] += examples.filter(~pl.col('in_catalog')).height
                counts['cold_user_labels'] += examples.filter(pl.col('purchase_count') == 0).height
                counts['eligible_train_labels'] += examples.filter(pl.col('eligible')).height if split == 'train' else 0
                counts[split + '_transactions'] = counts.get(split + '_transactions', 0) + current.height
                counts[split + '_labels'] = counts.get(split + '_labels', 0) + examples.height
                counts[split + '_out_of_catalog_labels'] = counts.get(split + '_out_of_catalog_labels', 0) + examples.filter(~pl.col('in_catalog')).height
                combined = pl.concat([state, aggregate(current)])
                state = combined.group_by('customer_id').agg(pl.col('purchase_count').sum(), pl.col('price_sum').sum(),
                    pl.col('online_count').sum(), pl.col('last_purchase').max())
            atomic_json(output_dir / 'progress.json', {'week_completed':str(week), 'counts':counts})
            week = end
        # Dates outside the evaluation horizon are explicitly reported, never silently discarded.
        counts['after_test_end_transactions'] = dataset.counts['transactions'] - counts['transactions']
        result = {'schema_version':1, **identity, 'counts':counts, 'files':files}
        atomic_json(output_dir / 'manifest.json', result)
        return result
