import asyncio
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

STABLECOINS = frozenset(
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
    }
)
LEVERAGED_MARKERS = ("3L", "3S", "5L", "5S", "2L", "2S")
MIN_24H_QUOTE_VOLUME = Decimal("1000000")
MAX_CANDLES = 300


@register
class CryptoComProvider(
    HttpProviderMixin, SupportsUniverse, SupportsQuotes, SupportsBars, SupportsStream
):
    """Crypto.com public market data. No key required, which makes it the default crypto venue."""

    key = "cryptocom"
    label = "Crypto.com"
    base_url = "https://api.crypto.com/exchange/v1/public"
    stream_url = "wss://stream.crypto.com/exchange/v1/market"
    capabilities = frozenset(
        {Capability.UNIVERSE, Capability.QUOTES, Capability.BARS, Capability.STREAM}
    )
    asset_classes = frozenset({AssetClass.CRYPTO})
    rate_limits = RateLimits(requests_per_minute=100, max_concurrency=4)
    default_priority = 10

    quote_currency = "USD"

    def to_canonical(self, venue_symbol: str) -> str:
        base, _, quote = venue_symbol.upper().partition("_")
        return f"{base}-{quote}" if quote else base

    def to_venue(self, canonical_symbol: str) -> str:
        base, _, quote = canonical_symbol.upper().partition("-")
        return f"{base}_{quote or self.quote_currency}"

    async def _tickers(self) -> list[dict[str, Any]]:
        payload = await self.get_json("/get-tickers")
        data = payload.get("result", {}).get("data")
        if not isinstance(data, list):
            raise ProviderError(self.key, "unexpected /get-tickers shape")
        return [row for row in data if isinstance(row, dict)]

    def _tradable(self, instrument: str) -> bool:
        if "-" in instrument:  # perpetuals and dated futures
            return False
        base, _, quote = instrument.partition("_")
        if quote != self.quote_currency or not base:
            return False
        if base in STABLECOINS:
            return False
        return not any(marker in base for marker in LEVERAGED_MARKERS)

    async def list_universe(self, asset_class: AssetClass) -> list[UniverseEntry]:
        if asset_class is not AssetClass.CRYPTO:
            return []

        entries: list[UniverseEntry] = []
        for row in await self._tickers():
            instrument = str(row.get("i", ""))
            if not self._tradable(instrument):
                continue
            volume = _decimal(row.get("vv"))
            if volume is None or volume < MIN_24H_QUOTE_VOLUME:
                continue
            entries.append(
                UniverseEntry(
                    symbol=self.to_canonical(instrument),
                    asset_class=AssetClass.CRYPTO,
                    name=instrument.partition("_")[0],
                    exchange="CRYPTOCOM",
                    sector="crypto",
                    currency=self.quote_currency,
                    dollar_volume=volume,
                )
            )
        entries.sort(key=lambda e: e.dollar_volume or Decimal(0), reverse=True)
        return entries

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        wanted = {self.to_venue(s): s.upper() for s in symbols}
        if not wanted:
            return {}

        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}
        for row in await self._tickers():
            instrument = str(row.get("i", "")).upper()
            canonical = wanted.get(instrument)
            if canonical is None:
                continue
            price = _decimal(row.get("a"))
            if price is None or price <= 0:
                continue
            quotes[canonical] = Quote(
                symbol=canonical,
                price=price,
                at=_timestamp(row.get("t")) or now,
                previous_close=_previous_close(price, _decimal(row.get("c"))),
                bid=_decimal(row.get("b")),
                ask=_decimal(row.get("k")),
                volume=_decimal(row.get("v")),
            )
        return quotes

    async def get_bars(self, symbol: str, *, days: int = 260, end: date | None = None) -> list[Bar]:
        canonical = symbol.upper()
        count = min(max(days, 30), MAX_CANDLES)
        payload = await self.get_json(
            "/get-candlestick",
            params={"instrument_name": self.to_venue(canonical), "timeframe": "1D", "count": count},
        )
        rows = payload.get("result", {}).get("data")
        if not isinstance(rows, list):
            raise ProviderError(self.key, f"unexpected candlestick shape for {symbol}")

        bars: list[Bar] = []
        for row in rows:
            bar = _parse_bar(canonical, row)
            if bar is not None and (end is None or bar.bar_date <= end):
                bars.append(bar)
        bars.sort(key=lambda b: b.bar_date)
        return bars[-days:]

    async def stream_quotes(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        wanted = {self.to_venue(s): s.upper() for s in symbols}
        if not wanted:
            return

        async with websockets.connect(self.stream_url) as socket:
            # The venue drops a connection that subscribes too soon after the handshake.
            await asyncio.sleep(1)
            await socket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "subscribe",
                        "params": {"channels": [f"ticker.{v}" for v in wanted]},
                    }
                )
            )

            async for raw in socket:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue

                if payload.get("method") == "public/heartbeat":
                    await socket.send(
                        json.dumps({"id": payload.get("id"), "method": "public/respond-heartbeat"})
                    )
                    continue

                for quote in _parse_stream_result(payload, wanted):
                    yield quote


def _parse_bar(symbol: str, row: Any) -> Bar | None:
    if not isinstance(row, dict):
        return None
    at = _timestamp(row.get("t"))
    values = [_decimal(row.get(field)) for field in ("o", "h", "l", "c", "v")]
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


def _parse_stream_result(payload: Any, wanted: dict[str, str]) -> list[Quote]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or not str(result.get("channel", "")).startswith("ticker"):
        return []

    rows = result.get("data")
    if not isinstance(rows, list):
        return []

    instrument = str(result.get("instrument_name", "")).upper()
    quotes: list[Quote] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        canonical = wanted.get(str(row.get("i", instrument)).upper())
        price = _decimal(row.get("a"))
        if canonical is None or price is None or price <= 0:
            continue
        quotes.append(
            Quote(
                symbol=canonical,
                price=price,
                at=_timestamp(row.get("t")) or datetime.now(UTC),
                previous_close=_previous_close(price, _decimal(row.get("c"))),
                bid=_decimal(row.get("b")),
                ask=_decimal(row.get("k")),
                volume=_decimal(row.get("v")),
            )
        )
    return quotes


def _previous_close(price: Decimal, change_ratio: Decimal | None) -> Decimal | None:
    """The venue reports a 24h change ratio rather than a prior close."""
    if change_ratio is None or change_ratio <= -1:
        return None
    return price / (Decimal(1) + change_ratio)


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
