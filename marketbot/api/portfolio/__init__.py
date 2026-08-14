from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from marketbot.context import ServerContext
from marketbot.db import (
    Event,
    ExitReason,
    PortfolioSnapshot,
    Position,
    PositionStatus,
    Sleeve,
    Trade,
)
from marketbot.dto.portfolio import (
    EventOut,
    PortfolioCreate,
    PortfolioDetail,
    PortfolioOut,
    PortfolioUpdate,
    PositionOut,
    SnapshotOut,
    TradeOut,
)
from marketbot.security import verify_access_token

router = APIRouter(dependencies=[Depends(verify_access_token)])


@router.post('', summary='Create a portfolio', response_model=PortfolioOut)
def create_portfolio(body: PortfolioCreate, ctx: ServerContext = Depends()):
    with ctx.db.session() as session:
        if ctx.portfolios.get_by_name(session, body.name):
            raise HTTPException(409, f'A portfolio named {body.name!r} already exists')
        fields = body.model_dump(exclude_none=True)
        portfolio = ctx.portfolios.create(session, **fields)
        return PortfolioOut.model_validate(portfolio)


@router.get('', summary='List portfolios', response_model=List[PortfolioOut])
def list_portfolios(ctx: ServerContext = Depends()):
    with ctx.db.session() as session:
        return [
            PortfolioOut.model_validate(p) for p in ctx.portfolios.list_all(session)
        ]


@router.get(
    '/{portfolio_id}',
    summary='Portfolio detail with live valuation',
    response_model=PortfolioDetail,
)
def get_portfolio(portfolio_id: int, ctx: ServerContext = Depends()):
    with ctx.db.session() as session:
        portfolio = _require(ctx, session, portfolio_id)
        positions = ctx.portfolios.open_positions(session, portfolio)
        prices = _price_map(ctx, session, positions)

        equity = ctx.portfolios.equity(portfolio, positions, prices)
        detail = PortfolioDetail.model_validate(portfolio)
        detail.equity = equity
        detail.positions_value = ctx.portfolios.positions_value(positions, prices)
        detail.realized_pnl = ctx.portfolios.realized_pnl(session, portfolio)
        detail.unrealized_pnl = sum(
            p.unrealized_pnl(prices.get(p.instrument.symbol, p.avg_entry))
            for p in positions
        )
        detail.total_return_pct = (
            (equity - portfolio.initial_capital) / portfolio.initial_capital * 100
            if portfolio.initial_capital > 0 else 0.0
        )
        detail.positions = [_position_out(p, prices) for p in positions]
        return detail


@router.patch(
    '/{portfolio_id}',
    summary='Update risk settings or pause a portfolio',
    response_model=PortfolioOut,
)
def update_portfolio(
    portfolio_id: int, body: PortfolioUpdate, ctx: ServerContext = Depends()
):
    with ctx.db.session() as session:
        portfolio = _require(ctx, session, portfolio_id)
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(portfolio, key, value)
        session.flush()
        return PortfolioOut.model_validate(portfolio)


@router.get(
    '/{portfolio_id}/positions',
    summary='Positions, open by default',
    response_model=List[PositionOut],
)
def list_positions(
    portfolio_id: int,
    status: Optional[str] = Query(default=PositionStatus.OPEN),
    limit: int = Query(default=100, le=500),
    ctx: ServerContext = Depends(),
):
    with ctx.db.session() as session:
        portfolio = _require(ctx, session, portfolio_id)
        query = select(Position).where(Position.portfolio_id == portfolio.id)
        if status:
            query = query.where(Position.status == status.upper())
        rows = list(session.scalars(
            query.order_by(Position.id.desc()).limit(limit)
        ).all())
        prices = _price_map(ctx, session, rows)
        return [_position_out(p, prices) for p in rows]


@router.get(
    '/{portfolio_id}/trades', summary='Fill history', response_model=List[TradeOut]
)
def list_trades(
    portfolio_id: int,
    limit: int = Query(default=100, le=500),
    ctx: ServerContext = Depends(),
):
    with ctx.db.session() as session:
        portfolio = _require(ctx, session, portfolio_id)
        rows = session.scalars(
            select(Trade)
            .where(Trade.portfolio_id == portfolio.id)
            .order_by(Trade.id.desc())
            .limit(limit)
        ).all()
        return [
            TradeOut(
                id=t.id,
                symbol=t.instrument.symbol,
                side=t.side,
                qty=t.qty,
                price=t.price,
                gross=t.gross,
                fees=t.fees,
                reason=t.reason,
                executed_at=t.executed_at,
            )
            for t in rows
        ]


