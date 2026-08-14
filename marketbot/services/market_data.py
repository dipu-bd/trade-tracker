"""Caching layer between the data providers and the database.

Daily bars live in `price_bars` and are only refetched when stale, which is
what makes a 250-call/day data plan workable: a scan normally costs one batched
quote call per 40 symbols plus a handful of history refreshes.
"""

import logging
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketbot.db import ApiUsage, AssetClass, Instrument, PriceBar, Sleeve
from marketbot.dto.market import Bar, Quote, UniverseEntry
from marketbot.providers import (
    CryptoComProvider,
    FMPProvider,
    MarketDataProvider,
    RequestBudget,
)
from marketbot.services import universe as universe_lib

_log = logging.getLogger(__name__)


class MarketDataService:
    def __init__(self, ctx):
        self._ctx = ctx
        self._equity: Optional[FMPProvider] = None
        self._crypto: Optional[CryptoComProvider] = None
        self._budget: Optional[RequestBudget] = None

    # ----------------------------------------------------------------- #
    # Providers
    # ----------------------------------------------------------------- #

    @property
    def config(self):
        return self._ctx.config.market

    def budget(self, session: Session) -> RequestBudget:
        if self._budget is None:
            usage = _usage_row(session, 'fmp')
            self._budget = RequestBudget(self.config.fmp_daily_budget, usage.count)
        return self._budget

    def flush_budget(self, session: Session) -> None:
        if self._budget is None:
            return
        usage = _usage_row(session, 'fmp')
        usage.count = self._budget.used
        session.flush()

    def equity_provider(self, session: Session) -> FMPProvider:
        if self._equity is None:
            self._equity = FMPProvider(
                api_key=self.config.fmp_api_key,
                budget=self.budget(session),
            )
        return self._equity

    def crypto_provider(self) -> CryptoComProvider:
        if self._crypto is None:
            self._crypto = CryptoComProvider(
                quote_currency=self.config.crypto_quote_currency,
            )
        return self._crypto

    def provider_for(self, asset_class: str, session: Session) -> MarketDataProvider:
        if asset_class == AssetClass.CRYPTO:
            return self.crypto_provider()
        return self.equity_provider(session)

    # ----------------------------------------------------------------- #
    # Universe
    # ----------------------------------------------------------------- #

    def sync_universe(
        self,
        session: Session,
        sleeve: str,
        enable_stocks: bool = True,
        enable_etfs: bool = True,
        enable_crypto: bool = True,
    ) -> List[Instrument]:
        """Reconcile the configured universe into `instruments` rows."""
        entries: List[UniverseEntry] = []

        if sleeve in (Sleeve.EQUITY, Sleeve.ALL) and (enable_stocks or enable_etfs):
            static = universe_lib.static_equity_universe(enable_stocks, enable_etfs)
            screened: List[UniverseEntry] = []
            provider = self.equity_provider(session)
            if provider.available and enable_stocks:
                screened = provider.list_universe()
            entries.extend(universe_lib.merge_universes(static, screened))

        if sleeve in (Sleeve.CRYPTO, Sleeve.ALL) and enable_crypto:
            entries.extend(self.crypto_provider().list_universe())

        limit = self.config.universe_max_symbols
        if limit > 0:
            entries = entries[:limit]

        instruments: List[Instrument] = []
        for entry in entries:
            instruments.append(self._upsert_instrument(session, entry))
        session.flush()
        return instruments

    def _upsert_instrument(self, session: Session, entry: UniverseEntry) -> Instrument:
        symbol = entry.symbol.upper()
        instrument = session.scalar(
            select(Instrument).where(
                Instrument.symbol == symbol,
                Instrument.asset_class == entry.asset_class,
            )
        )
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                asset_class=entry.asset_class,
                name=entry.name,
                exchange=entry.exchange,
                sector=entry.sector or universe_lib.sector_for(symbol),
            )
            session.add(instrument)
        else:
            instrument.is_active = True
            if entry.name and not instrument.name:
                instrument.name = entry.name
            if entry.sector and not instrument.sector:
                instrument.sector = entry.sector
        return instrument

    # ----------------------------------------------------------------- #
    # Bars
    # ----------------------------------------------------------------- #

    def get_bars(self, session: Session, instrument: Instrument) -> List[Bar]:
        rows = session.scalars(
            select(PriceBar)
            .where(PriceBar.instrument_id == instrument.id)
            .order_by(PriceBar.bar_date)
        ).all()
        return [
            Bar(
                bar_date=row.bar_date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in rows
        ]

    def refresh_bars(
        self,
        session: Session,
        instruments: Sequence[Instrument],
        priority_ids: Iterable[int] = (),
        max_age_days: int = 1,
    ) -> int:
        """Top up cached history, spending the request budget by priority.

        Held positions are refreshed first — a stale stop is worse than a
        missed entry — then everything else in universe order.
        """
        priority = set(priority_ids)
        ordered = sorted(
            instruments,
            key=lambda i: (0 if i.id in priority else 1, i.symbol),
        )

        refreshed = 0
        for instrument in ordered:
            if not self._is_stale(instrument, max_age_days):
                continue
            provider = self.provider_for(instrument.asset_class, session)
            if not provider.available:
                continue
            bars = provider.get_daily_bars(
                instrument.symbol, days=self.config.bar_history_days
            )
            if not bars:
                continue
            self._store_bars(session, instrument, bars)
            refreshed += 1

        session.flush()
        self.flush_budget(session)
        return refreshed

    def _is_stale(self, instrument: Instrument, max_age_days: int) -> bool:
        if instrument.last_bar_date is None:
            return True
        return (date.today() - instrument.last_bar_date) >= timedelta(days=max_age_days)

    def _store_bars(
        self, session: Session, instrument: Instrument, bars: Sequence[Bar]
    ) -> None:
        existing = {
            row.bar_date: row
            for row in session.scalars(
                select(PriceBar).where(PriceBar.instrument_id == instrument.id)
            ).all()
        }
        for bar in bars:
            row = existing.get(bar.bar_date)
            if row is None:
                session.add(PriceBar(
                    instrument_id=instrument.id,
                    bar_date=bar.bar_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                ))
            else:
                # The most recent bar keeps changing until the session closes.
                row.open, row.high = bar.open, bar.high
                row.low, row.close = bar.low, bar.close
                row.volume = bar.volume

        instrument.last_bar_date = max(bar.bar_date for bar in bars)

    # ----------------------------------------------------------------- #
    # Quotes
    # ----------------------------------------------------------------- #

    def get_quotes(
        self, session: Session, instruments: Sequence[Instrument]
    ) -> Dict[str, Quote]:
        by_class: Dict[str, List[str]] = {}
        for instrument in instruments:
            by_class.setdefault(instrument.asset_class, []).append(instrument.symbol)

        quotes: Dict[str, Quote] = {}
        equity_symbols: List[str] = []
        for asset_class, symbols in by_class.items():
            if asset_class == AssetClass.CRYPTO:
                quotes.update(self.crypto_provider().get_quotes(symbols))
            else:
                equity_symbols.extend(symbols)

        if equity_symbols:
            provider = self.equity_provider(session)
            if provider.available:
                quotes.update(provider.get_quotes(equity_symbols))

        self.flush_budget(session)
        return quotes


def _usage_row(session: Session, provider: str) -> ApiUsage:
    today = date.today()
    row = session.scalar(
        select(ApiUsage).where(
            ApiUsage.provider == provider,
            ApiUsage.usage_date == today,
        )
    )
    if row is None:
        row = ApiUsage(provider=provider, usage_date=today, count=0)
        session.add(row)
        session.flush()
    return row
