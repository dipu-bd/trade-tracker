from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.context import AppContext
from tradebot.core.logging import get_logger
from tradebot.db.models import Instrument, Portfolio
from tradebot.marketdata import calendar
from tradebot.marketdata.jobs import ProgressFn
from tradebot.marketdata.service import DEFAULT_HISTORY_DAYS, IngestReport, MarketDataService
from tradebot.providers.base import AssetClass, ProviderError

_log = get_logger(__name__)

DISCOVERABLE = (AssetClass.ETF, AssetClass.STOCK, AssetClass.CRYPTO, AssetClass.COMMODITY)


def _noop(current: str, done: int, total: int) -> None:
    return None


class MarketSync:
    """The one path that fills the store, whether a person asked or the schedule did."""

    def __init__(self, context: AppContext) -> None:
        self._context = context

    async def discover(
        self,
        user_id: int,
        *,
        asset_classes: Sequence[AssetClass] = DISCOVERABLE,
        symbols: Sequence[str] = (),
        limit: int = 200,
        days: int = DEFAULT_HISTORY_DAYS,
        progress: ProgressFn = _noop,
    ) -> IngestReport:
        """Walk every requested asset class, then pull bars and prices for what comes back.

        One class failing is reported and the rest still run: a stock listing that 402s should
        not cost you the ETF and crypto sleeves in the same pass.
        """
        total = IngestReport()

        for asset_class in asset_classes:

            def stage(symbol: str, done: int, count: int, name: str = asset_class.value) -> None:
                progress(f"{name} {symbol}".strip(), done, count)

            async with self._context.db.session() as session:
                service = await self._service(session, user_id)
                try:
                    _, report = await service.sync(
                        session,
                        asset_class,
                        symbols=symbols,
                        limit=limit,
                        days=days,
                        progress=stage,
                    )
                except ProviderError as error:
                    _log.warning(
                        "universe_sync_failed", asset_class=asset_class.value, error=str(error)
                    )
                    total.failed.append(f"{asset_class.value}: {error}")
                    continue
            total.absorb(report)

        return total

    async def refresh_all(self, *, progress: ProgressFn = _noop) -> IngestReport:
        """Bars for anything gone stale, and a fresh price for everything already tracked."""
        total = IngestReport()

        async with self._context.db.session() as session:
            tracked = await session.scalar(
                select(func.count(Instrument.id)).where(Instrument.is_active.is_(True))
            )
            owners = await self._owners(session)

        if not tracked or not owners:
            _log.info("market_refresh_skipped", instruments=tracked or 0, owners=len(owners))
            return total

        for user_id in owners:
            async with self._context.db.session() as session:
                # Re-read inside the session that will commit. The refresh stamps the last
                # quote and bar dates onto the instrument row, and a row loaded by a session
                # that has since closed is detached — those writes went nowhere, so every
                # `last_quote_price` stayed blank and the matching pass had no price to fill
                # a resting order against.
                instruments = await self._tracked(session)
                service = await self._service(session, user_id)
                # force=False, so a second owner's pass skips everything the first already made
                # fresh rather than spending another provider call on it.
                report = await service.refresh_bars(session, instruments, progress=progress)
                quotable = [row for row in instruments if self._quotable(row)]
                if quotable:
                    report.quotes_updated = await service.refresh_quotes(session, quotable)

            total.absorb(report)

        _log.info(
            "market_refresh_finished",
            bars_written=total.bars_written,
            quotes_updated=total.quotes_updated,
            skipped=total.skipped_fresh,
            failed=len(total.failed),
        )
        return total

    async def scheduled(self, *, progress: ProgressFn = _noop) -> IngestReport:
        """What the cron runs: discovery for every owner, then a refresh of everything tracked."""
        settings = self._context.settings
        total = IngestReport()

        if settings.market_discovery_enabled:
            async with self._context.db.session() as session:
                owners = await self._owners(session)
            for user_id in owners:
                total.absorb(
                    await self.discover(
                        user_id, limit=settings.market_universe_limit, progress=progress
                    )
                )

        total.absorb(await self.refresh_all(progress=progress))
        return total

    async def _owners(self, session: AsyncSession) -> list[int]:
        return list(await session.scalars(select(Portfolio.user_id).distinct()))

    async def _tracked(self, session: AsyncSession) -> list[Instrument]:
        rows = await session.scalars(
            # Deliberately not filtered on first_bar_date: an instrument with no bars is the
            # one that most needs a fetch, and excluding it meant a name whose first bar fetch
            # failed could never recover.
            select(Instrument).where(Instrument.is_active.is_(True))
        )
        return list(rows)

    async def _service(self, session: AsyncSession, user_id: int) -> MarketDataService:
        router = await self._context.providers.build_router(session, user_id)
        return MarketDataService(router, self._context.events, clock=self._context.clock)

    def _quotable(self, instrument: Instrument) -> bool:
        """Open markets always; a closed one only until its last close is on record.

        Polling a shut exchange every 15 minutes spends provider calls to restate the same
        close, but skipping it outright left every equity blank until someone happened to look
        during US hours.
        """
        asset_class = AssetClass(instrument.asset_class)
        now = self._context.clock.now()
        if calendar.is_open(now, asset_class):
            return True
        if calendar.is_24x7(asset_class):
            return True
        return instrument.last_quote_at is None or instrument.last_quote_at < calendar.last_close(
            now
        )
