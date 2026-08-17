import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tradebot.api.routers import api_router
from tradebot.context import AppContext
from tradebot.core.errors import TradebotError
from tradebot.core.logging import configure_logging, get_logger
from tradebot.core.settings import Settings, get_settings
from tradebot.obs import metrics
from tradebot.obs.slack import SlackNotifier, WebhookLookup
from tradebot.providers.base import ProviderError
from tradebot.workers.scheduler import EngineScheduler

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    context: AppContext = app.state.context
    _log.info("startup", env=context.settings.env)

    scheduler = EngineScheduler(context)
    app.state.scheduler = scheduler
    notifier = SlackNotifier(context.bus, WebhookLookup(context))
    app.state.notifier = notifier
    if context.settings.scheduler_enabled:
        scheduler.start()
        notifier.start()

    try:
        yield
    finally:
        scheduler.shutdown()
        await notifier.aclose()
        await context.aclose()
        _log.info("shutdown")


def create_app(settings: Settings | None = None, *, context: AppContext | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="Tradebot",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.context = context or AppContext.build(settings)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    _register_middleware(app)
    _register_error_handlers(app)

    app.include_router(api_router, prefix="/api")
    _mount_static(app, settings)
    return app


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[object]]
    ) -> object:
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        metrics.http_latency.labels(request.method, path).observe(time.perf_counter() - started)
        metrics.http_requests.labels(
            request.method, path, str(getattr(response, "status_code", 0))
        ).inc()
        return response


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(TradebotError)
    async def handle_domain_error(_request: Request, exc: TradebotError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(ProviderError)
    async def handle_provider_error(_request: Request, exc: ProviderError) -> JSONResponse:
        # An upstream that is down is not an internal error, and a 500 hides which provider it
        # was behind a stack trace nobody reads.
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "provider_unavailable", "message": str(exc)}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Without this a 422 keeps FastAPI's `detail` shape, and the client reads the envelope
        # it never finds as an empty message — HTTP/2 has no status text to fall back on.
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'][1:])}: {error['msg']}"
            for error in exc.errors()
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {"code": "validation_error", "message": details or "invalid request"}
            },
        )


def _mount_static(app: FastAPI, settings: Settings) -> None:
    if not settings.static_dir:
        return
    root = Path(settings.static_dir)
    if not root.is_dir():
        _log.warning("static_dir_missing", path=str(root))
        return

    app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = root / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(root / "index.html")
