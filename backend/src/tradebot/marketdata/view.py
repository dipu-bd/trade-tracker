from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.analytics.features import Features, extract
from tradebot.analytics.series import BarSeries, build_series
from tradebot.core.clock import Clock
from tradebot.core.errors import LookAheadError
from tradebot.db.models import Instrument, PriceBar
from tradebot.marketdata.calendar import session_bounds
from tradebot.providers.base import AssetClass


class MarketView:
    """The only way decision code reads prices.

    Every read is bounded by `clock.now()`, so look-ahead bias is prevented structurally rather
    than by code review. A daily bar becomes visible only once its session has closed — reading
    the bar for the current day would hand the strategy a close that has not happened yet, which
    is the subtlest and most damaging form of the bug.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self._features: dict[str, Features] = {}

    @property
    def now(self) -> datetime:
        return self._clock.now()

    def assert_visible(self, moment: datetime) -> None:
        if moment > self.now:
            raise LookAheadError(
                f"{moment.isoformat()} is after the clock at {self.now.isoformat()}"
            )

    def last_complete_bar_date(self, asset_class: str = AssetClass.STOCK) -> date:
        """The most recent date whose daily bar is finished as of now."""
        now = self.now
        today = now.date()

        if asset_class == AssetClass.CRYPTO:
            return today - timedelta(days=1)

        bounds = session_bounds(today)
        if bounds is not None and now >= bounds[1]:
            return today
        return today - timedelta(days=1)

    async def bars(self, symbol: str, count: int = 400) -> BarSeries:
        instrument = await self._instrument(symbol)
        if instrument is None:
            return BarSeries(symbol.upper(), "", ())

        cutoff = self.last_complete_bar_date(instrument.asset_class)
        rows = await self._session.execute(
            select(
                PriceBar.bar_date,
                PriceBar.open,
                PriceBar.high,
                PriceBar.low,
                PriceBar.close,
                PriceBar.volume,
            )
            .where(PriceBar.instrument_id == instrument.id, PriceBar.bar_date <= cutoff)
            .order_by(PriceBar.bar_date.desc())
            .limit(count)
        )
        return build_series(
            instrument.symbol, instrument.asset_class, [tuple(row) for row in rows.all()]
        )

    async def features(self, symbol: str, count: int = 400) -> Features:
        key = symbol.upper()
        if key not in self._features:
            self._features[key] = extract(await self.bars(key, count))
        return self._features[key]

    async def quote(self, symbol: str) -> Decimal | None:
        """Last known quote, refused if it is stamped after the clock."""
        instrument = await self._instrument(symbol)
        if instrument is None or instrument.last_quote_price is None:
            return None

        if instrument.last_quote_at is not None:
            self.assert_visible(instrument.last_quote_at)

        return instrument.last_quote_price

    async def mark(self, symbol: str) -> Decimal | None:
        """Quote if one is visible, otherwise the last complete daily close."""
        quoted = await self.quote(symbol)
        if quoted is not None:
            return quoted

        series = await self.bars(symbol, 1)
        return Decimal(str(series.last_close)) if len(series) else None

    async def _instrument(self, symbol: str) -> Instrument | None:
        found: Instrument | None = await self._session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        return found

    def reset_cache(self) -> None:
        self._features.clear()
