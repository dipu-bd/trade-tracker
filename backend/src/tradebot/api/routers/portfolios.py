from decimal import Decimal

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.broker import lots as lot_math
from tradebot.broker.ledger import Ledger
from tradebot.broker.reconcile import reconcile
from tradebot.broker.service import BrokerService, load_portfolio
from tradebot.core.errors import NotFoundError
from tradebot.db.models import (
    Fill,
    Instrument,
    LedgerEntry,
    Order,
    Portfolio,
    PortfolioSnapshot,
    Position,
    PositionStatus,
)
from tradebot.schemas.broker import (
    FillOut,
    HoldingSeed,
    LedgerEntryOut,
    OrderCreate,
    OrderOut,
    PortfolioCreate,
    PortfolioDetail,
    PortfolioOut,
    PositionOut,
    ReconciliationOut,
    SnapshotOut,
)

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


def _broker(context: Context) -> BrokerService:
    return BrokerService(Ledger(clock=context.clock), context.events, clock=context.clock)


async def _marks(session: DbSession, portfolio_id: int) -> dict[int, Decimal]:
    """Last known quote per held instrument, falling back to cost when never quoted."""
    rows = await session.scalars(
        select(Instrument)
        .join(Position, Position.instrument_id == Instrument.id)
        .where(
            Position.portfolio_id == portfolio_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    return {row.id: row.last_quote_price for row in rows if row.last_quote_price is not None}


@router.post("", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    body: PortfolioCreate, user: CurrentUser, context: Context, session: DbSession
) -> PortfolioOut:
    """Open a paper portfolio. The initial capital is posted as the first ledger entry."""
    portfolio = await _broker(context).create_portfolio(
        session,
        user_id=user.id,
        name=body.name,
        initial_capital=body.initial_capital,
        slippage_bps=body.slippage_bps,
        commission_bps=body.commission_bps,
        min_commission=body.min_commission,
        allow_fractional=body.allow_fractional,
    )
    return PortfolioOut.model_validate(portfolio)


@router.get("", response_model=list[PortfolioOut])
async def list_portfolios(user: CurrentUser, session: DbSession) -> list[PortfolioOut]:
    """Every portfolio belonging to the current account."""
    rows = await session.scalars(
        select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)
    )
    return [PortfolioOut.model_validate(row) for row in rows]


@router.get("/{portfolio_id}", response_model=PortfolioDetail)
async def get_portfolio(
    portfolio_id: int, user: CurrentUser, context: Context, session: DbSession
) -> PortfolioDetail:
    """Portfolio settings plus live cash, buying power and marked equity."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    broker = _broker(context)
    marks = await _marks(session, portfolio_id)

    return PortfolioDetail(
        **PortfolioOut.model_validate(portfolio).model_dump(),
        cash=await broker.cash(session, portfolio_id),
        reserved=await broker.reserved(session, portfolio_id),
        buying_power=await broker.buying_power(session, portfolio_id),
        equity=await broker.equity(session, portfolio_id, marks),
        open_positions=len(await broker.open_positions(session, portfolio_id)),
    )


@router.post("/{portfolio_id}/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def place_order(
    portfolio_id: int,
    body: OrderCreate,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> OrderOut:
    """Submit an order. A rejected order is returned with its reason rather than raising."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    instrument = await session.scalar(
        select(Instrument).where(Instrument.symbol == body.symbol.upper())
    )
    if instrument is None:
        raise NotFoundError(f"instrument not tracked: {body.symbol.upper()}")

    order = await _broker(context).place_order(
        session,
        portfolio=portfolio,
        instrument=instrument,
        side=body.side,
        qty=body.qty,
        order_type=body.order_type,
        time_in_force=body.time_in_force,
        limit_price=body.limit_price,
        stop_price=body.stop_price,
        reference_price=instrument.last_quote_price,
        client_order_id=body.client_order_id,
    )
    return OrderOut.model_validate(order)


