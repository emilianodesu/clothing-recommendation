"""Versioned interchange types. Artifact paths are relative to their manifest."""
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[int, Field(ge=0, strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactFile(Contract):
    path: str
    sha256: SHA256
    rows: Count | None = None

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or value == ".":
            raise ValueError("Artifact paths must be safe, relative POSIX paths")
        return value


class DatasetManifest(Contract):
    _root: Path | None = PrivateAttr(default=None)
    schema_version: Literal[1] = 1
    dataset_id: str
    source_sha256: SHA256
    min_date: date
    max_date: date
    counts: dict[str, Count]
    partitions: list[ArtifactFile]


class FeatureManifest(Contract):
    schema_version: Literal[1] = 1
    dataset_id: str
    data_as_of: date
    feature_schema_version: Literal[1] = 1
    counts: dict[str, Count]
    files: dict[str, ArtifactFile]


class TrainingResult(Contract):
    run_id: str
    checkpoint_path: str
    config: dict
    seed: Count
    epochs: Count
    rows_consumed: Count
    device: Literal["cpu"] = "cpu"
    duration_seconds: float = Field(ge=0)
    peak_memory_bytes: Count


class EvaluationReport(Contract):
    period_start: date
    period_end: date
    evaluated_users: Count
    excluded_users: Count
    map_at_12: float = Field(ge=0, le=1)
    ndcg_at_12: float = Field(ge=0, le=1)
    recall_at_200: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    segments: dict[str, dict]
    popularity: dict[str, float]


class ReleaseManifest(Contract):
    schema_version: Literal[1] = 1
    release_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,96}$")
    commit: str
    source_sha256: SHA256
    data_as_of: date
    feature_schema_version: Literal[1] = 1
    embedding_dim: Literal[64] = 64
    metric: Literal["dot"] = "dot"
    files: dict[str, ArtifactFile]
    results: EvaluationReport
    software_versions: dict[str, str]


class PublicationResult(Contract):
    release_id: str
    collection: str
    vector_count: Count
    queries_verified: Count
    status: Literal["prepared", "active"]


class EventInput(Contract):
    event_id: str = Field(min_length=1, max_length=128)
    customer_id: str = Field(min_length=1, max_length=128)
    article_id: str = Field(min_length=1, max_length=128)
    event_type: Literal["purchase", "view", "click"]
    event_time: datetime
    origin: Literal["historical", "synthetic", "simulation"]
    request_id: str | None = None


class RecommendationItem(Contract):
    article_id: str
    score: float


class RecommendationResponse(Contract):
    request_id: str
    release_id: str
    features_timestamp: datetime
    fallback: bool
    items: list[RecommendationItem]
