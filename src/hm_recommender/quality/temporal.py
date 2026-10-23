"""Past-only snapshots, separate labels, and verified local artifacts."""
from datetime import timedelta
import json
from pathlib import Path

import polars as pl
from hm_recommender.data.ingest import scan, write_partition
from hm_recommender.data.io import atomic_json, locked, safe_file, sha256
from hm_recommender.quality.contracts import FoldManifest, QualityConfig, QualityFile, digest

CONTENT=('product_type_name','product_group_name','colour_group_name')


def file_entry(root, name, table):
    entry=write_partition(table.to_arrow(),root,name+'.parquet')
    return QualityFile(**entry.model_dump(),size_bytes=safe_file(root,entry.path).stat().st_size)


def verified_path(artifact,name):
    if artifact._root is None: raise ValueError('Artifact root not bound')
    entry=artifact.files[name]; path=safe_file(artifact._root,entry.path)
    if path.stat().st_size!=entry.size_bytes or sha256(path)!=entry.sha256:
        raise ValueError('Quality artifact integrity mismatch')
    return path


def frame(artifact,name):
    table=pl.read_parquet(verified_path(artifact,name))
    if table.height!=artifact.files[name].rows: raise ValueError('Quality artifact row count mismatch')
    return table


def publish(root,artifact):
    artifact._root=Path(root)
    atomic_json(root/'manifest.json',artifact.model_dump(mode='json'))
    return artifact


def existing(root, identity, model):
    marker=root/'identity.json'
    if marker.exists() and json.loads(marker.read_text())!=identity:
        raise ValueError('Destination belongs to another quality identity')
    atomic_json(marker,identity)
    if (root/'manifest.json').exists():
        saved=model.model_validate_json((root/'manifest.json').read_text()); saved._root=root
        if saved.identity!=identity: raise ValueError('Manifest identity mismatch')
        for name in saved.files: verified_path(saved,name)
        return saved
    # A partial directory cannot silently reuse shards with different contents.
    if list(root.glob('*.parquet')): raise ValueError('Incomplete artifact; preserve it and use a new run')


def build_snapshot(dataset, fold, output_dir, config, *, artifact_type=FoldManifest):
    artifact_type.model_fields['fold'].annotation.model_validate(fold.model_dump())
    if config.model_dump()!=QualityConfig().model_dump(): raise ValueError('Fixed quality protocol required')
    root=Path(output_dir)
    identity={'dataset_id':dataset.dataset_id,'source_sha256':dataset.source_sha256,
        'dataset_manifest_sha256':sha256(dataset._root/'manifest.json'),
        'fold':fold.model_dump(mode='json'),'config_sha256':digest(config.model_dump()),'feature_version':1}
    with locked(root):
        saved=existing(root,identity,artifact_type)
        if saved: return saved
        past=scan(dataset).filter(pl.col('t_dat')<fold.cutoff)
        catalog=(past.group_by('article_id').agg(pl.col('t_dat').min().alias('first_seen'))
            .join(scan(dataset,'articles').select('article_id',*CONTENT),on='article_id',how='left')
            .sort('article_id').collect(engine='streaming'))
        if catalog.is_empty(): raise ValueError('No eligible catalog before cutoff')
        profiles=(past.group_by('customer_id').agg(pl.col('t_dat').max().alias('last_purchase'),pl.len().alias('purchase_count'))
            .sort('customer_id').collect(engine='streaming'))
        profiles=profiles.with_columns(pl.when(pl.col('last_purchase')>=fold.cutoff-timedelta(days=28)).then(pl.lit('recent28d'))
            .when(pl.col('last_purchase')>=fold.cutoff-timedelta(days=90)).then(pl.lit('history29_90d'))
            .otherwise(pl.lit('older_history')).alias('segment'))
        history=history_table(past,fold.cutoff)
        popularity=popular_table(past,catalog,fold.cutoff)
        tables={'catalog':catalog,'profiles':profiles,'histories':history,'popularity':popularity}
        files={name:file_entry(root,name,table) for name,table in tables.items()}
        return publish(root,artifact_type(identity=identity,files=files,fold=fold,counts={name:table.height for name,table in tables.items()}))


def history_table(transactions,cutoff,days=28):
    return (transactions.filter((pl.col('t_dat')>=cutoff-timedelta(days=days)) & (pl.col('t_dat')<cutoff))
        .group_by('customer_id','article_id').agg(pl.col('t_dat').max().alias('last_purchase'),pl.len().alias('frequency'))
        .sort(['customer_id','last_purchase','frequency','article_id'],descending=[False,True,True,False])
        .collect(engine='streaming'))


def popular_table(transactions,catalog,cutoff,days=7):
    counts=(transactions.filter((pl.col('t_dat')>=cutoff-timedelta(days=days)) & (pl.col('t_dat')<cutoff))
        .group_by('article_id').len().collect(engine='streaming'))
    return (catalog.select('article_id').join(counts,on='article_id',how='left').with_columns(pl.col('len').fill_null(0).alias('frequency'))
        .drop('len').sort(['frequency','article_id'],descending=[True,False]))


def read_labels(dataset,fold):
    # Distinct labels include unseen/unavailable items; annotations only for evaluation.
    tx=scan(dataset)
    labels=tx.filter((pl.col('t_dat')>=fold.cutoff)&(pl.col('t_dat')<fold.label_end)).select('customer_id','article_id').unique()
    seen=(tx.filter(pl.col('t_dat')<fold.cutoff).select('customer_id','article_id')
        .join(labels,on=['customer_id','article_id'],how='semi').unique().with_columns(pl.lit(True).alias('seen_before')))
    return labels.join(seen,on=['customer_id','article_id'],how='left').with_columns(pl.col('seen_before').fill_null(False)).sort('customer_id','article_id')