@router.get(
    '/{portfolio_id}/events',
    summary='Add/remove event log',
    response_model=List[EventOut],
)
def list_events(
    portfolio_id: int,
    limit: int = Query(default=100, le=500),
    ctx: ServerContext = Depends(),
):
    with ctx.db.session() as session:
        portfolio = _require(ctx, session, portfolio_id)
        rows = session.scalars(
            select(Event)
            .where(Event.portfolio_id == portfolio.id)
            .order_by(Event.id.desc())
            .limit(limit)
        ).all()
        return [EventOut.model_validate(e) for e in rows]


@router.get(
    '/{portfolio_id}/history',
    summary='Daily equity curve',
    response_model=List[SnapshotOut],
)
def list_history(
    portfolio_id: int,
    limit: int = Query(default=365, le=2000),
    ctx: ServerContext = Depends(),
):
    with ctx.db.session() as session:
        portfolio = _require(ctx, session, portfolio_id)
        rows = session.scalars(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.portfolio_id == portfolio.id)
            .order_by(PortfolioSnapshot.snap_date.desc())
            .limit(limit)
        ).all()
        return [SnapshotOut.model_validate(s) for s in reversed(rows)]


@router.post('/{portfolio_id}/scan', summary='Run a scan now')
def trigger_scan(
    portfolio_id: int,
    sleeve: str = Query(default=Sleeve.ALL, pattern='^(all|equity|crypto)$'),
    dry_run: bool = Query(default=False),
    ctx: ServerContext = Depends(),
):
    return ctx.engine.run_scan(portfolio_id, sleeve=sleeve, dry_run=dry_run)


@router.post('/{portfolio_id}/digest', summary='Send the digest email now')
def trigger_digest(portfolio_id: int, ctx: ServerContext = Depends()):
    return {'sent': ctx.engine.send_digest(portfolio_id)}


@router.post('/{portfolio_id}/liquidate', summary='Close every open position')
def liquidate(portfolio_id: int, ctx: ServerContext = Depends()):
    with ctx.db.session() as session:
        portfolio = _require(ctx, session, portfolio_id)
        positions = ctx.portfolios.open_positions(session, portfolio)
        prices = _price_map(ctx, session, positions)
        closed = ctx.portfolios.liquidate(
            session, portfolio, prices, ExitReason.MANUAL
        )
        ctx.portfolios.snapshot(session, portfolio, prices, [])
        return {
            'closed': [
                {'symbol': p.instrument.symbol, 'realized_pnl': p.realized_pnl}
                for p in closed
            ],
            'cash': portfolio.cash,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _require(ctx: ServerContext, session, portfolio_id: int):
    portfolio = ctx.portfolios.get(session, portfolio_id)
    if portfolio is None:
        raise HTTPException(404, f'No portfolio with id {portfolio_id}')
    return portfolio


def _price_map(ctx: ServerContext, session, positions) -> Dict[str, float]:
    """Live prices where we can get them, entry price as the fallback."""
    prices: Dict[str, float] = {}
    open_positions = [p for p in positions if p.status == PositionStatus.OPEN]
    if open_positions:
        instruments = [p.instrument for p in open_positions]
        try:
            quotes = ctx.market_data.get_quotes(session, instruments)
            prices = {symbol: q.price for symbol, q in quotes.items()}
        except Exception:  # noqa: BLE001 — valuation must not 500 on a quote outage
            prices = {}
    for position in positions:
        prices.setdefault(position.instrument.symbol, position.avg_entry)
    return prices


def _position_out(position: Position, prices: Dict[str, float]) -> PositionOut:
    price = prices.get(position.instrument.symbol, position.avg_entry)
    is_open = position.status == PositionStatus.OPEN
    return PositionOut(
        id=position.id,
        symbol=position.instrument.symbol,
        asset_class=position.instrument.asset_class,
        status=position.status,
        qty=position.qty,
        avg_entry=position.avg_entry,
        stop_price=position.stop_price,
        target_price=position.target_price,
        entry_score=position.entry_score,
        entry_at=position.entry_at,
        exit_at=position.exit_at,
        exit_price=position.exit_price,
        exit_reason=position.exit_reason,
        realized_pnl=position.realized_pnl,
        last_price=price if is_open else position.exit_price,
        unrealized_pnl=position.unrealized_pnl(price) if is_open else None,
        r_multiple=position.r_multiple(price) if is_open else None,
    )
