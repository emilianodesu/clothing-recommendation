from importlib import import_module
from zipfile import ZipFile

import polars as pl
import pytest

from hm_recommender.data.ingest import ingest, load_dataset
from hm_recommender.data.synthetic import make_zip
from hm_recommender.data.validate import validate_source


def test_ids_preserve_leading_zeros(tmp_path):
    source = make_zip(tmp_path / 'source.zip')
    result = ingest(source, tmp_path / 'out')
    assert result.counts['transactions'] == 7  # Including two identical purchases.
    assert result.min_date.isoformat() == '2020-08-31'
    assert result.max_date.isoformat() == '2020-09-16'
    frames = [pl.read_parquet(tmp_path / 'out' / p.path) for p in result.partitions if p.path.startswith('transactions/')]
    table = pl.concat(frames)
    assert set(table['article_id']) == {'00a', '00b', '00c'}
    assert table.filter(pl.col('t_dat') == pl.date(2020, 9, 8)).height == 2
    assert load_dataset(tmp_path / 'out').source_sha256 == result.source_sha256


def test_ingest_resume_after_partial_write(tmp_path, monkeypatch):
    module = import_module('hm_recommender.data.ingest')
    source = make_zip(tmp_path / 'source.zip')
    original = module.write_partition
    calls = 0
    def disk_full(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError(28, 'No space left on device')
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'write_partition', disk_full)
    with pytest.raises(OSError):
        ingest(source, tmp_path / 'out')
    assert not (tmp_path / 'out/manifest.json').exists()
    with pytest.raises((ValueError, FileNotFoundError)):
        load_dataset(tmp_path / 'out')
    monkeypatch.setattr(module, 'write_partition', original)
    resumed = ingest(source, tmp_path / 'out')
    clean = ingest(source, tmp_path / 'clean')
    assert resumed.model_dump() == clean.model_dump()
    assert ingest(source, tmp_path / 'out').model_dump() == resumed.model_dump()


def test_corrupt_partition_and_different_source_are_rejected(tmp_path):
    source = make_zip(tmp_path / 'source.zip')
    result = ingest(source, tmp_path / 'out')
    (tmp_path / 'out' / result.partitions[0].path).write_bytes(b'broken')
    with pytest.raises(ValueError, match='integridad'):
        load_dataset(tmp_path / 'out')
    with pytest.raises(ValueError):
        ingest(make_zip(tmp_path / 'different.zip', future=True), tmp_path / 'out')


@pytest.mark.parametrize('name', ['../escape.csv', '/absolute.csv', 'articles.csv'])
def test_unsafe_or_duplicate_zip_rejected(tmp_path, name):
    source = make_zip(tmp_path / 'source.zip')
    with ZipFile(source, 'a') as z:
        z.writestr(name, 'bad')
    with pytest.raises(ValueError):
        validate_source(source, mode='quick', expected=None)


def test_hm_validation_never_accepts_synthetic_as_full_dataset(tmp_path):
    with pytest.raises(ValueError):
        validate_source(make_zip(tmp_path / 'source.zip'), mode='full')
