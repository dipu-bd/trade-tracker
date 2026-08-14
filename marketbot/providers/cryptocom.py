"""Crypto pairs via the Crypto.com Exchange public REST API.

No API key and no request quota, which is why the crypto sleeve keeps working
even with no market-data credentials configured at all.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from marketbot.dto.market import Bar, Quote, UniverseEntry
from marketbot.utils.retry_session import RetrySession

from .base import MarketDataProvider

_log = logging.getLogger(__name__)

BASE_URL = 'https://api.crypto.com/exchange/v1/public'
DAILY_TIMEFRAME = '1D'

# Excluded from the universe: a stablecoin pair has no trend to trade, and
# leveraged tokens decay against their own index.
STABLECOINS = {
    'USDT', 'USDC', 'DAI', 'TUSD', 'BUSD', 'USDD', 'PYUSD', 'FDUSD',
    'USDP', 'GUSD', 'EURT', 'USD',
}
LEVERAGED_MARKERS = ('3L', '3S', '5L', '5S', '2L', '2S')

DEFAULT_TOP_N = 15
MIN_24H_USD_VOLUME = 1_000_000


class CryptoComProvider(MarketDataProvider):
    def __init__(
        self,
        quote_currency: str = 'USD',
        top_n: int = DEFAULT_TOP_N,
        session: Optional[RetrySession] = None,
    ):
        self.quote_currency = quote_currency.upper()
        self.top_n = top_n
        self._session = session or RetrySession()

    @property
    def name(self) -> str:
        return 'cryptocom'

    @property
    def asset_classes(self) -> Sequence[str]:
        return ('CRYPTO',)

    # ----------------------------------------------------------------- #
    # HTTP
    # ----------------------------------------------------------------- #

    def _get(self, path: str, **params) -> Any:
        try:
            resp = self._session.request(
                'GET', f'{BASE_URL}{path}', params=params, timeout=20
            )
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            _log.warning(f'Crypto.com request failed for {path}: {e}')
            return None

        if not isinstance(payload, dict) or payload.get('code') not in (0, None):
            _log.warning(f'Crypto.com error for {path}: {payload}')
            return None
        result = payload.get('result') or {}
        return result.get('data')

    # ----------------------------------------------------------------- #
    # Universe + quotes (one call serves both)
    # ----------------------------------------------------------------- #

    def _tickers(self) -> List[dict]:
        data = self._get('/get-tickers')
        return data if isinstance(data, list) else []

    def _is_tradable(self, instrument: str) -> bool:
        if not instrument or '_' not in instrument:
            return False
        base, _, quote = instrument.partition('_')
        if quote != self.quote_currency:
            return False
        if '-' in instrument:  # perpetuals and futures
            return False
        if base in STABLECOINS:
            return False
        return not any(marker in base for marker in LEVERAGED_MARKERS)

    def list_universe(self) -> List[UniverseEntry]:
        rows = []
        for row in self._tickers():
            instrument = str(row.get('i') or '')
            if not self._is_tradable(instrument):
                continue
            usd_volume = _as_float(row.get('vv'))
            if usd_volume < MIN_24H_USD_VOLUME:
                continue
            rows.append((usd_volume, instrument))

        rows.sort(reverse=True)
        return [
            UniverseEntry(
                symbol=instrument,
                asset_class='CRYPTO',
                name=instrument.replace('_', '/'),
                exchange='CRYPTO.COM',
                sector='Crypto',
            )
            for _, instrument in rows[:self.top_n]
        ]

    def get_quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        wanted = {s.upper() for s in symbols if s}
        quotes: Dict[str, Quote] = {}
        for row in self._tickers():
            instrument = str(row.get('i') or '').upper()
            if instrument not in wanted:
                continue
            price = _as_float(row.get('a'))
            if price <= 0:
                continue

            # `c` is the 24h change as a ratio, so the prior close is implied.
            change_ratio = _as_float(row.get('c'))
            prev_close = price / (1 + change_ratio) if change_ratio > -1 else price

            quotes[instrument] = Quote(
                symbol=instrument,
                price=price,
                previous_close=prev_close,
                day_high=_as_float(row.get('h')),
                day_low=_as_float(row.get('l')),
                volume=_as_float(row.get('v')),
                name=instrument.replace('_', '/'),
                exchange='CRYPTO.COM',
            )
        return quotes

    # ----------------------------------------------------------------- #
    # Bars
    # ----------------------------------------------------------------- #

    def get_daily_bars(self, symbol: str, days: int = 260) -> List[Bar]:
        data = self._get(
            '/get-candlestick',
            instrument_name=symbol.upper(),
            timeframe=DAILY_TIMEFRAME,
            count=min(max(days, 30), 300),
        )
        if not isinstance(data, list):
            return []

        bars: List[Bar] = []
        for row in data:
            bar = _parse_candle(row)
            if bar:
                bars.append(bar)

        bars.sort(key=lambda b: b.bar_date)
        return bars[-days:]


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_candle(row: dict) -> Optional[Bar]:
    timestamp = row.get('t')
    close = _as_float(row.get('c'))
    if timestamp is None or close <= 0:
        return None
    try:
        bar_date = datetime.fromtimestamp(
            int(timestamp) / 1000, tz=timezone.utc
        ).date()
    except (TypeError, ValueError, OSError):
        return None

    return Bar(
        bar_date=bar_date,
        open=_as_float(row.get('o')) or close,
        high=_as_float(row.get('h')) or close,
        low=_as_float(row.get('l')) or close,
        close=close,
        volume=_as_float(row.get('v')),
    )
