import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.ai.pipeline import AIOutcome, AIPipeline
from tradebot.ai.reflection import ClosedTrade, Reflection
from tradebot.analytics.exits import ExitAction, Holding
from tradebot.analytics.features import Features, extract
from tradebot.analytics.indicators import rolling_return
from tradebot.analytics.series import BarSeries
from tradebot.broker.service import BrokerService
from tradebot.core.clock import Clock
from tradebot.db.models import (
    DecisionRun,
    Fill,
    Instrument,
    Lesson,
    Lot,
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionStatus,
    Side,
)
from tradebot.engine.config import strategy_config
from tradebot.engine.strategy import Decision, Entry, PortfolioState, decide
from tradebot.engine.universe import UniverseSpec, resolve
from tradebot.marketdata.view import MarketView
from tradebot.obs import EventRecorder
from tradebot.providers.base import Capability

TURNOVER_WINDOW_DAYS = 30


@dataclass
class CycleReport:
    run_id: int
    correlation_id: str
    as_of: date
    decision: Decision | None = None
    orders: list[int] = field(default_factory=list)
    skipped_orders: dict[str, str] = field(default_factory=dict)
    ai: AIOutcome | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class DecisionCycle:
    """A decision cycle is a function of (clock, market view, portfolio state) -> orders.

    The three inputs are injected rather than reached for, which is the whole reason a backtest
    can be a different wiring of this code instead of a parallel implementation of it.
    """

    def __init__(
        self,
        broker: BrokerService,
        events: EventRecorder,
        clock: Clock,
        ai: AIPipeline | None = None,
        reflection: Reflection | None = None,
        keys: dict[str, str] | None = None,
        capabilities: frozenset[Capability] = frozenset(),
    ) -> None:
        self._broker = broker
        self._events = events
        self._clock = clock
        self._ai = ai
        self._reflection = reflection
        self._keys = keys or {}
        self._capabilities = capabilities

    async def run(
        self, session: AsyncSession, portfolio: Portfolio, trigger: str = "scheduled"
    ) -> CycleReport:
        now = self._clock.now()
        correlation_id = uuid.uuid4().hex[:32]
        view = MarketView(session, self._clock)
        as_of = view.last_complete_bar_date()

        run = DecisionRun(
            portfolio_id=portfolio.id,
            correlation_id=correlation_id,
            trigger=trigger,
            started_at=now,
            as_of=as_of,
        )
        session.add(run)
        await session.flush()

        report = CycleReport(run_id=run.id, correlation_id=correlation_id, as_of=as_of)
        await self._emit(session, portfolio, "cycle_started", correlation_id, {"trigger": trigger})

        try:
            decision, ai = await self._decide(
                session, portfolio, view, as_of, correlation_id, run.id
            )
            report.decision = decision
            report.ai = ai
            await self._emit(
                session,
                portfolio,
                "screened",
                correlation_id,
                {
                    "candidates": decision.candidates,
                    "regime": decision.regime.state.value,
                    "exposure": decision.regime.exposure,
                },
            )

            report.orders, report.skipped_orders = await self._execute(
                session, portfolio, view, decision, correlation_id
            )
        except Exception as error:
            run.status = "failed"
            run.error = str(error)[:500]
            run.finished_at = self._clock.now()
            report.error = str(error)
            await self._emit(
                session, portfolio, "cycle_finished", correlation_id, {"status": "failed"}
            )
            return report

        run.status = "ok"
        run.finished_at = self._clock.now()
        run.regime = decision.regime.state.value
        run.exposure = Decimal(str(round(decision.regime.exposure, 4)))
        run.candidates = decision.candidates
        run.entries = len(decision.entries)
        run.exits = len(decision.exits)
        run.orders_placed = len(report.orders)
        run.detail = {
            "entries": [
                {
                    "symbol": item.symbol,
                    "target_weight": round(item.target_weight, 6),
                    "score": round(item.score, 6),
                    "binding": item.sizing.binding,
                }
                for item in decision.entries
            ],
            "exits": [
                {"symbol": item.symbol, "reason": item.reason.value, "fraction": item.fraction}
                for item in decision.exits
            ],
            "screened_out": decision.screened_out,
            "skipped": decision.skipped,
            "skipped_orders": report.skipped_orders,
            "ai": report.ai.as_detail() if report.ai else {"enabled": False},
        }
        await session.flush()

        await self._emit(
            session,
            portfolio,
            "cycle_finished",
            correlation_id,
            {"status": "ok", "orders": len(report.orders)},
        )
        return report

    async def _decide(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        view: MarketView,
        as_of: date,
        correlation_id: str = "",
        run_id: int | None = None,
    ) -> tuple[Decision, AIOutcome]:
        config = strategy_config(portfolio)
        state, held_symbols = await self._state(session, portfolio, view)

        spec = UniverseSpec.from_json(portfolio.universe)
        universe = await resolve(session, spec, held=held_symbols)

        cohort: list[Features] = []
        for instrument in universe.instruments:
            cohort.append(await view.features(instrument.symbol))

        benchmark_series = await view.bars(config.benchmark, 700)
        benchmark_features = extract(benchmark_series)

        await self._reflect(session, portfolio, benchmark_series)

        proposed = decide(
            as_of=as_of,
            cohort=cohort,
            benchmark_series=benchmark_series,
            benchmark_features=benchmark_features,
            state=state,
            config=config,
            names=universe.names,
        )

        if self._ai is None:
            return proposed, AIOutcome(reason="no ai pipeline wired")

        outcome = await self._ai.run(
            session,
            portfolio,
            as_of,
            proposed,
            state,
            {item.symbol: item for item in cohort},
            config,
            self._keys,
            self._capabilities,
            correlation_id,
            run_id,
        )
        if not outcome.used:
            return proposed, outcome

        # Re-run the rules with the meta-labeler's confidence. Deterministic and cheap, and it
        # keeps the AI's whole influence expressible as one map the audit row can show.
        final = decide(
            as_of=as_of,
            cohort=cohort,
            benchmark_series=benchmark_series,
            benchmark_features=benchmark_features,
            state=state,
            config=config,
            names=universe.names,
            confidence=outcome.confidence,
        )
        return final, outcome

    async def _reflect(
        self, session: AsyncSession, portfolio: Portfolio, benchmark: BarSeries
    ) -> None:
        """Turn positions that closed since the last cycle into lessons.

        Triggered at the start of a cycle rather than on the fill, because a position closes
        when a quote arrives rather than when the engine is running, and reflecting here is
        idempotent — a position already carrying a lesson is skipped.
        """
        if self._reflection is None:
            return

        closed = await session.execute(
            select(Position, Instrument.symbol)
            .join(Instrument, Position.instrument_id == Instrument.id)
            .outerjoin(Lesson, Lesson.position_id == Position.id)
            .where(
                Position.portfolio_id == portfolio.id,
                Position.status == PositionStatus.CLOSED,
                Position.closed_at.is_not(None),
                Lesson.id.is_(None),
            )
            .limit(5)
        )

        for position, symbol in closed.all():
            days = max(1, (position.closed_at - position.opened_at).days)
            invested = await session.scalar(
                select(func.coalesce(func.sum(Lot.qty_original * Lot.cost_basis), 0)).where(
                    Lot.position_id == position.id
                )
            )
            cost = float(invested or 0)
            realized = float(position.realized_pnl) / cost if cost > 0 else 0.0
            await self._reflection.record(
                session,
                ClosedTrade(
                    portfolio_id=portfolio.id,
                    position_id=position.id,
                    symbol=symbol,
                    closed_at=position.closed_at,
                    holding_days=days,
                    realized_return=realized,
                    benchmark_return=_benchmark_return(benchmark, days),
                ),
            )

    async def _state(
        self, session: AsyncSession, portfolio: Portfolio, view: MarketView
    ) -> tuple[PortfolioState, frozenset[str]]:
        positions = await self._broker.open_positions(session, portfolio.id)
        symbols = {
            position.instrument_id: await self._symbol(session, position.instrument_id)
            for position in positions
        }
        marks: dict[int, Decimal] = {}
        for position in positions:
            mark = await view.mark(symbols[position.instrument_id])
            if mark is not None:
                marks[position.instrument_id] = mark

        equity = await self._broker.equity(session, portfolio.id, marks)
        cash = await self._broker.cash(session, portfolio.id)

        holdings: dict[str, Holding] = {}
        weights: dict[str, float] = {}
        for position in positions:
            symbol = symbols[position.instrument_id]
            mark = marks.get(position.instrument_id, position.avg_cost)
            close = float(mark)
            holdings[symbol] = Holding(
                symbol=symbol,
                qty=float(position.qty),
                entry_price=float(position.avg_cost),
                entry_date=position.opened_at.date(),
                highest_close=float(position.highest_close or position.avg_cost),
                stop_price=float(position.stop_price or 0),
                laddered=position.laddered,
            )
            if equity > 0:
                weights[symbol] = float(position.qty) * close / float(equity)

        return (
            PortfolioState(
                equity=float(equity),
                cash=float(cash),
                holdings=holdings,
                weights=weights,
                last_exit=await self._last_exits(session, portfolio.id),
                turnover_used=await self._turnover(session, portfolio.id, equity),
            ),
            frozenset(holdings),
        )

    async def _execute(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        view: MarketView,
        decision: Decision,
        correlation_id: str,
    ) -> tuple[list[int], dict[str, str]]:
        placed: list[int] = []
        skipped: dict[str, str] = {}

        pending = await self._pending_instruments(session, portfolio.id)

        for action in decision.exits:
            if await self._is_pending(session, action.symbol, pending):
                skipped[action.symbol] = "an order is already working"
                continue
            order = await self._place_exit(session, portfolio, view, action, correlation_id)
            if isinstance(order, str):
                skipped[action.symbol] = order
            else:
                placed.append(order)

        await self._apply_stops(session, portfolio, decision)

        for entry in decision.entries:
            if await self._is_pending(session, entry.symbol, pending):
                skipped[entry.symbol] = "an order is already working"
                continue
            order = await self._place_entry(session, portfolio, view, entry, correlation_id)
            if isinstance(order, str):
                skipped[entry.symbol] = order
            else:
                placed.append(order)

        return placed, skipped

    async def _pending_instruments(self, session: AsyncSession, portfolio_id: int) -> set[int]:
        """Instruments with an order still working.

        Without this a cycle firing before the previous one's fills arrive sees no position and
        buys the same name twice; the buying-power reservation limits the damage but does not
        prevent it.
        """
        rows = await session.scalars(
            select(Order.instrument_id).where(
                Order.portfolio_id == portfolio_id, Order.status.in_(sorted(OrderStatus.OPEN))
            )
        )
        return set(rows)

    async def _is_pending(self, session: AsyncSession, symbol: str, pending: set[int]) -> bool:
        if not pending:
            return False
        instrument = await self._instrument(session, symbol)
        return instrument is not None and instrument.id in pending

    async def _place_exit(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        view: MarketView,
        action: ExitAction,
        correlation_id: str,
    ) -> int | str:
        instrument = await self._instrument(session, action.symbol)
        if instrument is None:
            return "instrument not tracked"

        position = await session.scalar(
            select(Position).where(
                Position.portfolio_id == portfolio.id,
                Position.instrument_id == instrument.id,
                Position.status == PositionStatus.OPEN,
            )
        )
        if position is None or position.qty <= 0:
            return "no open position"

        qty = position.qty * Decimal(str(action.fraction))
        mark = await view.mark(action.symbol)

        order = await self._broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.SELL,
            qty=qty,
            order_type=OrderType.MARKET,
            reference_price=mark,
            client_order_id=f"{correlation_id}-x-{instrument.id}",
        )
        if not action.is_full:
            position.laddered = True
        return order.id

    async def _place_entry(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        view: MarketView,
        entry: Entry,
        correlation_id: str,
    ) -> int | str:
        instrument = await self._instrument(session, entry.symbol)
        if instrument is None:
            return "instrument not tracked"

        mark = await view.mark(entry.symbol)
        if mark is None or mark <= 0:
            return "no usable mark"

        delta_weight = entry.target_weight - entry.current_weight
        if delta_weight <= 0:
            return "already at or above target weight"

        equity = Decimal(str(entry.notional / entry.target_weight)) if entry.target_weight else None
        if equity is None:
            return "zero target weight"

        qty = (equity * Decimal(str(delta_weight))) / mark
        if qty <= 0:
            return "sized below one unit"

        order = await self._broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=qty,
            order_type=OrderType.MARKET,
            reference_price=mark,
            client_order_id=f"{correlation_id}-e-{instrument.id}",
        )
        return order.id

    async def _apply_stops(
        self, session: AsyncSession, portfolio: Portfolio, decision: Decision
    ) -> None:
        for update in decision.stop_updates:
            instrument = await self._instrument(session, update.symbol)
            if instrument is None:
                continue
            position = await session.scalar(
                select(Position).where(
                    Position.portfolio_id == portfolio.id,
                    Position.instrument_id == instrument.id,
                    Position.status == PositionStatus.OPEN,
                )
            )
            if position is not None:
                position.stop_price = Decimal(str(update.new_stop))

    async def _last_exits(self, session: AsyncSession, portfolio_id: int) -> dict[str, date]:
        rows = await session.execute(
            select(Instrument.symbol, func.max(Position.closed_at))
            .join(Position, Position.instrument_id == Instrument.id)
            .where(
                Position.portfolio_id == portfolio_id,
                Position.status == PositionStatus.CLOSED,
                Position.closed_at.is_not(None),
            )
            .group_by(Instrument.symbol)
        )
        return {symbol: closed.date() for symbol, closed in rows.all() if closed is not None}

    async def _turnover(self, session: AsyncSession, portfolio_id: int, equity: Decimal) -> float:
        if equity <= 0:
            return 0.0

        since = self._clock.now() - timedelta(days=TURNOVER_WINDOW_DAYS)
        traded = await session.scalar(
            select(func.coalesce(func.sum(Fill.qty * Fill.price), 0))
            .join(Order, Fill.order_id == Order.id)
            .where(Order.portfolio_id == portfolio_id, Fill.executed_at >= since)
        )
        return float(Decimal(str(traded or 0)) / equity)

    async def _instrument(self, session: AsyncSession, symbol: str) -> Instrument | None:
        found: Instrument | None = await session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        return found

    async def _symbol(self, session: AsyncSession, instrument_id: int) -> str:
        symbol: str | None = await session.scalar(
            select(Instrument.symbol).where(Instrument.id == instrument_id)
        )
        return symbol or ""

    async def _emit(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        kind: str,
        correlation_id: str,
        payload: dict[str, object],
    ) -> None:
        await self._events.record(
            session,
            domain="engine",
            kind=kind,
            user_id=portfolio.user_id,
            portfolio_id=portfolio.id,
            correlation_id=correlation_id,
            payload=payload,
        )


def _benchmark_return(benchmark: BarSeries, days: int) -> float:
    """What the benchmark did over the same holding window.

    A winning trade that lagged the benchmark is not a good trade, and the lesson is worthless
    without that comparison.
    """
    closes = benchmark.closes
    if len(closes) < days + 1:
        return 0.0
    return rolling_return(closes, days) or 0.0