@router.get("/{portfolio_id}/orders", response_model=list[OrderOut])
async def list_orders(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    open_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[OrderOut]:
    """Orders newest first."""
    await load_portfolio(session, portfolio_id, user.id)
    stmt = select(Order).where(Order.portfolio_id == portfolio_id)
    if open_only:
        stmt = stmt.where(Order.status.in_(("ACCEPTED", "PARTIALLY_FILLED")))

    rows = await session.scalars(stmt.order_by(Order.id.desc()).limit(limit))
    return [OrderOut.model_validate(row) for row in rows]


@router.delete("/{portfolio_id}/orders/{order_id}", response_model=OrderOut)
async def cancel_order(
    portfolio_id: int,
    order_id: int,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> OrderOut:
    """Cancel an open order and release its cash reservation."""
    await load_portfolio(session, portfolio_id, user.id)
    order = await session.scalar(
        select(Order).where(Order.id == order_id, Order.portfolio_id == portfolio_id)
    )
    if order is None:
        raise NotFoundError("order not found")

    return OrderOut.model_validate(await _broker(context).cancel_order(session, order))


@router.get("/{portfolio_id}/fills", response_model=list[FillOut])
async def list_fills(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FillOut]:
    """Executed fills newest first."""
    await load_portfolio(session, portfolio_id, user.id)
    rows = await session.scalars(
        select(Fill)
        .join(Order, Fill.order_id == Order.id)
        .where(Order.portfolio_id == portfolio_id)
        .order_by(Fill.id.desc())
        .limit(limit)
    )
    return [FillOut.model_validate(row) for row in rows]


@router.get("/{portfolio_id}/positions", response_model=list[PositionOut])
async def list_positions(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    open_only: bool = True,
) -> list[PositionOut]:
    """Positions with market value and unrealized profit against the last known quote."""
    await load_portfolio(session, portfolio_id, user.id)
    stmt = select(Position).where(Position.portfolio_id == portfolio_id)
    if open_only:
        stmt = stmt.where(Position.status == PositionStatus.OPEN)

    marks = await _marks(session, portfolio_id)
    out: list[PositionOut] = []
    for row in await session.scalars(stmt.order_by(Position.id)):
        item = PositionOut.model_validate(row)
        mark = marks.get(row.instrument_id)
        if mark is not None:
            item.market_value = lot_math.market_value(row, mark)
            item.unrealized_pnl = lot_math.unrealized(row, mark)
        out.append(item)
    return out


@router.get("/{portfolio_id}/ledger", response_model=list[LedgerEntryOut])
async def list_ledger(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[LedgerEntryOut]:
    """The append-only cash record, newest first."""
    await load_portfolio(session, portfolio_id, user.id)
    rows = await session.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.portfolio_id == portfolio_id)
        .order_by(LedgerEntry.id.desc())
        .limit(limit)
    )
    return [LedgerEntryOut.model_validate(row) for row in rows]


@router.get("/{portfolio_id}/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=365, ge=1, le=3000),
) -> list[SnapshotOut]:
    """The equity curve, oldest first."""
    await load_portfolio(session, portfolio_id, user.id)
    rows = await session.scalars(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == portfolio_id)
        .order_by(PortfolioSnapshot.snap_date.desc())
        .limit(limit)
    )
    return [SnapshotOut.model_validate(row) for row in reversed(list(rows))]


@router.post("/{portfolio_id}/snapshots", response_model=SnapshotOut)
async def take_snapshot(
    portfolio_id: int, user: CurrentUser, context: Context, session: DbSession
) -> SnapshotOut:
    """Record today's equity point."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    marks = await _marks(session, portfolio_id)
    snapshot = await _broker(context).snapshot(session, portfolio, marks)
    return SnapshotOut.model_validate(snapshot)


@router.post("/{portfolio_id}/reconcile", response_model=ReconciliationOut)
async def run_reconciliation(
    portfolio_id: int, user: CurrentUser, context: Context, session: DbSession
) -> ReconciliationOut:
    """Assert the projections still equal a replay of the ledger."""
    await load_portfolio(session, portfolio_id, user.id)
    report = await reconcile(session, Ledger(clock=context.clock), portfolio_id)
    return ReconciliationOut(
        portfolio_id=portfolio_id,
        ok=report.ok,
        cash=report.cash_replayed,
        problems=report.problems,
    )


@router.post(
    "/{portfolio_id}/holdings", response_model=OrderOut, status_code=status.HTTP_201_CREATED
)
async def seed_holding(
    portfolio_id: int,
    body: HoldingSeed,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> OrderOut:
    """Record a position you already hold, at the cost basis you actually paid.

    Cash is debited so the equity curve stays honest, but no slippage or commission is charged:
    the trade happened elsewhere and already cost what it cost.
    """
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    instrument = await session.scalar(
        select(Instrument).where(Instrument.symbol == body.symbol.upper())
    )
    if instrument is None:
        raise NotFoundError(f"instrument not tracked: {body.symbol.upper()}")

    order = await _broker(context).seed_holding(
        session,
        portfolio=portfolio,
        instrument=instrument,
        qty=body.qty,
        cost_basis=body.cost_basis,
        opened_at=body.opened_at or context.clock.now(),
    )
    return OrderOut.model_validate(order)
