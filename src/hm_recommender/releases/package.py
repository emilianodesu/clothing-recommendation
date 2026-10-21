"""Past-only state, strict verification and atomic directory publication."""
from datetime import date, timedelta
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import tempfile
import fcntl

import polars as pl

from hm_recommender.data.ingest import load_dataset, scan
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.quality.contracts import digest
from hm_recommender.quality.temporal import CONTENT, history_table, popular_table
from hm_recommender.releases.contracts import HistoryReleaseManifest, HistoryRules, ReleaseFile

TABLES={'catalog','customers','histories','popularity'}
JSON_FILES={'rules','validation-reference'}


def configuration():
    path=Path(__file__).resolve().parents[3]/'configs/releases/history-v1.json'
    if not path.is_file():path=Path('/opt/hmrec/configs/releases/history-v1.json')
    return json.loads(path.read_text())


def release_identity(payload):
    return 'h-'+digest({k:v for k,v in payload.items() if k!='release_id'})


def validation_reference(config):
    return {'selection':config['selection'],'baseline':config['baseline'],'diagnostic':config['diagnostic'],
        'validation_cutoff':'2020-09-09','metrics_measured_for_this_package':False}


def verify_history_release(directory: Path) -> HistoryReleaseManifest:
    root=Path(directory)
    if root.is_symlink() or not root.is_dir():raise ValueError('Release must be a regular directory')
    expected={name+'.parquet' for name in TABLES}|{name+'.json' for name in JSON_FILES}|{'manifest.json'}
    if {p.name for p in root.iterdir()}!=expected:raise ValueError('Incomplete or unexpected release inventory')
    if any(p.is_symlink() or not p.is_file() for p in root.iterdir()):raise ValueError('Release links are forbidden')
    manifest=HistoryReleaseManifest.model_validate_json((root/'manifest.json').read_text())
    if set(manifest.files)!=TABLES|JSON_FILES:raise ValueError('Invalid release inventory')
    if manifest.release_id!=release_identity(manifest.model_dump(mode='json')):raise ValueError('Release identity mismatch')
    config=configuration()
    if manifest.selection_archive_sha256!=config['selection']['archive_sha256']:raise ValueError('Selection differs from frozen Q4')
    for name,entry in manifest.files.items():
        filename=name+('.parquet' if name in TABLES else '.json')
        if entry.path!=filename:raise ValueError('Release paths must be canonical relative filenames')
        path=root/filename
        if path.stat().st_size!=entry.size_bytes or sha256(path)!=entry.sha256:raise ValueError('Release file integrity mismatch')
        if name in TABLES:
            if pl.scan_parquet(path).select(pl.len()).collect().item()!=entry.rows:raise ValueError('Release row count mismatch')
        elif entry.rows is not None:raise ValueError('JSON row count must be absent')
    if json.loads((root/'rules.json').read_text())!=manifest.rules.model_dump():raise ValueError('Rules mismatch')
    if json.loads((root/'validation-reference.json').read_text())!=validation_reference(config):
        raise ValueError('Validation provenance mismatch')
    _verify_tables(root,manifest.data_as_of)
    manifest._root=root
    return manifest


def _verify_tables(root,cutoff):
    catalog=pl.read_parquet(root/'catalog.parquet')
    customers=pl.read_parquet(root/'customers.parquet')
    history=pl.read_parquet(root/'histories.parquet')
    popular=pl.read_parquet(root/'popularity.parquet')
    schemas=[(catalog,{'article_id':pl.String,'first_seen':pl.Date,**{c:pl.String for c in CONTENT}}),
        (customers,{'customer_id':pl.String}),
        (history,{'customer_id':pl.String,'article_id':pl.String,'last_purchase':pl.Date,'frequency':pl.UInt32}),
        (popular,{'article_id':pl.String,'frequency':pl.UInt32,'rank':pl.UInt32})]
    for table,schema in schemas:
        if dict(table.schema)!=schema:raise ValueError('Unexpected release table schema')
    for table,keys in [(catalog,['article_id']),(customers,['customer_id']),(history,['customer_id','article_id']),(popular,['article_id'])]:
        if table.select(keys).n_unique()!=table.height:raise ValueError('Duplicate release keys')
        if any(table[k].null_count() or (table[k]=='').any() for k in keys):raise ValueError('Missing release keys')
    if catalog.is_empty() or customers.is_empty():raise ValueError('Empty past population')
    if not catalog.equals(catalog.sort('article_id')) or not customers.equals(customers.sort('customer_id')):
        raise ValueError('Unordered population')
    if (catalog['first_seen'].null_count() or catalog['first_seen'].max()>=cutoff
        or history['last_purchase'].null_count() or history['frequency'].null_count()
        or popular['frequency'].null_count()):raise ValueError('Invalid release timestamps/counts')
    if history.height and (history['last_purchase'].min()<cutoff-timedelta(days=28)
        or history['last_purchase'].max()>=cutoff or history['frequency'].min()<1):raise ValueError('Invalid history window')
    if not history.equals(history.sort(['customer_id','last_purchase','frequency','article_id'],descending=[False,True,True,False])):
        raise ValueError('Unordered histories')
    if history.join(customers,on='customer_id',how='anti').height or history.join(catalog,on='article_id',how='anti').height:
        raise ValueError('Unbound history keys')
    if popular.height!=catalog.height or popular.join(catalog,on='article_id',how='anti').height:
        raise ValueError('Popularity must cover the entire available catalog')
    if not popular.equals(popular.sort(['frequency','article_id'],descending=[True,False])) or popular['rank'].to_list()!=list(range(1,len(popular)+1)):
        raise ValueError('Unordered popularity')


