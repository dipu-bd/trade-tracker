import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .context import ServerContext
    ctx = ServerContext()
    try:
        ctx.db  # create tables before the first request lands
        ctx.scheduler.start()
    except Exception:  # noqa: BLE001 — a broken schedule must not block serving
        _log.exception('Startup failed')
    yield
    try:
        ctx.scheduler.close()
    except Exception:  # noqa: BLE001
        _log.exception('Shutdown failed')


app = FastAPI(
    title="MarketBot",
    description="Real time reports on trade market changes",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
)

try:
    from .api import router as api
    app.include_router(api, prefix='/api')
except ImportError:
    traceback.print_exc()
