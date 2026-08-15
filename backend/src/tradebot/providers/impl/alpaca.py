import json
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import websockets

from tradebot.providers.base import (
    AssetClass,
    Bar,
    Capability,
    CredentialField,
    ProviderError,
    Quote,
    RateLimits,
    SupportsBars,
    SupportsQuotes,
    SupportsStream,
)
from tradebot.providers.http import HttpProviderMixin
from tradebot.providers.registry import register

SNAPSHOT_BATCH = 100
CALENDAR_PAD = 1.6


@register
class AlpacaProvider(HttpProviderMixin, SupportsQuotes, SupportsBars, SupportsStream):
    """Alpaca market data. Free accounts get real-time IEX quotes and split-adjusted daily bars."""

    key = "alpaca"
    label = "Alpaca"
    base_url = "https://data.alpaca.markets/v2"
    stream_url = "wss://stream.data.alpaca.markets/v2/iex"
    capabilities = frozenset({Capability.QUOTES, Capability.BARS, Capability.STREAM})
    asset_classes = frozenset({AssetClass.STOCK, AssetClass.ETF})
    credential_fields = (
        CredentialField(name="api_key_id", label="API key ID"),
        CredentialField(name="secret_key", label="API secret key"),
    )
    rate_limits = RateLimits(requests_per_minute=200, max_concurrency=4)
    default_priority = 10

    feed = "iex"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.config.credentials.get("api_key_id", ""),
            "APCA-API-SECRET-KEY": self.config.credentials.get("secret_key", ""),
        }

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        wanted = [s.upper() for s in symbols]
        if not wanted:
            return {}

        quotes: dict[str, Quote] = {}
        for start in range(0, len(wanted), SNAPSHOT_BATCH):
            batch = wanted[start : start + SNAPSHOT_BATCH]
            payload = await self.get_json(
                "/stocks/snapshots",
                params={"symbols": ",".join(batch), "feed": self.feed},
                headers=self._headers,
            )
            if not isinstance(payload, dict):
                raise ProviderError(self.key, "unexpected snapshots shape")
            for symbol, snapshot in payload.items():
                quote = _parse_snapshot(str(symbol).upper(), snapshot)
                if quote is not None:
                    quotes[quote.symbol] = quote
        return quotes

    async def get_bars(self, symbol: str, *, days: int = 260, end: date | None = None) -> list[Bar]:
        last = end or datetime.now(UTC).date()
        first = last - timedelta(days=int(days * CALENDAR_PAD) + 10)

        params: dict[str, Any] = {
            "symbols": symbol.upper(),
            "timeframe": "1Day",
            "start": first.isoformat(),
            "end": last.isoformat(),
            "limit": 10_000,
            "adjustment": "all",
            "feed": self.feed,
        }

        collected: list[Bar] = []
        page: str | None = None
        while True:
            if page:
                params["page_token"] = page
            payload = await self.get_json("/stocks/bars", params=params, headers=self._headers)
            if not isinstance(payload, dict):
                raise ProviderError(self.key, f"unexpected bars shape for {symbol}")

            rows = (payload.get("bars") or {}).get(symbol.upper()) or []
            for row in rows:
                bar = _parse_bar(symbol.upper(), row)
                if bar is not None:
                    collected.append(bar)

            page = payload.get("next_page_token")
            if not page:
                break

        collected.sort(key=lambda b: b.bar_date)
        return collected[-days:]

    async def stream_quotes(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        wanted = [s.upper() for s in symbols]
        if not wanted:
            return

        async with websockets.connect(self.stream_url) as socket:
            await socket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.config.credentials.get("api_key_id", ""),
                        "secret": self.config.credentials.get("secret_key", ""),
                    }
                )
            )
            await _await_authentication(self.key, socket)
            await socket.send(json.dumps({"action": "subscribe", "quotes": wanted}))

            async for raw in socket:
                try:
                    messages = json.loads(raw)
                except ValueError:
                    continue
                for message in messages if isinstance(messages, list) else [messages]:
                    quote = _parse_stream_quote(message)
                    if quote is not None:
                        yield quote


async def _await_authentication(key: str, socket: Any) -> None:
    """Alpaca rejects a subscription sent before it has acknowledged the credentials."""
    for _ in range(5):
        raw = await socket.recv()
        try:
            messages = json.loads(raw)
        except ValueError:
            continue
        for message in messages if isinstance(messages, list) else [messages]:
            if not isinstance(message, dict):
                continue
            if message.get("T") == "error":
                raise ProviderError(key, f"stream rejected: {message.get('msg')}")
            if message.get("T") == "success" and message.get("msg") == "authenticated":
                return
    raise ProviderError(key, "stream did not authenticate")


def _parse_stream_quote(message: Any) -> Quote | None:
    if not isinstance(message, dict) or message.get("T") != "q":
        return None

    symbol = str(message.get("S", "")).upper()
    bid = _decimal(message.get("bp"))
    ask = _decimal(message.get("ap"))
    if not symbol or bid is None or ask is None or bid <= 0 or ask <= 0:
        return None

    # A quote message carries no last trade, so the midpoint stands in for price.
    return Quote(
        symbol=symbol,
        price=(bid + ask) / 2,
        at=_timestamp(message.get("t")) or datetime.now(UTC),
        bid=bid,
        ask=ask,
    )


def _parse_snapshot(symbol: str, snapshot: Any) -> Quote | None:
    if not isinstance(snapshot, dict):
        return None

    trade = snapshot.get("latestTrade") or {}
    quote = snapshot.get("latestQuote") or {}
    daily = snapshot.get("dailyBar") or {}
    previous = snapshot.get("prevDailyBar") or {}

    price = _decimal(trade.get("p")) or _decimal(daily.get("c"))
    if price is None or price <= 0:
        return None

    return Quote(
        symbol=symbol,
        price=price,
        at=_timestamp(trade.get("t")) or datetime.now(UTC),
        previous_close=_positive(_decimal(previous.get("c"))),
        bid=_positive(_decimal(quote.get("bp"))),
        ask=_positive(_decimal(quote.get("ap"))),
        volume=_decimal(daily.get("v")),
    )


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
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None
