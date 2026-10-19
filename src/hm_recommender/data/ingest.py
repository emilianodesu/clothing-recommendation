"""Streaming CSV -> monthly Parquet shards with recoverable atomic publication."""
from datetime import date
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

import polars as pl
import pyarrow as pa
import pyarrow.csv as csv
import pyarrow.parquet as pq

from hm_recommender.contracts import ArtifactFile, DatasetManifest
from hm_recommender.data.io import atomic_json, locked, safe_file, sha256
from hm_recommender.data.validate import SCHEMAS, HM_SOURCE, validate_source

BLOCK_SIZE = 16 * 1024 * 1024
LAYOUT = {'version': 1, 'csv_block_bytes': BLOCK_SIZE}


def write_partition(table, root, relative):
    path = safe_file(root, relative)
    receipt = path.with_suffix('.json')
    if path.exists() and receipt.exists():
        saved = ArtifactFile.model_validate_json(receipt.read_text())
        if saved.path != relative or saved.rows != table.num_rows or sha256(path) != saved.sha256:
            raise ValueError('Fallo de integridad en partición existente; conservar para diagnóstico')
        return saved
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.parquet.partial')
    pq.write_table(table, temporary, compression='zstd', row_group_size=65536)
    with temporary.open('rb') as stream:
        import os
        os.fsync(stream.fileno())
    file = ArtifactFile(path=relative, rows=table.num_rows, sha256=sha256(temporary))
    temporary.replace(path)
    atomic_json(receipt, file.model_dump())
    return file


def load_dataset(directory: Path, verify=True) -> DatasetManifest:
    result = DatasetManifest.model_validate_json((directory / 'manifest.json').read_text())
    paths = [p.path for p in result.partitions]
    if len(paths) != len(set(paths)):
        raise ValueError('Particiones duplicadas')
    for entry in result.partitions:
        path = safe_file(directory, entry.path)
        if verify and (not path.is_file() or sha256(path) != entry.sha256
                       or pq.ParquetFile(path).metadata.num_rows != entry.rows):
            raise ValueError('Fallo de integridad en dataset')
    result._root = directory.resolve()
    return result


def scan(dataset, table='transactions'):
    if dataset._root is None:
        raise ValueError('Cargar el manifiesto con load_dataset para resolver su raíz')
    files = [str(safe_file(dataset._root, p.path)) for p in dataset.partitions if p.path.startswith(table + '/')]
    if not files:
        raise ValueError('Faltan particiones de ' + table)
    return pl.scan_parquet(files)


def validate_relations(dataset):
    for table, key in [('articles','article_id'), ('customers','customer_id'), ('sample_submission','customer_id')]:
        stats = scan(dataset, table).select(pl.len().alias('n'), pl.col(key).n_unique().alias('unique'),
            pl.col(key).is_null().sum().alias('nulls'), (pl.col(key) == '').sum().alias('empty')).collect().row(0, named=True)
        if stats['n'] != stats['unique'] or stats['nulls'] or stats['empty']:
            raise ValueError('Claves inválidas en ' + table)
    transactions = scan(dataset)
    for table, key in [('articles','article_id'), ('customers','customer_id')]:
        count = transactions.select(key).unique().join(scan(dataset, table).select(key), on=key, how='anti').select(pl.len()).collect(engine='streaming').item()
        if count:
            raise ValueError('Referencias inexistentes: ' + table)
    missing = scan(dataset, 'sample_submission').select('customer_id').join(scan(dataset, 'customers').select('customer_id'), on='customer_id', how='anti').select(pl.len()).collect(engine='streaming').item()
    if missing:
        raise ValueError('Clientes desconocidos en sample_submission')


def ingest(source_zip: Path, output_dir: Path, *, expected=None) -> DatasetManifest:
    source_zip, output_dir = Path(source_zip), Path(output_dir)
    with locked(output_dir):
        source = validate_source(source_zip, mode='full', expected=expected)
        identity = {'source': source, 'layout': LAYOUT}
        identity_path = output_dir / 'source.json'
        if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
            raise ValueError('El destino pertenece a otra fuente o versión; usar otro directorio')
        atomic_json(identity_path, identity)
        if (output_dir / 'manifest.json').exists():
            return load_dataset(output_dir)
        if shutil.disk_usage(output_dir).free < 512 * 1024 * 1024:
            raise ValueError('Espacio insuficiente: se requiere al menos 512 MiB libres para comenzar')
        files, counts, earliest, latest = [], {}, None, None
        with ZipFile(source_zip) as bundle:
            for name, columns in SCHEMAS.items():
                table_name = 'transactions' if name == 'transactions_train.csv' else name[:-4]
                types = {c: pa.string() for c in columns}
                if table_name == 'transactions':
                    types.update(t_dat=pa.date32(), price=pa.float64(), sales_channel_id=pa.int8())
                counts[table_name] = 0
                with bundle.open(name) as stream:
                    reader = csv.open_csv(stream, read_options=csv.ReadOptions(block_size=BLOCK_SIZE),
                        parse_options=csv.ParseOptions(newlines_in_values=True),
                        convert_options=csv.ConvertOptions(column_types=types, strings_can_be_null=True, null_values=['']))
                    for index, batch in enumerate(reader):
                        frame = pl.from_arrow(batch)
                        if table_name == 'transactions':
                            if frame.null_count().sum_horizontal().item() or frame.filter(
                                (~pl.col('price').is_finite()) | (pl.col('price') < 0) | (~pl.col('sales_channel_id').is_in([1, 2]))).height:
                                raise ValueError('Transacciones con valores inválidos')
                            start, end = frame['t_dat'].min(), frame['t_dat'].max()
                            earliest = min(earliest, start) if earliest else start
                            latest = max(latest, end) if latest else end
                            for (month,), part in frame.with_columns(pl.col('t_dat').dt.strftime('%Y-%m').alias('_month')).partition_by('_month', as_dict=True).items():
                                relative = f'transactions/month={month}/part-{index:05d}.parquet'
                                files.append(write_partition(part.drop('_month').to_arrow(), output_dir, relative))
                        else:
                            files.append(write_partition(pa.Table.from_batches([batch]), output_dir, f'{table_name}/part-{index:05d}.parquet'))
                        counts[table_name] += batch.num_rows
                        atomic_json(output_dir / 'progress.json', {'stage':'ingest', 'table':table_name, 'rows':counts[table_name], 'completed_shards':len(files)})
                if counts[table_name] == 0:
                    raise ValueError('Tabla vacía: ' + table_name)
        if expected is not None and counts != expected['rows']:
            raise ValueError('Los conteos no concuerdan con H&M completo')
        manifest = DatasetManifest(dataset_id='hm-' + source['source_sha256'][:16],
            source_sha256=source['source_sha256'], min_date=earliest, max_date=latest, counts=counts, partitions=files)
        manifest._root = output_dir.resolve()
        validate_relations(manifest)
        atomic_json(output_dir / 'manifest.json', manifest.model_dump(mode='json'))
        atomic_json(output_dir / 'progress.json', {'stage':'complete', 'counts':counts, 'completed_shards':len(files)})
        return manifest
