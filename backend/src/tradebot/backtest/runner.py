import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.backtest.metrics import Performance, TradeResult, evaluate
from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.core.clock import ReplayClock
from tradebot.db.models import Instrument, Portfolio, Position, PositionStatus, PriceBar
from tradebot.engine.cycle import DecisionCycle
from tradebot.obs import EventRecorder
from tradebot.providers.base import Quote

SESSION_CLOSE = time(21, 0)


@dataclass
class ReplayResult:
    label: str
    dates: list[date] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    trades: list[TradeResult] = field(default_factory=list)
    orders: int = 0
    traded_notional: float = 0.0
    days_invested: int = 0
    error: str | None = None

    @property
    def exposure(self) -> float:
        return self.days_invested / len(self.equity) if self.equity else 0.0

    @property
    def turnover(self) -> float:
        start = self.equity[0] if self.equity else 0.0
        return self.traded_notional / start if start > 0 else 0.0

    def performance(self, periods_per_year: int = 252) -> Performance:
        return evaluate(
            self.equity,
            self.trades,
            periods_per_year=periods_per_year,
            exposure=self.exposure,
            turnover=self.turnover,
        )


async def trading_days(session: AsyncSession, symbol: str, start: date, end: date) -> list[date]:
    """The calendar the replay walks: dates the benchmark actually has bars for."""
    rows = await session.scalars(
        select(PriceBar.bar_date)
        .join(Instrument, PriceBar.instrument_id == Instrument.id)
        .where(
            Instrument.symbol == symbol.upper(),
            PriceBar.bar_date >= start,
            PriceBar.bar_date <= end,
        )
        .order_by(PriceBar.bar_date)
    )
    return list(rows)


async def closes_for(session: AsyncSession, symbol: str) -> dict[date, Decimal]:
    rows = await session.execute(
        select(PriceBar.bar_date, PriceBar.close)
        .join(Instrument, PriceBar.instrument_id == Instrument.id)
        .where(Instrument.symbol == symbol.upper())
        .order_by(PriceBar.bar_date)
    )
    return {row[0]: row[1] for row in rows.all()}


async def buy_and_hold(
    session: AsyncSession, symbol: str, days: list[date], capital: float = 100_000.0
) -> ReplayResult:
    """The comparison every result is reported against.

    "Grew from $10k to $11k" is meaningless without what an index fund did over the same window,
    so this is computed on the same dates from the same stored bars.
    """
    closes = await closes_for(session, symbol)
    result = ReplayResult(label=f"buy_and_hold:{symbol}")

    first = next((closes[day] for day in days if day in closes), None)
    if first is None or first <= 0:
        result.error = f"no bars for {symbol}"
        return result

    units = Decimal(str(capital)) / first
    for day in days:
        price = closes.get(day)
        if price is None:
            continue
        result.dates.append(day)
        result.equity.append(float(units * price))
        result.days_invested += 1

    return result


