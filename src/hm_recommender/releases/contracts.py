from datetime import date
from pathlib import Path
from typing import Literal
from pydantic import Field, PrivateAttr
from hm_recommender.contracts import Contract, ArtifactFile, SHA256, Count


class ReleaseFile(ArtifactFile):
    size_bytes: Count


class HistoryRules(Contract):
    strategy: Literal['recent_then_popular']='recent_then_popular'
    protocol: Literal['recent-history-v1']='recent-history-v1'
    history_days: Literal[28]=28
    popularity_days: Literal[7]=7
    candidate_limit: Literal[200]=200
    history_order: Literal['last_purchase DESC, frequency DESC, article_id ASC']='last_purchase DESC, frequency DESC, article_id ASC'
    popularity_order: Literal['frequency DESC, article_id ASC']='frequency DESC, article_id ASC'
    events: Literal['strictly_before_cutoff']='strictly_before_cutoff'
    duplicates: Literal['first_occurrence']='first_occurrence'
    exclude_purchased: Literal[False]=False


class HistoryReleaseManifest(Contract):
    _root: Path | None=PrivateAttr(default=None)
    schema_version: Literal[2]=2
    release_id: str=Field(pattern=r'^h-[a-f0-9]{64}$')
    commit: str=Field(pattern=r'^[a-f0-9]{40}$')
    source_sha256: SHA256
    dataset_manifest_sha256: SHA256
    data_as_of: date
    strategy: Literal['recent_then_popular']='recent_then_popular'
    rules: HistoryRules=Field(default_factory=HistoryRules)
    selection_protocol: Literal['quality-v1']='quality-v1'
    selection_archive_sha256: SHA256
    selection_archive_verified: bool
    files: dict[str,ReleaseFile]
    software_versions: dict[str,str]


class HistoryPublicationResult(Contract):
    release_id: str=Field(pattern=r'^h-[a-f0-9]{64}$')
    catalog_size: Count
    past_buyers: Count
    history_rows: Count
    ranking_verified: Literal[True]=True
    status: Literal['prepared']='prepared'
    activated: Literal[False]=False
