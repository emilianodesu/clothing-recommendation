"""FastAPI serving application with authenticated administrative endpoints."""

from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import threading
import time
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from hm_recommender.api.metrics import Metrics
from hm_recommender.api.runtime import source_digest
from hm_recommender.api.schemas import (
    ActivateRequest,
    HistoryRecommendationResponse,
    PrepareRequest,
    RecommendationRequest,
)
from hm_recommender.releases.registry import ReleaseRegistry, Unavailable


def create_app(
    registry: ReleaseRegistry | None = None,
    *,
    admin_token: str | None = None,
    input_root: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI recommendation and management service."""
    registry = registry or ReleaseRegistry(Path(os.environ.get('HMREC_SERVING_DIR', '/serving')))
    token = admin_token or os.environ['HM_APP_PASSWORD']
    root = Path(input_root or os.environ.get('HMREC_INPUT_ROOT', '/artifacts')).resolve()

    counts = {'ok': 0, 'unavailable': 0}
    duration = [0.0]
    metrics_lock = threading.Lock()
    code_digest = source_digest()
    telemetry = Metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry.open()
        try:
            yield
        finally:
            registry.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware('http')
    async def measure(request: Request, call_next: Callable):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            if request.url.path != '/metrics':
                telemetry.observe(status, time.perf_counter() - started)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={'detail': 'Invalid request'})

    def admin(authorization: str = Header(default='')):
        expected = ('Bearer ' + token).encode()
        if not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(status_code=403, detail='Administrative authorization required')

    @app.get('/health/live')
    def live():
        return {'status': 'alive', 'source_tree_sha256': code_digest}

    @app.get('/health/ready')
    def ready():
        try:
            state = registry.snapshot()
        except Unavailable:
            return JSONResponse(status_code=503, content={'status': 'unavailable'})
        return {
            'status': 'ready',
            'release_id': state.release_id,
            'data_as_of': state.data_as_of.isoformat(),
        }

    @app.post('/recommend', response_model=HistoryRecommendationResponse)
    def recommend(request: RecommendationRequest):
        started = time.perf_counter()
        status = 'ok'
        try:
            # The retained object remains consistent after the lock is released.
            response = registry.snapshot().recommend(request)
            if response.fallback:
                telemetry.used_fallback()
            return response
        except Unavailable:
            status = 'unavailable'
            raise HTTPException(status_code=503, detail='No valid active release') from None
        finally:
            with metrics_lock:
                counts[status] += 1
                duration[0] += time.perf_counter() - started

    @app.get('/metrics', response_class=PlainTextResponse)
    def metrics():
        with metrics_lock:
            body = (
                '# TYPE hmrec_recommendations_total counter\n'
                + ''.join(f'hmrec_recommendations_total{{status="{k}"}} {v}\n' for k, v in counts.items())
                + '# TYPE hmrec_recommendation_seconds_sum counter\n'
                + f'hmrec_recommendation_seconds_sum {duration[0]}\n'
                + telemetry.render()
            )
            return body

    def run_admin(operation: Callable):
        try:
            return operation()
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=400, detail='Invalid or unprepared release') from None
        except Exception:
            raise HTTPException(status_code=503, detail='Publication unavailable; inspect local service state') from None

    @app.post('/admin/releases/prepare', dependencies=[Depends(admin)])
    def prepare(request: PrepareRequest):
        relative = Path(request.directory)
        candidate = root / relative
        if (
            relative.is_absolute()
            or '..' in relative.parts
            or candidate.is_symlink()
            or not candidate.resolve().is_relative_to(root)
        ):
            raise HTTPException(status_code=400, detail='Invalid package directory')
        return run_admin(lambda: registry.prepare_release(candidate))

    @app.post('/admin/releases/activate', dependencies=[Depends(admin)])
    def activate(request: ActivateRequest):
        run_admin(lambda: registry.activate_release(request.release_id))
        return {'status': 'active', 'release_id': request.release_id}

    @app.post('/admin/releases/rollback', dependencies=[Depends(admin)])
    def rollback():
        return {'status': 'active', 'release_id': run_admin(registry.rollback)}

    from hm_recommender.api.events import install_events

    install_events(app, registry, admin)
    return app