def build_history_release(dataset, data_as_of: date, output_dir: Path, *, evidence_archive: Path | None=None) -> HistoryReleaseManifest:
    if type(data_as_of) is not date:raise ValueError('Explicit date cutoff required')
    commit=os.environ.get('HMREC_SOURCE_COMMIT','')
    import re
    if not re.fullmatch('[a-f0-9]{40}',commit):raise ValueError('HMREC_SOURCE_COMMIT must identify the build')
    if dataset._root is None:raise ValueError('Dataset root required')
    verified=load_dataset(dataset._root)
    if verified!=dataset:raise ValueError('Dataset manifest changed')
    if not dataset.min_date<data_as_of<=dataset.max_date+timedelta(days=1):raise ValueError('Cutoff outside dataset coverage')
    config=configuration();selection_verified=False
    if evidence_archive is not None:
        from hm_recommender.releases.evidence import verify_selection
        verify_selection(evidence_archive,config);selection_verified=True
    identity={'commit':commit,'source_sha256':dataset.source_sha256,
        'dataset_manifest_sha256':sha256(dataset._root/'manifest.json'),'data_as_of':data_as_of}
    root=Path(output_dir);root.parent.mkdir(parents=True,exist_ok=True)
    with (root.parent/('.'+root.name+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if root.exists() or root.is_symlink():
            saved=verify_history_release(root)
            if any(getattr(saved,k)!=v for k,v in identity.items()) or saved.selection_archive_verified!=selection_verified:
                raise ValueError('Destination belongs to another release')
            return saved
        temporary=Path(tempfile.mkdtemp(prefix='.'+root.name+'.partial-',dir=root.parent))
        past=scan(dataset).filter(pl.col('t_dat')<data_as_of)
        catalog=(past.group_by('article_id').agg(pl.col('t_dat').min().alias('first_seen'))
            .join(scan(dataset,'articles').select('article_id',*CONTENT),on='article_id',how='left')
            .sort('article_id').collect(engine='streaming'))
        customers=past.select('customer_id').unique().sort('customer_id').collect(engine='streaming')
        history=history_table(past,data_as_of)
        popular=popular_table(past,catalog,data_as_of).with_row_index('rank',offset=1).select('article_id','frequency','rank')
        files={}
        for name,table in [('catalog',catalog),('customers',customers),('histories',history),('popularity',popular)]:
            path=temporary/(name+'.parquet');table.write_parquet(path,compression='zstd',row_group_size=65536)
            files[name]=ReleaseFile(path=path.name,rows=len(table),sha256=sha256(path),size_bytes=path.stat().st_size)
        for name,value in [('rules',HistoryRules().model_dump()),('validation-reference',validation_reference(config))]:
            path=temporary/(name+'.json');atomic_json(path,value)
            files[name]=ReleaseFile(path=path.name,sha256=sha256(path),size_bytes=path.stat().st_size)
        manifest=HistoryReleaseManifest(release_id='h-'+'0'*64,**identity,files=files,
            selection_archive_sha256=config['selection']['archive_sha256'],selection_archive_verified=selection_verified,
            software_versions={'python':platform.python_version(),**{p:version(p) for p in ('polars','pyarrow','pydantic')}})
        manifest.release_id=release_identity(manifest.model_dump(mode='json'))
        atomic_json(temporary/'manifest.json',manifest.model_dump(mode='json'))
        verify_history_release(temporary)
        for path in temporary.iterdir():
            with path.open('rb') as stream:os.fsync(stream.fileno())
        fd=os.open(temporary,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        temporary.rename(root)
        fd=os.open(root.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        manifest._root=root
        return manifest
