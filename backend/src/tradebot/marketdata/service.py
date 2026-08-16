from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.logging import get_logger
from tradebot.db.models import Instrument, PriceBar
from tradebot.marketdata import calendar
from tradebot.obs import EventRecorder
from tradebot.providers.base import (
    AssetClass,
    Bar,
    Capability,
    Provider,
    ProviderUnavailableError,
    Quote,
    UniverseEntry,
)
from tradebot.providers.router import ProviderRouter

_log = get_logger(__name__)

DEFAULT_HISTORY_DAYS = 260
MAX_STALE_DAYS = 1


@dataclass
class IngestReport:
    instruments: int = 0
    bars_written: int = 0
    skipped_fresh: int = 0
    failed: list[str] = field(default_factory=list)
    gaps: dict[str, int] = field(default_factory=dict)


class MarketDataService:
    """Owns the instrument and bar store, and is the only thing that talks to the router.

    Everything downstream reads from the database, so a provider outage degrades to stale data
    rather than an exception in the middle of a decision cycle.
    """

    def __init__(
        self,
        router: ProviderRouter,
        events: EventRecorder,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._router = router
        self._events = events
        self._clock = clock or LiveClock()

    def _today(self) -> date:
        return self._clock.now().date()

    async def sync_universe(
        self, session: AsyncSession, asset_class: AssetClass, *, limit: int = 200
    ) -> list[Instrument]:
        async def call(provider: Provider) -> list[UniverseEntry]:
            found: list[UniverseEntry] = await provider.list_universe(asset_class)  # type: ignore[attr-defined]
            return found

        entries = await self._router.execute(Capability.UNIVERSE, call, asset_class=asset_class)
        return await self.upsert_instruments(session, entries[:limit])

    async def upsert_instruments(
        self, session: AsyncSession, entries: Sequence[UniverseEntry]
    ) -> list[Instrument]:
        if not entries:
            return []

        symbols = [entry.symbol for entry in entries]
        existing = {
            (row.symbol, row.asset_class): row
            for row in await session.scalars(
                select(Instrument).where(Instrument.symbol.in_(symbols))
            )
        }

        instruments: list[Instrument] = []
        for entry in entries:
            key = (entry.symbol, entry.asset_class.value)
            instrument = existing.get(key)
            if instrument is None:
                instrument = Instrument(symbol=entry.symbol, asset_class=entry.asset_class.value)
                session.add(instrument)
            instrument.name = entry.name or instrument.name
            instrument.exchange = entry.exchange or instrument.exchange
            instrument.sector = entry.sector or instrument.sector
            instrument.currency = entry.currency or instrument.currency
            instrument.is_active = True
            instruments.append(instrument)

        await session.flush()
        return instruments

    async def refresh_bars(
        self,
        session: AsyncSession,
        instruments: Sequence[Instrument],
        *,
        days: int = DEFAULT_HISTORY_DAYS,
        force: bool = False,
    ) -> IngestReport:
        report = IngestReport(instruments=len(instruments))

        for instrument in instruments:
            if not force and not self._is_stale(instrument):
                report.skipped_fresh += 1
                continue

            asset_class = AssetClass(instrument.asset_class)

            async def call(provider: Provider, symbol: str = instrument.symbol) -> list[Bar]:
                found: list[Bar] = await provider.get_bars(symbol, days=days)  # type: ignore[attr-defined]
                return found

            try:
                bars = await self._router.execute(Capability.BARS, call, asset_class=asset_class)
            except ProviderUnavailableError as exc:
                report.failed.append(instrument.symbol)
                _log.warning("bar_refresh_failed", symbol=instrument.symbol, error=str(exc))
                await self._events.record(
                    session,
                    domain="market",
                    kind="bar_refresh_failed",
                    severity="warning",
                    message=instrument.symbol,
                )
                continue

            written = await self._store_bars(session, instrument, bars)
            report.bars_written += written

            missing = self._detect_gaps(bars, asset_class)
            if missing:
                report.gaps[instrument.symbol] = missing
                await self._events.record(
                    session,
                    domain="market",
                    kind="bar_gap_detected",
                    severity="warning",
                    message=instrument.symbol,
                    payload={"missing_sessions": missing},
                )

        return report

    async def track_symbols(
        self,
        session: AsyncSession,
        symbols: Sequence[str],
        asset_class: AssetClass,
        *,
        days: int = DEFAULT_HISTORY_DAYS,
    ) -> tuple[list[Instrument], IngestReport]:
        """Track named symbols directly, rather than whatever a provider's listing returns.

        A provider's universe endpoint is a ranked list — most-actives and the like — so a name
        outside it could never be tracked at all. A symbol whose bars do not arrive is removed
        again rather than left behind: an instrument with no history is invisible to the screen
        but still shows up as tracked, which reads as a silent failure.
        """
        wanted = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        names = await self._company_names(wanted)
        entries = [
            UniverseEntry(symbol=symbol, asset_class=asset_class, name=names.get(symbol, ""))
            for symbol in wanted
        ]
        instruments = await self.upsert_instruments(session, entries)
        report = await self.refresh_bars(session, instruments, days=days, force=True)

        kept: list[Instrument] = []
        for instrument in instruments:
            if instrument.first_bar_date is None:
                await session.delete(instrument)
                if instrument.symbol not in report.failed:
                    report.failed.append(instrument.symbol)
            else:
                kept.append(instrument)

        await session.flush()
        report.instruments = len(kept)
        return kept, report

    async def _company_names(self, symbols: Sequence[str]) -> dict[str, str]:
        """Display names, best-effort. A tracked symbol with no name is still tracked."""
        for provider in self._router.providers:
            if not provider.available:
                continue
            try:
                found: dict[str, str] = await provider.company_names(symbols)
            except Exception as error:
                _log.warning("company_names_failed", provider=provider.key, error=str(error))
                continue
            if found:
                return found
        return {}

    def _is_stale(self, instrument: Instrument) -> bool:
        if instrument.last_bar_date is None:
            return True
        asset_class = AssetClass(instrument.asset_class)
        latest = calendar.previous_trading_day(self._today(), asset_class)
        return (latest - instrument.last_bar_date).days > MAX_STALE_DAYS

    async def _store_bars(
        self, session: AsyncSession, instrument: Instrument, bars: Sequence[Bar]
    ) -> int:
        if not bars:
            return 0

        existing = {
            row.bar_date: row
            for row in await session.scalars(
                select(PriceBar).where(
                    PriceBar.instrument_id == instrument.id,
                    PriceBar.bar_date >= bars[0].bar_date,
                )
            )
        }

        written = 0
        for bar in bars:
            row = existing.get(bar.bar_date)
            if row is None:
                session.add(
                    PriceBar(
                        instrument_id=instrument.id,
                        bar_date=bar.bar_date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        adjusted=True,
                    )
                )
                written += 1
            elif row.close != bar.close or row.volume != bar.volume:
                # The most recent session keeps changing until the close is final.
                row.open, row.high, row.low = bar.open, bar.high, bar.low
                row.close, row.volume = bar.close, bar.volume
                written += 1

        instrument.first_bar_date = min(
            bars[0].bar_date, instrument.first_bar_date or bars[0].bar_date
        )
        instrument.last_bar_date = max(
            bars[-1].bar_date, instrument.last_bar_date or bars[-1].bar_date
        )
        await session.flush()
        return written

    def _detect_gaps(self, bars: Sequence[Bar], asset_class: AssetClass) -> int:
        if len(bars) < 2:
            return 0
        expected = calendar.expected_session_count(bars[0].bar_date, bars[-1].bar_date, asset_class)
        return max(0, expected - len(bars))

    async def get_quotes(
        self, session: AsyncSession, instruments: Sequence[Instrument]
    ) -> dict[str, Quote]:
        by_class: dict[AssetClass, list[Instrument]] = {}
        for instrument in instruments:
            by_class.setdefault(AssetClass(instrument.asset_class), []).append(instrument)

        quotes: dict[str, Quote] = {}
        for asset_class, group in by_class.items():
            symbols = [row.symbol for row in group]

            async def call(provider: Provider, wanted: list[str] = symbols) -> dict[str, Quote]:
                found: dict[str, Quote] = await provider.get_quotes(wanted)  # type: ignore[attr-defined]
                return found

            try:
                fetched = await self._router.execute(
                    Capability.QUOTES, call, asset_class=asset_class
                )
            except ProviderUnavailableError as exc:
                _log.warning("quote_fetch_failed", asset_class=asset_class.value, error=str(exc))
                await self._events.record(
                    session,
                    domain="market",
                    kind="quote_fetch_failed",
                    severity="warning",
                    message=asset_class.value,
                )
                continue

            quotes.update(fetched)
            await self._mark_quotes(group, fetched)

        return quotes

    async def _mark_quotes(
        self, instruments: Sequence[Instrument], quotes: dict[str, Quote]
    ) -> None:
        source = next((a.provider_key for a in self._router.last_attempts if a.ok), None)
        for instrument in instruments:
            quote = quotes.get(instrument.symbol)
            if quote is None:
                continue
            instrument.last_quote_at = quote.at
            instrument.last_quote_price = quote.price
            instrument.last_quote_source = source

    async def load_bars(
        self, session: AsyncSession, instrument: Instrument, *, limit: int = DEFAULT_HISTORY_DAYS
    ) -> list[PriceBar]:
        rows = await session.scalars(
            select(PriceBar)
            .where(PriceBar.instrument_id == instrument.id)
            .order_by(PriceBar.bar_date.desc())
            .limit(limit)
        )
        return sorted(rows, key=lambda row: row.bar_date)

    async def stale_instruments(
        self, session: AsyncSession, asset_class: AssetClass | None = None
    ) -> list[Instrument]:
        stmt = select(Instrument).where(Instrument.is_active.is_(True))
        if asset_class is not None:
            stmt = stmt.where(Instrument.asset_class == asset_class.value)
        rows = await session.scalars(stmt)
        return [row for row in rows if self._is_stale(row)]

    def staleness_seconds(self, instrument: Instrument) -> float | None:
        if instrument.last_quote_at is None:
            return None
        return (self._clock.now() - instrument.last_quote_at).total_seconds()


def dollar_volume(bars: Iterable[Bar]) -> Decimal:
    total = Decimal(0)
    count = 0
    for bar in bars:
        total += bar.close * bar.volume
        count += 1
    return total / count if count else Decimal(0)


def bar_span(bars: Sequence[Bar]) -> timedelta:
    return bars[-1].bar_date - bars[0].bar_date if len(bars) >= 2 else timedelta(0)
