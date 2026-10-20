"""Verified temporal inputs. Only one weekly label partition is materialized."""
import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from hm_recommender.contracts import ArtifactFile, FeatureManifest
from hm_recommender.data.io import safe_file, sha256
from hm_recommender.features.build import CONTENT, VALIDATION, TEST


class TrainConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seed: int = Field(default=42, ge=0)
    batch_size: int = Field(default=4096, ge=1, le=65536)
    epochs: int = Field(default=1, ge=1, le=20)
    learning_rate: float = Field(default=.05, gt=0, le=1)
    negatives: int = Field(default=8, ge=1, le=64)
    temperature: float = Field(default=.1, gt=0)
    max_users: int = Field(default=0, ge=0)
    eval_users: int = Field(default=0, ge=0)
    checkpoint_steps: int = Field(default=250, ge=1)
    min_free_bytes: int = Field(default=10 * 2**30, ge=0)
    demo: bool = False


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def numeric(frame):
    # Fixed transforms; no statistics fitted on a validation/test population.
    values = frame.select('purchase_count', 'mean_price', 'online_share', 'recency_days').to_numpy().astype(np.float32)
    values[:, 0] = np.log1p(values[:, 0]) / 10
    values[:, 3] = np.log1p(np.maximum(values[:, 3], 0)) / 10
    if not np.isfinite(values).all():
        raise ValueError('Non-finite model input')
    return np.ascontiguousarray(values)


def ids(values):
    return np.asarray(values, dtype='S64')


def select_users(values, count, seed):
    if not count or len(values) <= count:
        return values
    order = sorted(range(len(values)), key=lambda i: hashlib.sha256(str(seed).encode() + b':' + bytes(values[i])).digest())
    return np.sort(values[np.asarray(order[:count])])


def lookup(vocabulary, values):
    positions = np.searchsorted(vocabulary, values)
    valid = positions < len(vocabulary)
    valid[valid] &= vocabulary[positions[valid]] == values[valid]
    return np.where(valid, positions + 1, 0).astype(np.int64)


def load_features(root):
    root = Path(root)
    manifest = FeatureManifest.model_validate_json((root / 'manifest.json').read_text())
    manifest._root = root
    for entry in manifest.files.values():
        path = safe_file(root, entry.path)
        if sha256(path) != entry.sha256 or pl.scan_parquet(path).select(pl.len()).collect().item() != entry.rows:
            raise ValueError('Feature integrity mismatch: ' + entry.path)
    return manifest


class Inputs:
    def __init__(self, features, config):
        self.root = features._root
        if self.root is None or features.data_as_of != VALIDATION:
            raise ValueError('T3 requires verified training features at 2020-09-09')
        self.config = config
        self.catalog = pl.read_parquet(self.root / features.files['catalog'].path).sort('article_id')
        self.profiles = pl.read_parquet(self.root / features.files['profiles'].path).sort('customer_id')
        self.users = select_users(ids(self.profiles['customer_id']), config.max_users, config.seed)
        self.article_ids = np.asarray(self.catalog['article_id'].to_list())
        self.attributes = self.catalog.select('item_index', *[field + '_index' for field in CONTENT]).to_numpy().astype(np.int64)
        self.item_sizes = (self.attributes.max(0) + 1).tolist()
        self.first_seen = self.catalog['first_seen'].to_numpy()
        self.article_map = {value: i for i, value in enumerate(self.article_ids)}
        self.examples_root = self.root.parent / 'examples'
        self.manifest = json.loads((self.examples_root / 'manifest.json').read_text())
        if (self.manifest['dataset_id'] != features.dataset_id or self.manifest['validation_start'] != str(VALIDATION)
                or self.manifest['test_start'] != str(TEST) or self.manifest.get('schema_version') != 1):
            raise ValueError('Incompatible temporal examples')
        self.files = []
        for value in self.manifest['files']:
            entry = ArtifactFile.model_validate(value)
            path = safe_file(self.examples_root, entry.path)
            if sha256(path) != entry.sha256 or pl.scan_parquet(path).select(pl.len()).collect().item() != entry.rows:
                raise ValueError('Example integrity mismatch: ' + entry.path)
            self.files.append((entry.path, path))
        self.train_files = sorted(path for name, path in self.files if name.startswith('train/'))
        self.validation_files = sorted(path for name, path in self.files if name.startswith('validation/'))
        digest = hashlib.sha256(ids(self.profiles['customer_id']).tobytes())
        digest.update(numeric(self.profiles).astype('<f4').tobytes())
        digest.update(self.attributes.astype('<i8').tobytes())
        self.identity = {'feature_manifest_sha256': sha256(self.root / 'manifest.json'),
                         'examples_manifest_sha256': sha256(self.examples_root / 'manifest.json'),
                         'source_sha256': self.manifest['source_sha256'],
                         'model_input_sha256': digest.hexdigest(), 'config': config.model_dump()}
        self.identity['sha256'] = canonical(self.identity)

    def week(self, path):
        frame = pl.read_parquet(path)
        frame = frame.filter(pl.col('eligible'))
        indices = lookup(self.users, ids(frame['customer_id']))
        return frame.filter(pl.Series(indices > 0))
