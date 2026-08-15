import json
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import websockets

from tradebot.providers.base import (
    AssetClass,
    Bar,
    Capability,
    ProviderError,
    Quote,
    RateLimits,
    SupportsBars,
    SupportsQuotes,
    SupportsStream,
    SupportsUniverse,
    UniverseEntry,
)
from tradebot.providers.http import HttpProviderMixin
from tradebot.providers.registry import register

STREAM_URL = "wss://stream.binance.com:9443/stream"

# Dollar-pegged quote assets are folded into a single canonical USD leg, so the same coin is one
# instrument regardless of which venue serves it.
DOLLAR_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD")

STABLECOIN_BASES = frozenset(
    {
        "USDT",
        "USDC",
        "DAI",
        "TUSD",
        "BUSD",
        "USDD",
        "PYUSD",
        "FDUSD",
        "USDP",
        "GUSD",
        "EURT",
        "USD",
        "USDE",
        "RLUSD",
        "AEUR",
    }
)
LEVERAGED_MARKERS = ("UP", "DOWN", "BULL", "BEAR")
MIN_24H_QUOTE_VOLUME = Decimal("5000000")
MAX_KLINES = 1000


@register
class BinanceProvider(
    HttpProviderMixin, SupportsUniverse, SupportsQuotes, SupportsBars, SupportsStream
):
    """Binance public market data. Keyless, and far broader than any other free crypto venue.

    Public endpoints are geo-blocked from US IPs; see the deployment note in the README.
    """

    key = "binance"
    label = "Binance"
    base_url = "https://api.binance.com/api/v3"
    capabilities = frozenset(
        {Capability.UNIVERSE, Capability.QUOTES, Capability.BARS, Capability.STREAM}
    )
    asset_classes = frozenset({AssetClass.CRYPTO})
    rate_limits = RateLimits(requests_per_minute=1000, max_concurrency=6)
    default_priority = 5

    stream_url = STREAM_URL

    def to_canonical(self, venue_symbol: str) -> str:
        symbol = venue_symbol.upper()
        for quote in DOLLAR_QUOTES:
            if symbol.endswith(quote) and len(symbol) > len(quote):
                return f"{symbol[: -len(quote)]}-USD"
        return symbol

    def to_venue(self, canonical_symbol: str) -> str:
        base, _, quote = canonical_symbol.upper().partition("-")
        return f"{base}USDT" if quote in {"USD", ""} else f"{base}{quote}"

    def _tradable(self, venue_symbol: str) -> tuple[bool, str]:
        for quote in DOLLAR_QUOTES:
            if venue_symbol.endswith(quote) and len(venue_symbol) > len(quote):
                base = venue_symbol[: -len(quote)]
                break
        else:
            return False, ""
        if _is_dollar_pegged(base):
            return False, base
        if any(base.endswith(marker) for marker in LEVERAGED_MARKERS):
            return False, base
        return True, base

    async def _tickers(self) -> list[dict[str, Any]]:
        payload = await self.get_json("/ticker/24hr")
        if not isinstance(payload, list):
            raise ProviderError(self.key, "unexpected /ticker/24hr shape")
        return [row for row in payload if isinstance(row, dict)]

    async def list_universe(self, asset_class: AssetClass) -> list[UniverseEntry]:
        if asset_class is not AssetClass.CRYPTO:
            return []

        best: dict[str, UniverseEntry] = {}
        for row in await self._tickers():
            venue_symbol = str(row.get("symbol", "")).upper()
            tradable, base = self._tradable(venue_symbol)
            if not tradable:
                continue
            volume = _decimal(row.get("quoteVolume"))
            if volume is None or volume < MIN_24H_QUOTE_VOLUME:
                continue

            canonical = f"{base}-USD"
            existing = best.get(canonical)
            if existing is not None and (existing.dollar_volume or Decimal(0)) >= volume:
                continue
            best[canonical] = UniverseEntry(
                symbol=canonical,
                asset_class=AssetClass.CRYPTO,
                name=base,
                exchange="BINANCE",
                sector="crypto",
                currency="USD",
                dollar_volume=volume,
            )

        entries = list(best.values())
        entries.sort(key=lambda e: e.dollar_volume or Decimal(0), reverse=True)
        return entries

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        wanted = {self.to_venue(symbol): symbol.upper() for symbol in symbols}
        if not wanted:
            return {}

        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}
        for row in await self._tickers():
            venue_symbol = str(row.get("symbol", "")).upper()
            canonical = wanted.get(venue_symbol)
            if canonical is None:
                continue
            price = _decimal(row.get("lastPrice"))
            if price is None or price <= 0:
                continue
            quotes[canonical] = Quote(
                symbol=canonical,
                price=price,
                at=_timestamp(row.get("closeTime")) or now,
                previous_close=_positive(_decimal(row.get("prevClosePrice"))),
                bid=_positive(_decimal(row.get("bidPrice"))),
                ask=_positive(_decimal(row.get("askPrice"))),
                volume=_decimal(row.get("volume")),
            )
        return quotes

    async def get_bars(self, symbol: str, *, days: int = 260, end: date | None = None) -> list[Bar]:
        canonical = symbol.upper()
        rows = await self.get_json(
            "/klines",
            params={
                "symbol": self.to_venue(canonical),
                "interval": "1d",
                "limit": min(max(days, 30), MAX_KLINES),
            },
        )
        if not isinstance(rows, list):
            raise ProviderError(self.key, f"unexpected klines shape for {symbol}")

        bars: list[Bar] = []
        for row in rows:
            bar = _parse_kline(canonical, row)
            if bar is not None and (end is None or bar.bar_date <= end):
                bars.append(bar)
        bars.sort(key=lambda b: b.bar_date)
        return bars[-days:]

    async def stream_quotes(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        wanted = {self.to_venue(s): s.upper() for s in symbols}
        if not wanted:
            return

        streams = "/".join(f"{venue.lower()}@ticker" for venue in wanted)
        async with websockets.connect(f"{self.stream_url}?streams={streams}") as socket:
            async for raw in socket:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                quote = _parse_stream_ticker(payload, wanted)
                if quote is not None:
                    yield quote


def _parse_stream_ticker(payload: Any, wanted: dict[str, str]) -> Quote | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None

    canonical = wanted.get(str(data.get("s", "")).upper())
    price = _decimal(data.get("c"))
    if canonical is None or price is None or price <= 0:
        return None

    return Quote(
        symbol=canonical,
        price=price,
        at=_timestamp(data.get("E")) or datetime.now(UTC),
        previous_close=_positive(_decimal(data.get("o"))),
        bid=_positive(_decimal(data.get("b"))),
        ask=_positive(_decimal(data.get("a"))),
        volume=_decimal(data.get("v")),
    )


def _parse_kline(symbol: str, row: Any) -> Bar | None:
    if not isinstance(row, list) or len(row) < 6:
        return None
    at = _timestamp(row[0])
    values = [_decimal(row[index]) for index in (1, 2, 3, 4, 5)]
    if at is None or any(value is None for value in values):
        return None
    open_, high, low, close, volume = values
    return Bar(
        symbol=symbol,
        bar_date=at.date(),
        open=open_,  # type: ignore[arg-type]
        high=high,  # type: ignore[arg-type]
        low=low,  # type: ignore[arg-type]
        close=close,  # type: ignore[arg-type]
        volume=volume,  # type: ignore[arg-type]
    )


def _is_dollar_pegged(base: str) -> bool:
    """New dollar stablecoins appear constantly, so match the shape rather than a fixed list."""
    return base in STABLECOIN_BASES or base.startswith("USD") or base.endswith("USD")


def _positive(value: Decimal | None) -> Decimal | None:
    return value if value is not None and value > 0 else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None