class ReplayRunner:
    """Backtesting is a different wiring of the live code, not a second implementation.

    The same `DecisionCycle` and the same `BrokerService` run here; only the clock and the source
    of quotes differ. If this file grew a decision rule of its own, the backtest would stop
    measuring the strategy that actually trades.
    """

    def __init__(self, events: EventRecorder) -> None:
        self._events = events

    async def run(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        days: list[date],
        *,
        label: str = "rules",
        ai_pipeline: object = None,
        keys: dict[str, str] | None = None,
    ) -> ReplayResult:
        result = ReplayResult(label=label)
        if len(days) < 2:
            result.error = "not enough trading days"
            return result

        clock = ReplayClock(datetime.combine(days[0], SESSION_CLOSE, tzinfo=UTC))
        ledger = Ledger(clock=clock)
        broker = BrokerService(ledger, self._events, clock=clock)
        cycle = DecisionCycle(
            broker,
            self._events,
            clock,
            ai=ai_pipeline,  # type: ignore[arg-type]
            keys=keys or {},
        )

        for index, day in enumerate(days):
            clock.advance_to(datetime.combine(day, SESSION_CLOSE, tzinfo=UTC))

            report = await cycle.run(session, portfolio, trigger=f"replay:{label}")
            result.orders += len(report.orders)

            if index + 1 < len(days):
                filled = await self._fill_next_session(
                    session, broker, portfolio, days[index + 1], clock
                )
                result.traded_notional += filled

            marks = await self._marks(session, portfolio.id, day)
            equity = await broker.equity(session, portfolio.id, marks)
            result.dates.append(day)
            result.equity.append(float(equity))
            if marks:
                result.days_invested += 1

        result.trades = await self._closed_trades(session, portfolio.id)
        return result

    async def _fill_next_session(
        self,
        session: AsyncSession,
        broker: BrokerService,
        portfolio: Portfolio,
        day: date,
        clock: ReplayClock,
    ) -> float:
        """Orders decided at a close fill at the next session, not the price that caused them."""
        clock.advance_to(datetime.combine(day, time(14, 30), tzinfo=UTC))
        open_orders = await broker.open_orders(session, portfolio.id)
        if not open_orders:
            return 0.0

        traded = 0.0
        instruments = {order.instrument_id for order in open_orders}
        for instrument_id in instruments:
            bar = await session.scalar(
                select(PriceBar).where(
                    PriceBar.instrument_id == instrument_id, PriceBar.bar_date == day
                )
            )
            if bar is None:
                continue
            instrument = await session.get(Instrument, instrument_id)
            if instrument is None:
                continue
            quote = Quote(symbol=instrument.symbol, price=bar.open, at=clock.now())
            report = await broker.on_quote(session, portfolio, instrument, quote)
            for fill in report.fills:
                traded += float(fill.qty * fill.price)

        return traded

    async def _marks(
        self, session: AsyncSession, portfolio_id: int, day: date
    ) -> dict[int, Decimal]:
        rows = await session.execute(
            select(PriceBar.instrument_id, PriceBar.close)
            .join(Position, Position.instrument_id == PriceBar.instrument_id)
            .where(
                Position.portfolio_id == portfolio_id,
                Position.status == PositionStatus.OPEN,
                PriceBar.bar_date == day,
            )
        )
        return {row[0]: row[1] for row in rows.all()}

    async def _closed_trades(self, session: AsyncSession, portfolio_id: int) -> list[TradeResult]:
        rows = await session.execute(
            select(Position, Instrument.symbol)
            .join(Instrument, Position.instrument_id == Instrument.id)
            .where(
                Position.portfolio_id == portfolio_id,
                Position.status == PositionStatus.CLOSED,
            )
        )

        trades: list[TradeResult] = []
        for position, symbol in rows.all():
            cost = float(position.avg_cost)
            if cost <= 0:
                continue
            days = 1
            if position.closed_at and position.opened_at:
                days = max(1, (position.closed_at - position.opened_at).days)
            realized = float(position.realized_pnl)
            trades.append(
                TradeResult(
                    symbol=symbol,
                    holding_days=days,
                    return_pct=realized / cost if cost else 0.0,
                    r_multiple=realized / cost / 0.06 if cost else 0.0,
                )
            )
        return trades


async def random_entry_control(
    session: AsyncSession,
    symbols: list[str],
    days: list[date],
    *,
    seed: int = 7,
    capital: float = 100_000.0,
    hold_days: int = 21,
    positions: int = 5,
) -> ReplayResult:
    """A matched-turnover control that trades on noise.

    If the strategy cannot beat coin flips with the same holding period and position count, its
    edge is the market's drift rather than the signal, and the backtest should say so.
    """
    generator = random.Random(seed)  # noqa: S311
    curves = {symbol: await closes_for(session, symbol) for symbol in symbols}
    usable = [symbol for symbol, closes in curves.items() if len(closes) > 1]
    result = ReplayResult(label="random_entry")

    if not usable or len(days) < 2:
        result.error = "not enough data for a control"
        return result

    equity = capital
    holdings: dict[str, tuple[float, int]] = {}

    for index, day in enumerate(days):
        for symbol in list(holdings):
            units, opened = holdings[symbol]
            if index - opened >= hold_days:
                price = curves[symbol].get(day)
                if price is not None:
                    equity += units * float(price)
                    del holdings[symbol]

        while len(holdings) < positions:
            symbol = generator.choice(usable)
            price = curves[symbol].get(day)
            if symbol in holdings or price is None or price <= 0:
                break
            stake = equity / (positions - len(holdings))
            if stake <= 0:
                break
            equity -= stake
            holdings[symbol] = (stake / float(price), index)
            result.traded_notional += stake

        held_value = sum(
            units * float(curves[symbol].get(day, 0) or 0)
            for symbol, (units, _) in holdings.items()
        )
        result.dates.append(day)
        result.equity.append(equity + held_value)
        if holdings:
            result.days_invested += 1

    return result
