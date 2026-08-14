"""Portfolio state and paper execution.

Fills are simulated at the scan-time price adjusted by a slippage assumption,
with commission applied in basis points. Positions, trades and cash all move
inside the caller's transaction so the books can never end up half-updated.
"""

import logging
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketbot.db import (
    AssetClass,
    Event,
    EventType,
    Instrument,
    Portfolio,
    PortfolioSnapshot,
    Position,
    PositionStatus,
    Side,
    Trade,
)

_log = logging.getLogger(__name__)

BPS = 10_000.0


class PortfolioService:
    def __init__(self, ctx):
        self._ctx = ctx

    # ----------------------------------------------------------------- #
    # Lifecycle
    # ----------------------------------------------------------------- #

    def create(self, session: Session, **fields) -> Portfolio:
        defaults = self._ctx.config.trading
        initial = float(fields.pop('initial_capital', defaults.initial_capital))
        if initial <= 0:
            raise ValueError('initial_capital must be positive')

        portfolio = Portfolio(
            name=fields.pop('name'),
            initial_capital=initial,
            cash=initial,
            risk_pct_per_trade=float(
                fields.pop('risk_pct_per_trade', defaults.risk_pct_per_trade)
            ),
            max_positions=int(fields.pop('max_positions', defaults.max_positions)),
            max_position_pct=float(
                fields.pop('max_position_pct', defaults.max_position_pct)
            ),
            daily_loss_pct=float(fields.pop('daily_loss_pct', defaults.daily_loss_pct)),
            crypto_max_pct=float(fields.pop('crypto_max_pct', defaults.crypto_max_pct)),
        )
        for key, value in fields.items():
            if value is not None and hasattr(portfolio, key):
                setattr(portfolio, key, value)

        session.add(portfolio)
        session.flush()
        _log.info(
            f'Created portfolio {portfolio.name!r} with '
            f'{portfolio.initial_capital:,.2f} {portfolio.base_currency}'
        )
        return portfolio

    def get(self, session: Session, portfolio_id: int) -> Optional[Portfolio]:
        return session.get(Portfolio, portfolio_id)

    def get_by_name(self, session: Session, name: str) -> Optional[Portfolio]:
        return session.scalar(select(Portfolio).where(Portfolio.name == name))

    def list_all(self, session: Session) -> List[Portfolio]:
        return list(session.scalars(select(Portfolio).order_by(Portfolio.id)).all())

    # ----------------------------------------------------------------- #
    # State
    # ----------------------------------------------------------------- #

    def open_positions(self, session: Session, portfolio: Portfolio) -> List[Position]:
        return list(session.scalars(
            select(Position).where(
                Position.portfolio_id == portfolio.id,
                Position.status == PositionStatus.OPEN,
            ).order_by(Position.id)
        ).all())

    def positions_value(
        self, positions: Sequence[Position], prices: Dict[str, float]
    ) -> float:
        total = 0.0
        for position in positions:
            price = prices.get(position.instrument.symbol, position.avg_entry)
            total += price * position.qty
        return total

    def equity(
        self,
        portfolio: Portfolio,
        positions: Sequence[Position],
        prices: Dict[str, float],
    ) -> float:
        return portfolio.cash + self.positions_value(positions, prices)

    def sleeve_exposure(
        self,
        positions: Sequence[Position],
        prices: Dict[str, float],
        asset_class_group: Sequence[str],
    ) -> float:
        total = 0.0
        for position in positions:
            if position.instrument.asset_class not in asset_class_group:
                continue
            price = prices.get(position.instrument.symbol, position.avg_entry)
            total += price * position.qty
        return total

    def realized_pnl(self, session: Session, portfolio: Portfolio) -> float:
        value = session.scalar(
            select(func.sum(Position.realized_pnl)).where(
                Position.portfolio_id == portfolio.id,
                Position.status == PositionStatus.CLOSED,
            )
        )
        return float(value or 0.0)

    def realized_pnl_today(self, session: Session, portfolio: Portfolio) -> float:
        start = datetime.combine(date.today(), datetime.min.time())
        value = session.scalar(
            select(func.sum(Position.realized_pnl)).where(
                Position.portfolio_id == portfolio.id,
                Position.status == PositionStatus.CLOSED,
                Position.exit_at >= start,
            )
        )
        return float(value or 0.0)

    # ----------------------------------------------------------------- #
    # Costs
    # ----------------------------------------------------------------- #

    def commission_bps(self, portfolio: Portfolio, asset_class: str) -> float:
        if asset_class == AssetClass.CRYPTO:
            return portfolio.crypto_commission_bps
        return portfolio.commission_bps

    def fill_price(self, portfolio: Portfolio, price: float, side: str) -> float:
        drift = price * (portfolio.slippage_bps / BPS)
        return price + drift if side == Side.BUY else max(price - drift, 0.0)

    def allows_fractional(self, asset_class: str) -> bool:
        return asset_class == AssetClass.CRYPTO

    # ----------------------------------------------------------------- #
    # Execution
    # ----------------------------------------------------------------- #

    def buy(
        self,
        session: Session,
        portfolio: Portfolio,
        instrument: Instrument,
        qty: float,
        price: float,
        stop_price: float,
        target_price: float,
        r_value: float,
        atr: float,
        max_hold_days: int,
        entry_score: float = 0.0,
        run_id: Optional[int] = None,
    ) -> Optional[Position]:
        if qty <= 0 or price <= 0:
            return None

        fill = self.fill_price(portfolio, price, Side.BUY)
        gross = qty * fill
        fees = gross * (self.commission_bps(portfolio, instrument.asset_class) / BPS)
        cost = gross + fees
        if cost > portfolio.cash + 1e-9:
            _log.info(
                f'Skipping {instrument.symbol}: needs {cost:,.2f} '
                f'but only {portfolio.cash:,.2f} cash available'
            )
            return None

        portfolio.cash -= cost
        position = Position(
            portfolio_id=portfolio.id,
            instrument_id=instrument.id,
            status=PositionStatus.OPEN,
            qty=qty,
            avg_entry=fill,
            entry_score=entry_score,
            initial_stop=stop_price,
            stop_price=stop_price,
            high_water=fill,
            r_value=max(fill - stop_price, 1e-9),
            target_price=target_price,
            max_hold_days=max_hold_days,
            atr_at_entry=atr,
            fees_paid=fees,
        )
        session.add(position)
        session.flush()

        session.add(Trade(
            portfolio_id=portfolio.id,
            position_id=position.id,
            instrument_id=instrument.id,
            run_id=run_id,
            side=Side.BUY,
            qty=qty,
            price=fill,
            gross=gross,
            fees=fees,
            slippage=abs(fill - price) * qty,
            reason='entry',
        ))
        session.flush()
        return position

    def sell(
        self,
        session: Session,
        portfolio: Portfolio,
        position: Position,
        price: float,
        reason: str,
        run_id: Optional[int] = None,
    ) -> Optional[Trade]:
        if position.status != PositionStatus.OPEN or price <= 0:
            return None

        asset_class = position.instrument.asset_class
        fill = self.fill_price(portfolio, price, Side.SELL)
        gross = position.qty * fill
        fees = gross * (self.commission_bps(portfolio, asset_class) / BPS)

        portfolio.cash += gross - fees
        position.status = PositionStatus.CLOSED
        position.exit_at = datetime.now(timezone.utc)
        position.exit_price = fill
        position.exit_reason = reason
        position.fees_paid += fees
        position.realized_pnl = (
            (fill - position.avg_entry) * position.qty - position.fees_paid
        )

        trade = Trade(
            portfolio_id=portfolio.id,
            position_id=position.id,
            instrument_id=position.instrument_id,
            run_id=run_id,
            side=Side.SELL,
            qty=position.qty,
            price=fill,
            gross=gross,
            fees=fees,
            slippage=abs(price - fill) * position.qty,
            reason=reason,
        )
        session.add(trade)
        session.flush()
        return trade

    # ----------------------------------------------------------------- #
    # Bookkeeping
    # ----------------------------------------------------------------- #

    def record_event(
        self,
        session: Session,
        portfolio: Portfolio,
        event_type: str,
        payload: dict,
        run_id: Optional[int] = None,
    ) -> Event:
        event = Event(
            portfolio_id=portfolio.id,
            run_id=run_id,
            type=event_type,
            payload=payload,
        )
        session.add(event)
        session.flush()
        return event

    def snapshot(
        self,
        session: Session,
        portfolio: Portfolio,
        prices: Dict[str, float],
        positions: Optional[Sequence[Position]] = None,
    ) -> PortfolioSnapshot:
        positions = (
            positions if positions is not None
            else self.open_positions(session, portfolio)
        )
        market_value = self.positions_value(positions, prices)
        equity = portfolio.cash + market_value
        unrealized = sum(
            position.unrealized_pnl(
                prices.get(position.instrument.symbol, position.avg_entry)
            )
            for position in positions
        )

        peak = session.scalar(
            select(func.max(PortfolioSnapshot.equity)).where(
                PortfolioSnapshot.portfolio_id == portfolio.id
            )
        ) or portfolio.initial_capital
        peak = max(float(peak), equity, portfolio.initial_capital)
        drawdown = ((peak - equity) / peak * 100) if peak > 0 else 0.0

        today = date.today()
        snapshot = session.scalar(
            select(PortfolioSnapshot).where(
                PortfolioSnapshot.portfolio_id == portfolio.id,
                PortfolioSnapshot.snap_date == today,
            )
        )
        if snapshot is None:
            snapshot = PortfolioSnapshot(
                portfolio_id=portfolio.id, snap_date=today, equity=equity,
                cash=portfolio.cash, positions_value=market_value,
            )
            session.add(snapshot)

        snapshot.equity = equity
        snapshot.cash = portfolio.cash
        snapshot.positions_value = market_value
        snapshot.realized_pnl = self.realized_pnl(session, portfolio)
        snapshot.unrealized_pnl = unrealized
        snapshot.open_positions = len(positions)
        snapshot.drawdown_pct = drawdown
        snapshot.updated_at = datetime.now(timezone.utc)
        session.flush()
        return snapshot

    def previous_snapshot(
        self, session: Session, portfolio: Portfolio
    ) -> Optional[PortfolioSnapshot]:
        return session.scalar(
            select(PortfolioSnapshot)
            .where(
                PortfolioSnapshot.portfolio_id == portfolio.id,
                PortfolioSnapshot.snap_date < date.today(),
            )
            .order_by(PortfolioSnapshot.snap_date.desc())
            .limit(1)
        )

    def liquidate(
        self,
        session: Session,
        portfolio: Portfolio,
        prices: Dict[str, float],
        reason: str,
        run_id: Optional[int] = None,
    ) -> List[Position]:
        """Close everything — the manual kill switch and the loss breaker."""
        closed: List[Position] = []
        for position in self.open_positions(session, portfolio):
            price = prices.get(position.instrument.symbol, position.avg_entry)
            if self.sell(session, portfolio, position, price, reason, run_id):
                closed.append(position)
                self.record_event(
                    session, portfolio, EventType.POSITION_CLOSED,
                    _closed_payload(position, price, reason), run_id,
                )
        return closed


def _closed_payload(position: Position, price: float, reason: str) -> dict:
    return {
        'symbol': position.instrument.symbol,
        'asset_class': position.instrument.asset_class,
        'qty': position.qty,
        'avg_entry': position.avg_entry,
        'price': position.exit_price or price,
        'realized_pnl': position.realized_pnl,
        'r_multiple': position.r_multiple(position.exit_price or price),
        'reason': reason,
    }
