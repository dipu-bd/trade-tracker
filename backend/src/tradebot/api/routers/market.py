from fastapi import APIRouter, Query
from sqlalchemy import select

from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.core.errors import NotFoundError, ValidationError
from tradebot.db.models import Instrument
from tradebot.marketdata.refresh import MarketDataRefresher
from tradebot.marketdata.service import MarketDataService
from tradebot.providers.base import AssetClass
from tradebot.schemas.market import (
    BarOut,
    InstrumentOut,
    ProviderStatusOut,
    QuoteOut,
    SyncRequest,
    SyncResultOut,
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


@router.post("/sync", response_model=SyncResultOut)
async def sync(
    body: SyncRequest, user: CurrentUser, context: Context, session: DbSession
) -> SyncResultOut:
    """Populate the store: universe, daily bars and last price, in one call.

    Naming symbols pulls exactly those — a provider's listing is ranked (most-actives and
    similar), so anything outside it was previously unreachable.
    """
    asset_class = _asset_class(body.asset_class)
    service = await _service(context, session, user.id)

    instruments, report = await service.sync(
        session,
        asset_class,
        symbols=body.symbols,
        limit=body.limit,
        with_bars=body.refresh_bars,
        with_quotes=body.refresh_quotes,
    )

    await context.events.record(
        session,
        domain="market",
        kind="universe_synced",
        user_id=user.id,
        message=", ".join(item.symbol for item in instruments) or asset_class.value,
        payload={"instruments": len(instruments), "failed": report.failed},
    )

    return SyncResultOut(
        asset_class=asset_class.value,
        instruments=len(instruments),
        bars_written=report.bars_written,
        quotes_updated=report.quotes_updated,
        skipped_fresh=report.skipped_fresh,
        failed=report.failed,
        gaps=report.gaps,
    )


@router.post("/refresh", response_model=SyncResultOut)
async def refresh(user: CurrentUser, context: Context) -> SyncResultOut:
    """Run the periodic refresh now, over every tracked instrument, for every owner."""
    report = await MarketDataRefresher(context).refresh_all()
    return SyncResultOut(
        asset_class="all",
        instruments=report.instruments,
        bars_written=report.bars_written,
        quotes_updated=report.quotes_updated,
        skipped_fresh=report.skipped_fresh,
        failed=report.failed,
        gaps=report.gaps,
    )
