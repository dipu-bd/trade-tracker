from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.backtest.ic import measure_from_decisions
from tradebot.backtest.report import BacktestReport, leakage_split, summarise
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

        report.signals.append(await measure_from_decisions(session, portfolio.id))

        report.notes.append(
            "Costs applied: the portfolio's own slippage and commission, plus a square-root "
            "impact term sized by each order's share of median daily dollar volume."
        )
        await self._cleanup(session, sandbox)
        return report

    async def ablate(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        start: date,
        end: date,
        arms: dict[str, object] | None = None,
    ) -> BacktestReport:
        """Run each arm over the SAME window with the SAME trial accounting.

        Arms run on different windows, or deflated by different trial counts, would flatter
        whichever arm was tried least — which is the comparison the prior art does not provide
        and the reason this exists at all.
        """
        arms = arms or {"rules_only": None}
        days = await trading_days(session, portfolio.benchmark, start, end)
        report = BacktestReport(start=start, end=end)

        if len(days) < 2:
            report.notes.append(f"No usable calendar for {portfolio.benchmark}.")
            return report

        for name in arms:
            self._trials.record(strategy_config(portfolio))
            del name

        trials = self._trials.trials
        report.trials = trials

        for name, pipeline in arms.items():
            sandbox = await self._sandbox(session, portfolio, days[0])
            sandbox.ai_enabled = pipeline is not None
            result = await ReplayRunner(self._events).run(
                session, sandbox, days, label=name, ai_pipeline=pipeline
            )
            report.strategies.append(summarise(result, trials))
            await self._cleanup(session, sandbox)

        report.benchmark = summarise(
            await buy_and_hold(
                session, portfolio.benchmark, days, capital=float(portfolio.initial_capital)
            ),
            1,
        )
        report.notes.append(
            f"All {len(arms)} arms ran the same {len(days)} sessions and are deflated by the "
            f"same trial count ({trials})."
        )
        return report

    async def ablate_scaling(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        start: date,
        end: date,
    ) -> BacktestReport:
        """Signal alone, scaling alone, both — over identical sessions and one trial count.

        The plan singles this out because replications attribute much of time-series momentum's
        measured benefit to the volatility scaling rather than to the momentum signal. If the
        scaling arm matches or beats the full strategy, that is a finding about *this* strategy
        and the verdict says so rather than burying it.
        """
        arms = {
            "signal_only": {"require_trend": True, "sizing": {"vol_scaling": False}},
            "scaling_only": {"require_trend": False, "sizing": {"vol_scaling": True}},
            "both": {"require_trend": True, "sizing": {"vol_scaling": True}},
        }

        days = await trading_days(session, portfolio.benchmark, start, end)
        report = BacktestReport(start=start, end=end)
        if len(days) < 2:
            report.notes.append(f"No usable calendar for {portfolio.benchmark}.")
            return report

        for _ in arms:
            self._trials.record(strategy_config(portfolio))
        trials = self._trials.trials
        report.trials = trials

        outcomes: dict[str, float] = {}
        for name, overrides in arms.items():
            sandbox = await self._sandbox(session, portfolio, days[0])
            sandbox.strategy = {**(portfolio.strategy or {}), **_merge(overrides)}
            await session.flush()

            result = await ReplayRunner(self._events).run(session, sandbox, days, label=name)
            summary = summarise(result, trials)
            report.strategies.append(summary)
            outcomes[name] = summary.performance.total_return
            await self._cleanup(session, sandbox)

        report.benchmark = summarise(
            await buy_and_hold(
                session, portfolio.benchmark, days, capital=float(portfolio.initial_capital)
            ),
            1,
        )
        report.notes.append(_scaling_verdict(outcomes))
        report.notes.append(
            f"All three arms ran the same {len(days)} sessions under one trial count ({trials})."
        )
        return report

    async def leakage_check(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        cutoff: date,
        span_days: int = 180,
    ) -> dict[str, object]:
        """Run the same strategy either side of the model's training cutoff.

        A large gap is a leakage signal rather than a strategy result: a model asked about a
        window inside its own training data may simply be recalling what happened.
        """
        before_days = await trading_days(
            session, portfolio.benchmark, cutoff - timedelta(days=span_days), cutoff
        )
        after_days = await trading_days(
            session, portfolio.benchmark, cutoff, cutoff + timedelta(days=span_days)
        )

        if len(before_days) < 2 or len(after_days) < 2:
            return {
                "checked": False,
                "reason": "not enough history on both sides of the cutoff",
                "cutoff": cutoff.isoformat(),
            }

        runner = ReplayRunner(self._events)

        pre_sandbox = await self._sandbox(session, portfolio, before_days[0])
        before = await runner.run(session, pre_sandbox, before_days, label="pre_cutoff")
        await self._cleanup(session, pre_sandbox)

        post_sandbox = await self._sandbox(session, portfolio, after_days[0])
        after = await runner.run(session, post_sandbox, after_days, label="post_cutoff")
        await self._cleanup(session, post_sandbox)

        return leakage_split(before, after, cutoff)

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


def _merge(overrides: dict[str, object]) -> dict[str, object]:
    return dict(overrides)


def _scaling_verdict(outcomes: dict[str, float]) -> str:
    """Name which component produced the return, in plain words."""
    if len(outcomes) < 3:
        return "Ablation incomplete."

    signal = outcomes.get("signal_only", 0.0)
    scaling = outcomes.get("scaling_only", 0.0)
    both = outcomes.get("both", 0.0)
    best = max(outcomes, key=lambda key: outcomes[key])

    parts = [
        f"Signal alone {signal:+.2%}, scaling alone {scaling:+.2%}, both {both:+.2%}.",
    ]
    if best == "scaling_only":
        parts.append(
            "The VOLATILITY SCALING is doing the work: it beat the momentum signal alone and the "
            "full strategy. The momentum signal is not earning its place on this window."
        )
    elif best == "signal_only":
        parts.append(
            "The MOMENTUM SIGNAL is doing the work: volatility scaling did not add to it, and "
            "the full strategy did not beat the signal on its own."
        )
    elif scaling >= both * 0.9:
        parts.append(
            "The scaling alone captured most of the full strategy's return, which is what the "
            "replications warn about — treat the momentum signal's contribution as unproven."
        )
    else:
        parts.append("Both components contributed; neither reproduces the result alone.")
    return " ".join(parts)
