import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.broker import lots as lot_math
from tradebot.broker import matching
from tradebot.broker import orders as order_rules
from tradebot.broker.costs import CostModel
from tradebot.broker.ledger import Ledger
from tradebot.core.clock import Clock, LiveClock
from tradebot.core.errors import ConflictError, NotFoundError, ValidationError
from tradebot.core.money import quantize_cash, quantize_price, quantize_qty
from tradebot.db.models import (
    EntryType,
    Fill,
    Instrument,
    Lot,
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    PortfolioSnapshot,
    Position,
    PositionStatus,
    Side,
    TimeInForce,
)
from tradebot.marketdata import calendar
from tradebot.obs import EventRecorder
from tradebot.providers.base import AssetClass, Quote

ZERO = Decimal(0)


@dataclass
class FillResult:
    order_id: int
    qty: Decimal
    price: Decimal
    fee: Decimal
    realized: Decimal = ZERO


@dataclass
class MatchReport:
    fills: list[FillResult] = field(default_factory=list)
    expired: list[int] = field(default_factory=list)


class BrokerService:
    """Paper execution: orders, fills, FIFO lots and cash, all projected from the ledger."""

    def __init__(
        self,
        ledger: Ledger,
        events: EventRecorder,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._ledger = ledger
        self._events = events
        self._clock = clock or LiveClock()
        self._armed_stops: set[int] = set()

    async def create_portfolio(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        name: str,
        initial_capital: Decimal,
        **settings: object,
    ) -> Portfolio:
        if initial_capital <= ZERO:
            raise ValidationError("initial capital must be positive")

        existing = await session.scalar(
            select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.name == name)
        )
        if existing is not None:
            raise ConflictError(f"a portfolio named {name!r} already exists")

        portfolio = Portfolio(
            user_id=user_id, name=name, initial_capital=quantize_cash(initial_capital), **settings
        )
        session.add(portfolio)
        await session.flush()

        await self._ledger.deposit(
            session,
            portfolio_id=portfolio.id,
            amount=initial_capital,
            memo="initial capital",
        )
        return portfolio

    async def cash(self, session: AsyncSession, portfolio_id: int) -> Decimal:
        return await self._ledger.balance(session, portfolio_id)

    async def reserved(self, session: AsyncSession, portfolio_id: int) -> Decimal:
        total = await session.scalar(
            select(func.coalesce(func.sum(Order.reserved_cash), 0)).where(
                Order.portfolio_id == portfolio_id,
                Order.status.in_(tuple(OrderStatus.OPEN)),
            )
        )
        return quantize_cash(Decimal(total or 0))

    async def buying_power(self, session: AsyncSession, portfolio_id: int) -> Decimal:
        return quantize_cash(
            await self.cash(session, portfolio_id) - await self.reserved(session, portfolio_id)
        )

    async def place_order(
        self,
        session: AsyncSession,
        *,
        portfolio: Portfolio,
        instrument: Instrument,
        side: str,
        qty: Decimal,
        order_type: str = OrderType.MARKET,
        time_in_force: str = TimeInForce.DAY,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        reference_price: Decimal | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        qty = quantize_qty(qty, whole_units=not portfolio.allow_fractional)
        order_rules.validate_request(
            side=side,
            order_type=order_type,
            time_in_force=time_in_force,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            allow_fractional=portfolio.allow_fractional,
        )

        client_order_id = client_order_id or uuid.uuid4().hex
        duplicate = await session.scalar(
            select(Order).where(Order.client_order_id == client_order_id)
        )
        if duplicate is not None:
            # Idempotency: a retried cycle must not double-trade.
            return duplicate

        now = self._clock.now()
        order = Order(
            portfolio_id=portfolio.id,
            instrument_id=instrument.id,
            client_order_id=client_order_id,
            side=side,
            order_type=order_type,
            time_in_force=time_in_force,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            submitted_at=now,
            expires_at=self._expiry(time_in_force, now),
        )
        session.add(order)
        await session.flush()

        rejection = await self._reject_reason(
            session, portfolio, instrument, order, reference_price
        )
        if rejection is not None:
            order_rules.transition(order, OrderStatus.REJECTED, at=now, reason=rejection)
            await session.flush()
            await self._record(session, portfolio, "order_rejected", order, message=rejection)
            return order

        if side == Side.BUY:
            reference = limit_price or reference_price
            if reference is not None:
                order.reserved_cash = CostModel.of(portfolio).reservation(qty, reference)

        order_rules.transition(order, OrderStatus.ACCEPTED, at=now)
        await session.flush()
        await self._record(session, portfolio, "order_placed", order)
        return order

    def _expiry(self, time_in_force: str, now: datetime) -> datetime | None:
        if time_in_force == TimeInForce.GTC:
            return None
        if time_in_force == TimeInForce.IOC:
            return now
        bounds = calendar.session_bounds(now.date())
        return bounds[1] if bounds else now + timedelta(days=1)

    async def _reject_reason(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        instrument: Instrument,
        order: Order,
        reference_price: Decimal | None,
    ) -> str | None:
        asset_class = AssetClass(instrument.asset_class)
        closed = not calendar.is_open(self._clock.now(), asset_class)
        if closed and order.time_in_force == TimeInForce.IOC:
            return "market closed"

        if order.side == Side.SELL:
            position = await self._open_position(session, portfolio.id, instrument.id)
            held = position.qty if position else ZERO
            if order.qty > held:
                return f"insufficient position: holding {held}"
            return None

        reference = order.limit_price or reference_price
        if reference is None:
            return None

        needed = CostModel.of(portfolio).reservation(order.qty, reference)
        available = await self.buying_power(session, portfolio.id)
        if needed > available:
            return f"insufficient buying power: need {needed}, have {available}"
        return None

    async def cancel_order(self, session: AsyncSession, order: Order) -> Order:
        if not order.is_open:
            raise ValidationError(f"order {order.id} is already {order.status}")
        order_rules.transition(order, OrderStatus.CANCELED, at=self._clock.now())
        order.reserved_cash = ZERO
        self._armed_stops.discard(order.id)
        await session.flush()
        return order

    async def open_orders(self, session: AsyncSession, portfolio_id: int) -> list[Order]:
        rows = await session.scalars(
            select(Order)
            .where(
                Order.portfolio_id == portfolio_id,
                Order.status.in_(tuple(OrderStatus.OPEN)),
            )
            .order_by(Order.id)
        )
        return list(rows)

    async def on_quote(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        instrument: Instrument,
        quote: Quote,
    ) -> MatchReport:
        """Drive every open order for this instrument against one quote."""
        report = MatchReport()
        now = self._clock.now()

        candidates = await session.scalars(
            select(Order)
            .where(
                Order.portfolio_id == portfolio.id,
                Order.instrument_id == instrument.id,
                Order.status.in_(tuple(OrderStatus.OPEN)),
            )
            .order_by(Order.id)
        )

        for order in candidates:
            if order_rules.is_expired(order, now):
                order_rules.transition(order, OrderStatus.EXPIRED, at=now)
                order.reserved_cash = ZERO
                report.expired.append(order.id)
                continue

            trigger = matching.evaluate(order, quote, stop_armed=order.id in self._armed_stops)
            if trigger.reference is not None and order.order_type in (
                OrderType.STOP,
                OrderType.STOP_LIMIT,
            ):
                self._armed_stops.add(order.id)

            if not trigger.fills or trigger.reference is None:
                if order.time_in_force == TimeInForce.IOC:
                    order_rules.transition(order, OrderStatus.EXPIRED, at=now)
                    order.reserved_cash = ZERO
                    report.expired.append(order.id)
                continue

            qty = quantize_qty(
                matching.fillable_qty(order, quote),
                whole_units=not portfolio.allow_fractional,
            )
            if qty <= ZERO:
                continue

            report.fills.append(
                await self._execute(session, portfolio, instrument, order, qty, trigger.reference)
            )

        await session.flush()
        return report

    async def _execute(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        instrument: Instrument,
        order: Order,
        qty: Decimal,
        reference: Decimal,
    ) -> FillResult:
        costs = CostModel.of(portfolio)
        price = costs.fill_price(reference, order.side)
        notional = quantize_cash(qty * price)
        fee = costs.commission(notional)
        now = self._clock.now()

        # A count query rather than len(order.fills): lazy relationship loads raise under async.
        previous = await session.scalar(
            select(func.count(Fill.id)).where(Fill.order_id == order.id)
        )
        seq = int(previous or 0) + 1
        fill = Fill(
            order_id=order.id,
            seq=seq,
            qty=qty,
            price=price,
            fee=fee,
            slippage_amount=costs.slippage_amount(reference, price, qty),
            executed_at=now,
        )
        session.add(fill)
        await session.flush()

        realized = (
            await self._settle_buy(session, portfolio, instrument, fill, notional, fee)
            if order.side == Side.BUY
            else await self._settle_sell(session, portfolio, instrument, fill, notional, fee)
        )

        order_rules.record_fill(order, qty, price, at=now)
        order.reserved_cash = (
            ZERO if not order.is_open else max(ZERO, order.reserved_cash - (notional + fee))
        )
        if not order.is_open:
            self._armed_stops.discard(order.id)
        await session.flush()

        await self._record(
            session,
            portfolio,
            "order_filled",
            order,
            message=f"{order.side} {qty} {instrument.symbol} @ {price}",
            payload={"qty": str(qty), "price": str(price), "fee": str(fee)},
        )
        return FillResult(order.id, qty, price, fee, realized)

    async def _settle_buy(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        instrument: Instrument,
        fill: Fill,
        notional: Decimal,
        fee: Decimal,
    ) -> Decimal:
        await self._ledger.post(
            session,
            portfolio_id=portfolio.id,
            entry_type=EntryType.BUY,
            amount=-notional,
            ref_type="fill",
            ref_id=fill.id,
            memo=instrument.symbol,
        )
        if fee > ZERO:
            await self._ledger.post(
                session,
                portfolio_id=portfolio.id,
                entry_type=EntryType.FEE,
                amount=-fee,
                ref_type="fill",
                ref_id=fill.id,
                memo=instrument.symbol,
            )

        position = await self._open_position(session, portfolio.id, instrument.id)
        if position is None:
            position = Position(
                portfolio_id=portfolio.id,
                instrument_id=instrument.id,
                opened_at=self._clock.now(),
            )
            session.add(position)
            await session.flush()

        session.add(
            Lot(
                position_id=position.id,
                fill_id=fill.id,
                qty_original=fill.qty,
                qty_open=fill.qty,
                cost_basis=fill.price,
                fee_paid=fee,
                opened_at=self._clock.now(),
            )
        )
        position.fees_paid = position.fees_paid + fee
        await session.flush()
        await lot_math.project(session, position)
        return ZERO

    async def _settle_sell(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        instrument: Instrument,
        fill: Fill,
        notional: Decimal,
        fee: Decimal,
    ) -> Decimal:
        position = await self._open_position(session, portfolio.id, instrument.id)
        if position is None:
            raise ValidationError(f"no open position in {instrument.symbol}")

        consumed = await lot_math.consume_fifo(session, position, fill.qty, fill.price)
        realized = quantize_cash(sum((c.realized for c in consumed), ZERO) - fee)

        await self._ledger.post(
            session,
            portfolio_id=portfolio.id,
            entry_type=EntryType.SELL,
            amount=notional,
            ref_type="fill",
            ref_id=fill.id,
            memo=instrument.symbol,
        )
        if fee > ZERO:
            await self._ledger.post(
                session,
                portfolio_id=portfolio.id,
                entry_type=EntryType.FEE,
                amount=-fee,
                ref_type="fill",
                ref_id=fill.id,
                memo=instrument.symbol,
            )

        position.realized_pnl = position.realized_pnl + realized
        position.fees_paid = position.fees_paid + fee
        await lot_math.project(session, position)

        if position.qty <= ZERO:
            position.status = PositionStatus.CLOSED
            position.closed_at = self._clock.now()
        await session.flush()
        return realized

    async def _open_position(
        self, session: AsyncSession, portfolio_id: int, instrument_id: int
    ) -> Position | None:
        found: Position | None = await session.scalar(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.instrument_id == instrument_id,
                Position.status == PositionStatus.OPEN,
            )
        )
        return found

    async def open_positions(self, session: AsyncSession, portfolio_id: int) -> list[Position]:
        rows = await session.scalars(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.status == PositionStatus.OPEN,
            )
        )
        return list(rows)

    async def equity(
        self, session: AsyncSession, portfolio_id: int, marks: dict[int, Decimal]
    ) -> Decimal:
        cash = await self.cash(session, portfolio_id)
        value = ZERO
        for position in await self.open_positions(session, portfolio_id):
            mark = marks.get(position.instrument_id, position.avg_cost)
            value += lot_math.market_value(position, mark)
        return quantize_cash(cash + value)

    async def snapshot(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        marks: dict[int, Decimal],
        *,
        on: date | None = None,
    ) -> PortfolioSnapshot:
        snap_date = on or self._clock.now().date()
        cash = await self.cash(session, portfolio.id)

        positions_value = ZERO
        unrealized = ZERO
        open_positions = await self.open_positions(session, portfolio.id)
        for position in open_positions:
            mark = marks.get(position.instrument_id, position.avg_cost)
            positions_value += lot_math.market_value(position, mark)
            unrealized += lot_math.unrealized(position, mark)

        equity = quantize_cash(cash + positions_value)
        peak = await session.scalar(
            select(func.max(PortfolioSnapshot.equity)).where(
                PortfolioSnapshot.portfolio_id == portfolio.id
            )
        )
        high_water = max(Decimal(peak or 0), equity, portfolio.initial_capital)
        drawdown = (
            quantize_price((high_water - equity) / high_water * 100) if high_water > ZERO else ZERO
        )

        existing = await session.scalar(
            select(PortfolioSnapshot).where(
                PortfolioSnapshot.portfolio_id == portfolio.id,
                PortfolioSnapshot.snap_date == snap_date,
            )
        )
        snapshot = existing or PortfolioSnapshot(portfolio_id=portfolio.id, snap_date=snap_date)
        snapshot.equity = equity
        snapshot.cash = cash
        snapshot.positions_value = quantize_cash(positions_value)
        snapshot.realized_pnl = await self._ledger.realized_total(session, portfolio.id)
        snapshot.unrealized_pnl = quantize_cash(unrealized)
        snapshot.open_positions = len(open_positions)
        snapshot.drawdown_pct = drawdown

        if existing is None:
            session.add(snapshot)
        await session.flush()
        return snapshot

    async def _record(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        kind: str,
        order: Order,
        *,
        message: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        await self._events.record(
            session,
            domain="broker",
            kind=kind,
            user_id=portfolio.user_id,
            portfolio_id=portfolio.id,
            message=message or f"{order.side} {order.qty}",
            payload={"order_id": order.id, **(payload or {})},
        )


async def load_portfolio(session: AsyncSession, portfolio_id: int, user_id: int) -> Portfolio:
    portfolio = await session.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    )
    if portfolio is None:
        raise NotFoundError("portfolio not found")
    return portfolio
