from sqlalchemy import select

from tradebot.context import AppContext
from tradebot.core.logging import get_logger
from tradebot.db.models import Instrument, Portfolio
from tradebot.marketdata.service import IngestReport, MarketDataService

_log = get_logger(__name__)


class MarketDataRefresher:
    """Keeps stored bars current for instruments the engine already tracks.

    It never adds instruments. Which names are tracked is a deliberate choice made through the
    market sync endpoint, and a background job that quietly widened the universe would change
    what the strategy trades without anyone asking it to.
    """

    def __init__(self, context: AppContext) -> None:
        self._context = context

    async def refresh_all(self) -> IngestReport:
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
            _log.info("bar_refresh_skipped", instruments=len(instruments), owners=len(owners))
            return total

        for user_id in owners:
            async with self._context.db.session() as session:
                router = await self._context.providers.build_router(session, user_id)
                service = MarketDataService(router, self._context.events, clock=self._context.clock)
                # force=False, so a second owner's pass skips everything the first already made
                # fresh rather than spending another provider call on it.
                report = await service.refresh_bars(session, instruments)

            total.instruments += report.instruments
            total.bars_written += report.bars_written
            total.skipped_fresh += report.skipped_fresh
            total.failed.extend(report.failed)
            total.gaps.update(report.gaps)

        _log.info(
            "bar_refresh_finished",
            bars_written=total.bars_written,
            skipped=total.skipped_fresh,
            failed=len(total.failed),
        )
        return total
