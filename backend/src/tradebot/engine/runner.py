import asyncio
from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.ai.analysts import AnalystCache
from tradebot.ai.client import AIClient
from tradebot.ai.pipeline import AIPipeline
from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.logging import get_logger
from tradebot.db.models import Portfolio
from tradebot.engine.cycle import CycleReport, DecisionCycle
from tradebot.workers.matching import MatchingPass

_log = get_logger(__name__)

LOCK_NAMESPACE = 0x7472  # "tr"


class EngineRunner:
    """Runs decision cycles, one portfolio at a time.

    A cron firing while a manual run is in flight is the race that matters, so a portfolio is
    serialized by a Postgres advisory lock. SQLite is single-writer already, so it takes the
    in-process lock alone and skips the advisory call.
    """

    def __init__(self, context: AppContext) -> None:
        self._context = context
        self._locks: dict[int, asyncio.Lock] = {}
        self._client = AIClient(clock=context.clock)
        self._matching = MatchingPass(context)
        self._cache = AnalystCache()

    def _cycle(self, keys: dict[str, str]) -> DecisionCycle:
        clock = self._context.clock
        broker = BrokerService(Ledger(clock=clock), self._context.events, clock=clock)
        return DecisionCycle(
            broker,
            self._context.events,
            clock,
            ai=AIPipeline(self._client, clock, self._cache),
            keys=keys,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run_portfolio(self, portfolio_id: int, trigger: str = "scheduled") -> CycleReport:
        lock = self._locks.setdefault(portfolio_id, asyncio.Lock())
        async with lock, self._context.db.session() as session:
            if not await self._acquire(session, portfolio_id):
                raise RuntimeError(f"portfolio {portfolio_id} already has a cycle running")

            portfolio = await session.get(Portfolio, portfolio_id)
            if portfolio is None:
                raise RuntimeError(f"portfolio {portfolio_id} not found")
            if not portfolio.is_active:
                # The cron already filters on this; a manual run has to as well, or a paused
                # portfolio still places orders that nothing will ever match.
                raise RuntimeError(f"portfolio {portfolio_id} is paused")

            keys = await self._context.providers.llm_keys(session, portfolio.user_id)
            report = await self._cycle(keys).run(session, portfolio, trigger=trigger)

        # Outside the cycle's session and lock: the orders it just placed should be worked now
        # rather than at the next interval, but a matching failure must not undo the cycle.
        await self._match(portfolio_id)
        return report

    async def _match(self, portfolio_id: int) -> None:
        try:
            await self._matching.run(portfolio_id)
        except Exception as error:
            _log.warning("post_cycle_match_failed", portfolio_id=portfolio_id, error=str(error))

    async def run_due(self, cadence: str) -> list[CycleReport]:
        async with self._context.db.session() as session:
            due = await self._due(session, cadence)

        reports: list[CycleReport] = []
        for portfolio_id in due:
            try:
                reports.append(await self.run_portfolio(portfolio_id, trigger=f"cron:{cadence}"))
            except Exception as error:
                _log.warning("cycle_failed", portfolio_id=portfolio_id, error=str(error))
        return reports

    async def _due(self, session: AsyncSession, cadence: str) -> Sequence[int]:
        rows = await session.scalars(
            select(Portfolio.id).where(
                Portfolio.is_active.is_(True),
                Portfolio.autopilot.is_(True),
                Portfolio.cadence == cadence,
            )
        )
        return list(rows)

    async def _acquire(self, session: AsyncSession, portfolio_id: int) -> bool:
        if session.bind is None or session.bind.dialect.name != "postgresql":
            return True

        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": LOCK_NAMESPACE, "key": portfolio_id},
        )
        return bool(acquired)
