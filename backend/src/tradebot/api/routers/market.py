from fastapi import APIRouter, Query
from sqlalchemy import select

from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.core.errors import ConflictError, NotFoundError, ValidationError
from tradebot.db.models import Instrument
from tradebot.marketdata.jobs import ProgressFn
from tradebot.marketdata.refresh import DISCOVERABLE, MarketSync
from tradebot.marketdata.service import IngestReport, MarketDataService
from tradebot.providers.base import AssetClass
from tradebot.schemas.market import (
    BarOut,
    InstrumentOut,
    ProviderStatusOut,
    QuoteOut,
    SyncRequest,
    SyncStatusOut,
)

router = APIRouter(prefix="/market", tags=["market"])


def _asset_class(value: str) -> AssetClass:
    try:
        return AssetClass(value.lower())
    except ValueError as exc:
        allowed = ", ".join(a.value for a in AssetClass)
        raise ValidationError(f"unknown asset class '{value}'; expected one of {allowed}") from exc


def _masked_fields(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


async def _service(context: Context, session: DbSession, user_id: int) -> MarketDataService:
    router_for_user = await context.providers.build_router(session, user_id)
    return MarketDataService(router_for_user, context.events, clock=context.clock)


@router.get("/instruments", response_model=list[InstrumentOut])
async def list_instruments(
    user: CurrentUser,
    context: Context,
    session: DbSession,
    asset_class: str | None = None,
    active_only: bool = True,
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[InstrumentOut]:
    """Tracked instruments with their last quote and how stale it is."""
    stmt = select(Instrument)
    if asset_class:
        stmt = stmt.where(Instrument.asset_class == _asset_class(asset_class).value)
    if active_only:
        stmt = stmt.where(Instrument.is_active.is_(True))

    rows = list(await session.scalars(stmt.order_by(Instrument.symbol).limit(limit)))
    service = await _service(context, session, user.id)

    out: list[InstrumentOut] = []
    for row in rows:
        item = InstrumentOut.model_validate(row)
        item.staleness_seconds = service.staleness_seconds(row)
        out.append(item)
    return out


@router.get("/instruments/{symbol}/bars", response_model=list[BarOut])
async def instrument_bars(
    symbol: str,
    user: CurrentUser,
    context: Context,
    session: DbSession,
    asset_class: str | None = None,
    limit: int = Query(default=260, ge=1, le=2000),
) -> list[BarOut]:
    """Stored daily bars, oldest first."""
    stmt = select(Instrument).where(Instrument.symbol == symbol.upper())
    if asset_class:
        stmt = stmt.where(Instrument.asset_class == _asset_class(asset_class).value)

    instrument = await session.scalar(stmt)
    if instrument is None:
        raise NotFoundError(f"instrument not tracked: {symbol.upper()}")

    service = await _service(context, session, user.id)
    bars = await service.load_bars(session, instrument, limit=limit)
    return [BarOut.model_validate(bar) for bar in bars]


@router.get("/quotes", response_model=list[QuoteOut])
async def quotes(
    user: CurrentUser,
    context: Context,
    session: DbSession,
    symbols: str = Query(description="Comma separated symbols"),
) -> list[QuoteOut]:
    """Live quotes, fetched through the provider chain for each symbol's asset class."""
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise ValidationError("at least one symbol is required")

    instruments = list(
        await session.scalars(select(Instrument).where(Instrument.symbol.in_(wanted)))
    )
    if not instruments:
        raise NotFoundError("none of those symbols are tracked")

    service = await _service(context, session, user.id)
    fetched = await service.get_quotes(session, instruments)
    return [QuoteOut(**vars(quote)) for quote in fetched.values()]


@router.get("/providers", response_model=list[ProviderStatusOut])
async def providers(
    user: CurrentUser, context: Context, session: DbSession
) -> list[ProviderStatusOut]:
    """Per-provider configuration and health. Credential values are masked, never returned."""
    summary = {
        row["provider"]: row for row in await context.providers.masked_summary(session, user.id)
    }
    router_for_user = await context.providers.build_router(session, user.id)
    health = {row["provider"]: row for row in router_for_user.health_snapshot()}

    out: list[ProviderStatusOut] = []
    for provider in router_for_user.providers:
        row = summary.get(provider.key, {})
        out.append(
            ProviderStatusOut(
                provider=provider.key,
                label=provider.label,
                keyless=bool(row.get("keyless", False)),
                configured=bool(row.get("configured", False)),
                available=provider.available,
                capabilities=sorted(c.value for c in provider.capabilities),
                asset_classes=sorted(a.value for a in provider.asset_classes),
                missing_credentials=list(provider.missing_credentials),
                fields=_masked_fields(row.get("fields")),
                health=health.get(provider.key, {}),
            )
        )
    return out


@router.post("/sync", response_model=SyncStatusOut)
async def sync(
    body: SyncRequest, user: CurrentUser, context: Context, session: DbSession
) -> SyncStatusOut:
    """Start a sync over every named asset class: universe, daily bars and last price.

    A full pass is thousands of sequential provider calls, so it runs in the background and this
    returns immediately — poll `GET /market/sync` for progress. Naming symbols pulls exactly
    those; a provider's listing is ranked (most-actives and similar), so anything outside it is
    only reachable by name.
    """
    classes = [_asset_class(name) for name in body.asset_classes] or list(DISCOVERABLE)
    runner = MarketSync(context)

    async def run(progress: ProgressFn) -> IngestReport:
        return await runner.discover(
            user.id,
            asset_classes=classes,
            symbols=body.symbols,
            limit=body.limit,
            progress=progress,
        )

    if not context.sync_job.start(", ".join(c.value for c in classes), run):
        raise ConflictError("a market sync is already running")

    await context.events.record(
        session,
        domain="market",
        kind="universe_synced",
        user_id=user.id,
        message=", ".join(body.symbols) or ", ".join(c.value for c in classes),
    )
    return SyncStatusOut.model_validate(context.sync_job.snapshot())


@router.post("/refresh", response_model=SyncStatusOut)
async def refresh(user: CurrentUser, context: Context) -> SyncStatusOut:
    """Start the periodic pass now: bars and prices for everything already tracked."""
    runner = MarketSync(context)

    async def run(progress: ProgressFn) -> IngestReport:
        return await runner.refresh_all(progress=progress)

    if not context.sync_job.start("refresh", run):
        raise ConflictError("a market sync is already running")
    return SyncStatusOut.model_validate(context.sync_job.snapshot())


@router.get("/sync", response_model=SyncStatusOut)
async def sync_status(user: CurrentUser, context: Context) -> SyncStatusOut:
    """Progress of the running or last-finished pass."""
    return SyncStatusOut.model_validate(context.sync_job.snapshot())
