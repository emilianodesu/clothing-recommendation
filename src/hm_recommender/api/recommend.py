"""Immutable in-memory version; filtering precedes candidate truncation."""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4
import polars as pl
from hm_recommender.ranking.history import rank_history
from hm_recommender.releases.package import verify_history_release
from hm_recommender.api.schemas import HistoryRecommendationResponse, RecommendationItem

FILTERS = ('product_type_name','product_group_name','colour_group_name')


@dataclass(frozen=True)
class HistoryState:
    release_id: str
    data_as_of: date
    customers: frozenset
    catalog: frozenset
    histories: MappingProxyType
    popularity: tuple
    filter_index: MappingProxyType

    def eligible(self, filters):
        eligible = self.catalog
        for key, value in filters.items():
            if key not in FILTERS or not isinstance(value,str):
                raise ValueError('Unknown filter')
            eligible = eligible & self.filter_index[key].get(value, frozenset())
        return eligible

    def rank(self, customer_id, filters):
        return rank_history(self.histories.get(customer_id,()), self.popularity, self.eligible(filters))

    def recommend(self, request):
        eligible = self.eligible(request.filters)
        history = self.histories.get(request.customer_id,())
        recent = frozenset(item for item in history if item in eligible)
        ranked = rank_history(history,self.popularity,eligible)
        return HistoryRecommendationResponse(request_id=str(uuid4()),release_id=self.release_id,
            features_timestamp=self.data_as_of,fallback=not recent,
            items=[RecommendationItem(article_id=item,rank=n,
                reason='recent_purchase' if item in recent else 'popular_7d')
                for n,item in enumerate(ranked[:request.k],1)])


def load_state(directory: Path) -> HistoryState:
    manifest = verify_history_release(directory)
    catalog = pl.read_parquet(directory/'catalog.parquet')
    histories = {}
    for user,item in pl.read_parquet(directory/'histories.parquet').select('customer_id','article_id').iter_rows():
        histories.setdefault(user,[]).append(item)
    indexes = {}
    for field in FILTERS:
        groups = {}
        for item,value in catalog.select('article_id',field).iter_rows():
            if value is not None: groups.setdefault(value,set()).add(item)
        indexes[field] = MappingProxyType({key:frozenset(values) for key,values in groups.items()})
    state = HistoryState(manifest.release_id,manifest.data_as_of,
        frozenset(pl.read_parquet(directory/'customers.parquet')['customer_id']),
        frozenset(catalog['article_id']),
        MappingProxyType({key:tuple(values) for key,values in histories.items()}),
        tuple(pl.read_parquet(directory/'popularity.parquet')['article_id']),MappingProxyType(indexes))
    if not state.rank('',{}): raise ValueError('Release cannot recommend')
    return state
