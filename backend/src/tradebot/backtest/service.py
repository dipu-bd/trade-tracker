from datetime import UTC, date, datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.backtest.report import BacktestReport, summarise
from tradebot.backtest.runner import (
    ReplayRunner,
    buy_and_hold,
    random_entry_control,
    trading_days,
)
from tradebot.backtest.trials import TrialLedger
from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.core.clock import FrozenClock
from tradebot.db.models import Instrument, Portfolio
from tradebot.engine.config import strategy_config
from tradebot.obs import EventRecorder

SANDBOX_PREFIX = "__backtest__"


class BacktestService:
    """Runs a replay in a disposable copy of the portfolio.

    The copy matters: a backtest that wrote into the live portfolio's ledger would corrupt the
    equity curve it is meant to evaluate, and the ledger is append-only by design.
    """

    def __init__(self, events: EventRecorder) -> None:
        self._events = events
        self._trials = TrialLedger()

    @property
    def trials(self) -> TrialLedger:
        return self._trials

    async def run(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        start: date,
        end: date,
        *,
        with_control: bool = True,
    ) -> BacktestReport:
        self._trials.record(strategy_config(portfolio))
        report = BacktestReport(start=start, end=end, trials=self._trials.trials)

        days = await trading_days(session, portfolio.benchmark, start, end)
        if len(days) < 2:
            report.notes.append(
                f"No usable calendar for {portfolio.benchmark} between {start} and {end}."
            )
            return report

        sandbox = await self._sandbox(session, portfolio, days[0])
        result = await ReplayRunner(self._events).run(
            session, sandbox, days, label=f"rules:{portfolio.name}"
        )
        report.strategies.append(summarise(result, self._trials.trials))

        benchmark = await buy_and_hold(
            session, portfolio.benchmark, days, capital=float(portfolio.initial_capital)
        )
        report.benchmark = summarise(benchmark, 1)

        if with_control:
            symbols = await self._universe_symbols(session, portfolio)
            if symbols:
                control = await random_entry_control(
                    session, symbols, days, capital=float(portfolio.initial_capital)
                )
                report.control = summarise(control, 1)

        report.notes.append(
            "Costs applied: the portfolio's own slippage and commission, plus the square-root "
            "impact term where participation is known."
        )
        await self._cleanup(session, sandbox)
        return report

    async def _sandbox(self, session: AsyncSession, portfolio: Portfolio, start: date) -> Portfolio:
        clock = FrozenClock(_at(start))
        broker = BrokerService(Ledger(clock=clock), self._events, clock=clock)

        copy = await broker.create_portfolio(
            session,
            user_id=portfolio.user_id,
            name=f"{SANDBOX_PREFIX}{portfolio.id}:{start.isoformat()}",
            initial_capital=portfolio.initial_capital,
            slippage_bps=portfolio.slippage_bps,
            commission_bps=portfolio.commission_bps,
            min_commission=portfolio.min_commission,
            allow_fractional=portfolio.allow_fractional,
        )
        copy.benchmark = portfolio.benchmark
        copy.strategy = portfolio.strategy
        copy.universe = portfolio.universe
        copy.cadence = portfolio.cadence
        copy.ai_enabled = False
        await session.flush()
        return copy

    async def _cleanup(self, session: AsyncSession, sandbox: Portfolio) -> None:
        await session.delete(sandbox)
        await session.flush()

    async def _universe_symbols(self, session: AsyncSession, portfolio: Portfolio) -> list[str]:
        classes = (portfolio.universe or {}).get("asset_classes") or ["stock", "etf"]
        rows = await session.scalars(
            select(Instrument.symbol)
            .where(
                Instrument.asset_class.in_(classes),
                Instrument.first_bar_date.is_not(None),
            )
            .limit(25)
        )
        return list(rows)


def _at(day: date) -> datetime:
    return datetime.combine(day, time(21, 0), tzinfo=UTC)
