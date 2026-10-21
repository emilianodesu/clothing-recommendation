from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RecommendationRequest(StrictModel):
    customer_id: str = Field(min_length=1, max_length=128, pattern=r'^\S+$')
    k: int = Field(default=12, ge=1, le=12)
    filters: dict[Literal['product_type_name','product_group_name','colour_group_name'],
                  str] = Field(default_factory=dict, max_length=3)


class RecommendationItem(StrictModel):
    article_id: str
    rank: int
    reason: Literal['recent_purchase','popular_7d']


class HistoryRecommendationResponse(StrictModel):
    request_id: str
    release_id: str
    strategy: Literal['recent_then_popular'] = 'recent_then_popular'
    features_timestamp: date
    fallback: bool
    items: list[RecommendationItem]


class PrepareRequest(StrictModel):
    directory: str = Field(min_length=1, max_length=512)


class ActivateRequest(StrictModel):
    release_id: str = Field(pattern=r'^h-[a-f0-9]{64}$')
