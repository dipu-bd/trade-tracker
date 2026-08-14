"""US equities and ETFs via Financial Modeling Prep.

Written for the free tier: quotes are batched (one call covers the whole
universe), daily bars are fetched incrementally, and endpoints that require a
paid plan are attempted once and then remembered as unavailable for the rest
of the day — the same 401/402/403 degradation the reference gap scanner uses.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from marketbot.dto.market import Bar, Quote, UniverseEntry
from marketbot.utils.retry_session import RetrySession

from .base import MarketDataProvider, RequestBudget, UnlimitedBudget

_log = logging.getLogger(__name__)

BASE_URL = 'https://financialmodelingprep.com/api/v3'
PLAN_DENIED = (401, 402, 403)
QUOTE_BATCH_SIZE = 40


class FMPProvider(MarketDataProvider):
    def __init__(
        self,
        api_key: str,
        budget: Optional[RequestBudget] = None,
        session: Optional[RetrySession] = None,
    ):
        self.api_key = api_key
        self.budget = budget or UnlimitedBudget()
        self._session = session or RetrySession()
        self._denied: set = set()

    @property
    def name(self) -> str:
        return 'fmp'

    @property
    def asset_classes(self) -> Sequence[str]:
        return ('STOCK', 'ETF')

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # ----------------------------------------------------------------- #
    # HTTP
    # ----------------------------------------------------------------- #

    def _get(self, path: str, **params) -> Any:
        if not self.api_key:
            return None
        if path in self._denied:
            return None
        if not self.budget.try_spend():
            _log.warning(f'FMP daily request budget exhausted; skipping {path}')
            return None

        params['apikey'] = self.api_key
        try:
            resp = self._session.request(
                'GET', f'{BASE_URL}{path}', params=params, timeout=20
            )
        except Exception as e:  # noqa: BLE001 — network/HTTP both land here
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status in PLAN_DENIED:
                _log.info(f'FMP {path} not available on this plan ({status}); skipping')
                self._denied.add(path)
            else:
                _log.warning(f'FMP request failed for {path}: {e}')
            return None

        try:
            return resp.json()
        except ValueError:
            _log.warning(f'FMP returned non-JSON for {path}')
            return None

    # ----------------------------------------------------------------- #
    # Universe
    # ----------------------------------------------------------------- #

    def list_universe(self) -> List[UniverseEntry]:
        """Movers from the screener endpoints, when the plan allows them.

        Returns an empty list on the free tier; the curated static universe in
        `services/universe.py` is the baseline either way.
        """
        entries: Dict[str, UniverseEntry] = {}
        for path in ('/stock_market/gainers', '/stock_market/actives'):
            rows = self._get(path)
            if not isinstance(rows, list):
                continue
            for row in rows:
                symbol = str(row.get('symbol') or '').upper()
                if not symbol or symbol in entries:
                    continue
                entries[symbol] = UniverseEntry(
                    symbol=symbol,
                    asset_class='STOCK',
                    name=str(row.get('name') or '')[:120],
                )
        return list(entries.values())

    # ----------------------------------------------------------------- #
    # Quotes
    # ----------------------------------------------------------------- #

    def get_quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        wanted = [s.upper() for s in symbols if s]
        quotes: Dict[str, Quote] = {}
        for i in range(0, len(wanted), QUOTE_BATCH_SIZE):
            batch = wanted[i:i + QUOTE_BATCH_SIZE]
            rows = self._get('/quote/' + ','.join(batch))
            if not isinstance(rows, list):
                continue
            for row in rows:
                quote = self._parse_quote(row)
                if quote:
                    quotes[quote.symbol] = quote
        return quotes

    def _parse_quote(self, row: dict) -> Optional[Quote]:
        symbol = str(row.get('symbol') or '').upper()
        price = _as_float(row.get('price'))
        if not symbol or price <= 0:
            return None
        return Quote(
            symbol=symbol,
            price=price,
            previous_close=_as_float(row.get('previousClose')),
            day_high=_as_float(row.get('dayHigh')),
            day_low=_as_float(row.get('dayLow')),
            volume=_as_float(row.get('volume')),
            avg_volume=_as_float(row.get('avgVolume')),
            year_high=_as_float(row.get('yearHigh')),
            name=str(row.get('name') or '')[:120],
            exchange=str(row.get('exchange') or '')[:32],
        )

    # ----------------------------------------------------------------- #
    # Bars
    # ----------------------------------------------------------------- #

    def get_daily_bars(self, symbol: str, days: int = 260) -> List[Bar]:
        end = date.today()
        # Pad generously for weekends and market holidays.
        start = end - timedelta(days=int(days * 1.6) + 10)
        data = self._get(
            f'/historical-price-full/{symbol.upper()}',
            **{'from': start.isoformat(), 'to': end.isoformat()},
        )

        rows: List[dict] = []
        if isinstance(data, dict):
            rows = data.get('historical') or []
        elif isinstance(data, list):
            rows = data

        bars: List[Bar] = []
        for row in rows:
            bar = _parse_bar(row)
            if bar:
                bars.append(bar)

        # FMP returns newest-first; the indicators want oldest-first.
        bars.sort(key=lambda b: b.bar_date)
        return bars[-days:]


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_bar(row: dict) -> Optional[Bar]:
    raw_date = row.get('date')
    if not raw_date:
        return None
    try:
        bar_date = datetime.strptime(str(raw_date)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None

    close = _as_float(row.get('close'))
    if close <= 0:
        return None
    return Bar(
        bar_date=bar_date,
        open=_as_float(row.get('open')) or close,
        high=_as_float(row.get('high')) or close,
        low=_as_float(row.get('low')) or close,
        close=close,
        volume=_as_float(row.get('volume')),
    )
