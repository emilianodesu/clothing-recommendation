from pathlib import Path

import pytest

from hm_recommender.config import load_config


def test_unknown_configuration_key_is_rejected(tmp_path: Path):
    path = tmp_path / "run.yaml"
    path.write_text("seed: 42\nbatch_szie: 4096\n")
    with pytest.raises(ValueError, match="batch_szie"):
        load_config(path)


def test_temporal_splits_must_be_ordered(tmp_path: Path):
    path = tmp_path / "run.yaml"
    path.write_text("validation_start: 2020-09-16\ntest_start: 2020-09-09\ntest_end: 2020-09-23\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_zero_batch_cannot_start_training(tmp_path: Path):
    path = tmp_path / "run.yaml"
    path.write_text("batch_size: 0\n")
    with pytest.raises(ValueError):
        load_config(path)
