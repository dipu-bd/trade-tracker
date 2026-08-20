from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.logging import get_logger
from tradebot.db.models import (
    Instrument,
    Order,
    OrderStatus,
    Portfolio,
    Position,
    PositionStatus,
)
from tradebot.engine.triggers import StopTrigger
from tradebot.marketdata import calendar
from tradebot.providers.base import AssetClass, Quote

_log = get_logger(__name__)

# A quote older than this is not a price you could trade at, however recently the refresh ran.
MIN_QUOTE_AGE = timedelta(minutes=30)


@dataclass
class MatchPassReport:
    portfolios: int = 0
    filled: int = 0
    expired: int = 0
    stops: int = 0
    waiting: dict[str, str] = field(default_factory=dict)

    def absorb(self, other: "MatchPassReport") -> None:
        self.portfolios += other.portfolios
        self.filled += other.filled
        self.expired += other.expired
        self.stops += other.stops
        self.waiting.update(other.waiting)


class MatchingPass:
    """Drives resting orders against the latest stored quote.

    The decision cycle places orders and the market sync refreshes prices, but until this ran
    nothing joined the two: `BrokerService.on_quote` is the only code that produces a fill and
    its only caller was the backtest runner, so a live order sat ACCEPTED forever, no position
    ever closed and no stop ever fired.

    Matching is gated on the venue actually being open. Filling a market order against the last
    close while the exchange is shut invents a price you could not have got, and the order's own
    expiry already assumes it rests until the next session. Crypto is 24/7 and so fills on the
    very next pass.
    """

    def __init__(self, context: AppContext) -> None:
        self._context = context
        # Two refresh intervals, so one skipped or slow market pass does not stall matching,
        # but a genuinely dead feed still stops it rather than trading on a stale price.
        self._max_age = max(
            MIN_QUOTE_AGE, timedelta(minutes=2 * context.settings.market_refresh_minutes)
        )

    async def run(self, portfolio_id: int | None = None) -> MatchPassReport:
        """Match one portfolio, or every portfolio with something to work."""
        total = MatchPassReport()
        if portfolio_id is not None:
            candidates = [portfolio_id]
        else:
            async with self._context.db.session() as session:
                candidates = await self._candidates(session)

        for candidate in candidates:
            try:
                # A session each, so one portfolio's failure cannot roll back another's fills.
                async with self._context.db.session() as session:
                    total.absorb(await self._match(session, candidate))
            except Exception as error:
                _log.warning("match_failed", portfolio_id=candidate, error=str(error))
        return total

    async def _candidates(self, session: AsyncSession) -> list[int]:
        """Portfolios with a working order, or a stop that a quote could breach."""
        working = await session.scalars(
            select(Order.portfolio_id).where(Order.status.in_(sorted(OrderStatus.OPEN))).distinct()
        )
        stopped = await session.scalars(
            select(Position.portfolio_id)
            .where(
                Position.status == PositionStatus.OPEN,
                Position.stop_price.is_not(None),
                Position.qty > 0,
            )
            .distinct()
        )
        return sorted(set(working) | set(stopped))

    async def _match(self, session: AsyncSession, portfolio_id: int) -> MatchPassReport:
        report = MatchPassReport()
        portfolio = await session.get(Portfolio, portfolio_id)
        if portfolio is None or not portfolio.is_active:
            return report

        report.portfolios = 1
        broker = BrokerService(
            Ledger(clock=self._context.clock), self._context.events, clock=self._context.clock
        )
        now = self._context.clock.now()

        # Stops first: a level already breached should exit before anything new is worked, and
        # the exit order it places is then matched by the loop below within this same pass.
        trigger = StopTrigger(broker, self._context.events, self._context.clock)
        for instrument, quote in self._priced(
            await self._stopped(session, portfolio_id), now, report
        ):
            report.stops += len(await trigger.on_quote(session, instrument.symbol, quote.price))

        for instrument, quote in self._priced(
            await self._working(session, portfolio_id), now, report
        ):
            result = await broker.on_quote(session, portfolio, instrument, quote)
            report.filled += len(result.fills)
            report.expired += len(result.expired)

        if report.filled or report.expired or report.stops:
            await self._context.events.record(
                session,
                domain="broker",
                kind="orders_matched",
                user_id=portfolio.user_id,
                portfolio_id=portfolio.id,
                message=f"{report.filled} filled, {report.expired} expired, {report.stops} stopped",
                payload={
                    "filled": report.filled,
                    "expired": report.expired,
                    "stops": report.stops,
                },
            )
        return report

    async def _working(self, session: AsyncSession, portfolio_id: int) -> list[Instrument]:
        return list(
            await session.scalars(
                select(Instrument)
                .join(Order, Order.instrument_id == Instrument.id)
                .where(
                    Order.portfolio_id == portfolio_id,
                    Order.status.in_(sorted(OrderStatus.OPEN)),
                )
                .distinct()
            )
        )

    async def _stopped(self, session: AsyncSession, portfolio_id: int) -> list[Instrument]:
        return list(
            await session.scalars(
                select(Instrument)
                .join(Position, Position.instrument_id == Instrument.id)
                .where(
                    Position.portfolio_id == portfolio_id,
                    Position.status == PositionStatus.OPEN,
                    Position.stop_price.is_not(None),
                    Position.qty > 0,
                )
                .distinct()
            )
        )

    def _priced(
        self, instruments: list[Instrument], now: datetime, report: MatchPassReport
    ) -> list[tuple[Instrument, Quote]]:
        """Pair each instrument with a quote it can actually trade at, recording why not."""
        out: list[tuple[Instrument, Quote]] = []
        for instrument in instruments:
            quote = self._quote(instrument, now)
            if isinstance(quote, str):
                report.waiting[instrument.symbol] = quote
                continue
            out.append((instrument, quote))
        return out

    def _quote(self, instrument: Instrument, now: datetime) -> Quote | str:
        """A quote this instrument can be matched against, or why it cannot be.

        A reason rather than a bare None: an order resting with no explanation is the failure
        this whole pass exists to fix, so the reason reaches the caller and the event feed.
        """
        price, at = instrument.last_quote_price, instrument.last_quote_at
        if price is None or at is None:
            return "no quote on record"
        if price <= 0:
            return "quoted at or below zero"

        if not calendar.is_open(now, AssetClass(instrument.asset_class)):
            return "market closed"

        age = now - at
        if age > self._max_age:
            return f"quote is {int(age.total_seconds() // 60)}m old"
        return Quote(symbol=instrument.symbol, price=price, at=at)
