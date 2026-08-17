from sqlalchemy import select

from tradebot.context import AppContext
from tradebot.core.logging import get_logger
from tradebot.db.models import Instrument, Portfolio
from tradebot.marketdata import calendar
from tradebot.marketdata.service import IngestReport, MarketDataService
from tradebot.providers.base import AssetClass

_log = get_logger(__name__)


class MarketDataRefresher:
    """Keeps stored bars current for instruments the engine already tracks.

    It never adds instruments. Which names are tracked is a deliberate choice made through the
    market sync endpoint, and a background job that quietly widened the universe would change
    what the strategy trades without anyone asking it to.
    """

    def __init__(self, context: AppContext) -> None:
        self._context = context

    def _quotable(self, instrument: Instrument) -> bool:
        return calendar.is_open(self._context.clock.now(), AssetClass(instrument.asset_class))

    async def refresh_all(self) -> IngestReport:
        """Bars for anything gone stale, and a fresh price for everything tracked.

        Quotes for a closed exchange would spend a provider call to restate yesterday's close,
        so equities are skipped outside their session while 24/7 sleeves always run.
        """
        total = IngestReport()

        async with self._context.db.session() as session:
            instruments = list(
                await session.scalars(
                    select(Instrument).where(
                        Instrument.is_active.is_(True),
                        Instrument.first_bar_date.is_not(None),
                    )
                )
            )
            owners = list(await session.scalars(select(Portfolio.user_id).distinct()))

        if not instruments or not owners:
            _log.info("market_refresh_skipped", instruments=len(instruments), owners=len(owners))
            return total

        for user_id in owners:
            async with self._context.db.session() as session:
                router = await self._context.providers.build_router(session, user_id)
                service = MarketDataService(router, self._context.events, clock=self._context.clock)
                # force=False, so a second owner's pass skips everything the first already made
                # fresh rather than spending another provider call on it.
                report = await service.refresh_bars(session, instruments)
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
