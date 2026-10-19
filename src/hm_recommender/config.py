"""Validated run settings; dates are dataset dates, never host time."""
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Literal["hm", "synthetic"] = "hm"
    seed: int = Field(default=42, ge=0, strict=True)
    batch_size: int = Field(default=4096, ge=1, le=65536, strict=True)
    embedding_dim: Literal[64] = 64
    epochs: int = Field(default=1, ge=1, le=100, strict=True)
    learning_rate: float = Field(default=0.001, gt=0, le=1)
    workers: int = Field(default=0, ge=0, le=8, strict=True)
    validation_start: date = date(2020, 9, 9)
    test_start: date = date(2020, 9, 16)
    test_end: date = date(2020, 9, 23)
    data_dir: Path = Path("/data")
    artifacts_dir: Path = Path("/artifacts")

    @model_validator(mode="after")
    def temporal_order(self):
        if not self.validation_start < self.test_start < self.test_end:
            raise ValueError("Los cortes deben cumplir validation_start < test_start < test_end")
        return self


def load_config(path: Path) -> dict:
    with path.open() as stream:
        payload = yaml.safe_load(stream)
    if not isinstance(payload, dict):
        raise ValueError("La configuración debe ser un objeto YAML")
    return RunConfig.model_validate(payload).model_dump(mode="json")
