"""Operational feedback routes; the base /recommend contract stays immutable."""
from collections import OrderedDict
from dataclasses import replace
from types import MappingProxyType
from datetime import date, datetime, timezone
import heapq
import os
from pathlib import Path
import threading
from typing import Annotated
import polars as pl
from fastapi import HTTPException, Depends, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from hm_recommender.api.schemas import EventInput, RecommendationRequest
from hm_recommender.events.store import EventStore, Conflict
from hm_recommender.events.aggregate import stream_identity, aggregate_events, metadata
from hm_recommender.ui.images import ImageCache
from hm_recommender.api.demo_profiles import DemoProfiles


class RefreshInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_as_of: date


class ArticleDetailsInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    article_ids: list[Annotated[str, Field(min_length=1, max_length=32, pattern=r'^\d+$')]] = Field(min_length=1, max_length=48)


def install_events(app, registry, admin):
    store=EventStore(registry.store.connection)
    profiles=DemoProfiles(store.connection, os.environ.get('HMREC_IMAGE_ARCHIVE'))
    images=ImageCache(os.environ.get('HMREC_IMAGE_ARCHIVE'),os.environ.get('HMREC_IMAGE_CACHE','/tmp/hmrec-images'),
                      max_bytes=int(os.environ.get('HMREC_IMAGE_CACHE_BYTES',64*1024**2)))
    catalog_cache: OrderedDict[tuple[str, str], tuple[list[dict], list[str]]] = OrderedDict()
    catalog_lock = threading.Lock()
    max_catalog_entries = 2

    def _get_catalog_cached(release_id: str) -> tuple[list[dict], list[str]]:
        catalog_path = registry._directory(release_id) / 'catalog.parquet'
        cache_key = (release_id, str(catalog_path))
        with catalog_lock:
            cached = catalog_cache.get(cache_key)
            if cached is not None:
                catalog_cache.move_to_end(cache_key)
                return cached
            table = pl.read_parquet(catalog_path)
            rows: list[dict] = []
            search_texts: list[str] = []
            for raw_row in table.to_dicts():
                norm_row = {
                    k: (v.isoformat() if isinstance(v, (date, datetime)) else v)
                    for k, v in raw_row.items()
                }
                rows.append(norm_row)
                search_texts.append(
                    ' '.join(
                        str(v)
                        for k, v in norm_row.items()
                        if k != 'first_seen' and v is not None
                    ).lower()
                )
            entry = (rows, search_texts)
            catalog_cache[cache_key] = entry
            catalog_cache.move_to_end(cache_key)
            while len(catalog_cache) > max_catalog_entries:
                catalog_cache.popitem(last=False)
            return entry

    @app.get('/streams')
    def streams():
        try:
            base=registry.snapshot()
            with store.connection() as conn:
                ids=conn.execute('SELECT stream_id FROM event_streams WHERE release_id=%s AND revision>0 ORDER BY published_at DESC LIMIT 50',(base.release_id,)).fetchall()
                return {'items':[metadata(conn,str(row[0])) for row in ids]}
        except Exception: raise HTTPException(503,'Streams unavailable') from None

    @app.get('/customers')
    def customers():
        try: return {'items':heapq.nsmallest(50,registry.snapshot().customers)}
        except Exception: raise HTTPException(503,'Customers unavailable') from None

    @app.get('/streams/{stream_id}/demo-profiles')
    def demo_profiles(stream_id: str):
        try:
            stream_identity(stream_id)
            return profiles.get(registry.snapshot(), stream_id)
        except Conflict: raise HTTPException(409, 'Stream belongs to another active release') from None
        except ValueError: raise HTTPException(404, 'Stream unavailable') from None
        except Exception: raise HTTPException(503, 'Demonstration profiles unavailable') from None

    @app.get('/catalog')
    def catalog(
        offset: int = Query(0, ge=0, le=10_000_000),
        limit: int = Query(12, ge=1, le=48),
        q: str | None = Query(None, max_length=256),
        index_group_name: str | None = Query(None, max_length=128),
    ):
        try:
            base = registry.snapshot()
            rows, search_texts = _get_catalog_cached(base.release_id)
            idx_group = (index_group_name or '').strip()
            query = (q or '').strip().lower()
            if not idx_group and not query:
                total = len(rows)
                items = rows[offset : offset + limit]
            else:
                if idx_group and query:
                    filtered = [
                        row
                        for row, txt in zip(rows, search_texts)
                        if row.get('index_group_name') == idx_group and query in txt
                    ]
                elif idx_group:
                    filtered = [row for row in rows if row.get('index_group_name') == idx_group]
                else:
                    filtered = [row for row, txt in zip(rows, search_texts) if query in txt]
                total = len(filtered)
                items = filtered[offset : offset + limit]
            return {
                'release_id': base.release_id,
                'total': total,
                'items': items,
                'filters': {field: sorted(index) for field, index in base.filter_index.items()},
            }
        except Exception:
            raise HTTPException(503, 'Catalog unavailable') from None

    @app.get('/articles/{article_id}/image')
    def image(article_id: str):
        try:
            if article_id not in registry.snapshot().catalog: raise HTTPException(404,'Unknown article')
            body,mime=images.read(article_id);return Response(body,media_type=mime)
        except HTTPException: raise
        except Exception: raise HTTPException(503,'Image unavailable') from None

    @app.post('/articles/details')
    def article_details(request: ArticleDetailsInput):
        """Bounded display metadata from the active snapshot; never changes ranking."""
        try:
            base = registry.snapshot()
            details = {item: {'article_id': item} for item in request.article_ids if item in base.catalog}
            requested = set(details)
            for field, groups in base.filter_index.items():
                for value, members in groups.items():
                    for item in requested.intersection(members):
                        details[item][field] = value
            return {'release_id': base.release_id, 'items': list(details.values())}
        except Exception: raise HTTPException(503, 'Article details unavailable') from None

    @app.post('/admin/streams/{stream_id}/refresh',dependencies=[Depends(admin)])
    def refresh(stream_id: str, request: RefreshInput):
        try:
            return aggregate_events(registry.snapshot().release_id,datetime.now(timezone.utc),
                stream_id=stream_id,data_as_of=request.data_as_of,connection=store.connection)
        except ValueError: raise HTTPException(400,'Invalid stream or clock') from None
        except Exception: raise HTTPException(503,'Profile publication unavailable') from None

    @app.post('/events')
    def insert_event(event: EventInput):
        try:
            if registry.snapshot().release_id!=event.release_id: raise Conflict('Active release differs')
            result=store.insert(event)
            return JSONResponse(status_code=201 if result['status']=='created' else 200,content=result)
        except Conflict: raise HTTPException(409,'Event or stream identity conflict') from None
        except ValueError: raise HTTPException(422,'Invalid event or article') from None
        except Exception: raise HTTPException(503,'Event was not acknowledged; retry with the same ID') from None

    @app.post('/streams/{stream_id}/recommend')
    def recommend_stream(stream_id: str, request: RecommendationRequest):
        try:
            stream_identity(stream_id)
            base = registry.snapshot()
            release, cutoff, revision, received, published, popular, history, mode = store.profile(
                stream_id, request.customer_id
            )
            if release != base.release_id:
                raise Conflict('Stream belongs to another active release')
            if hasattr(base, 'recommend_stream'):
                with store.connection() as conn:
                    rows = conn.execute(
                        """SELECT article_id, kind, event_time
                           FROM interaction_events
                           WHERE stream_id = %s AND customer_id = %s
                             AND received_at < %s
                             AND (event_time AT TIME ZONE 'UTC')::date < %s
                           ORDER BY event_time ASC""",
                        (stream_id, request.customer_id, received, cutoff),
                    ).fetchall()
                live_events = [
                    {'article_id': str(aid), 'kind': str(kind), 'event_time': ev_time}
                    for aid, kind, ev_time in rows
                ]
                body = base.recommend_stream(
                    request,
                    cutoff=cutoff,
                    popular=tuple(popular),
                    history=tuple(history),
                    live_events=live_events,
                ).model_dump(mode='json')
            else:
                state = replace(
                    base,
                    data_as_of=cutoff,
                    histories=MappingProxyType({request.customer_id: tuple(history)}),
                    popularity=tuple(popular),
                )
                body = state.recommend(request).model_dump(mode='json')
            body.update(
                stream_id=stream_id,
                revision=revision,
                received_before=received.isoformat(),
                published_at=published.isoformat(),
                mode=mode,
            )
            return body
        except Conflict:
            raise HTTPException(409, 'Stream belongs to another active release') from None
        except ValueError:
            raise HTTPException(404, 'Stream unavailable') from None
        except Exception:
            raise HTTPException(503, 'Operational profiles unavailable') from None
