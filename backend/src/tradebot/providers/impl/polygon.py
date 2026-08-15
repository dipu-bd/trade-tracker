from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

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
)
from tradebot.providers.http import HttpProviderMixin
from tradebot.providers.registry import register

CALENDAR_PAD = 1.6


@register
class PolygonProvider(HttpProviderMixin, SupportsQuotes, SupportsBars):
    """Polygon. The free tier is 5 requests a minute and end-of-day only, so it ranks last and
    exists as a fallback when the primary equity providers are down or throttled."""

    key = "polygon"
    label = "Polygon"
    base_url = "https://api.polygon.io"
    capabilities = frozenset({Capability.QUOTES, Capability.BARS})
    asset_classes = frozenset({AssetClass.STOCK, AssetClass.ETF})
    credential_fields = (CredentialField(name="api_key", label="API key"),)
    rate_limits = RateLimits(requests_per_minute=5, max_concurrency=1)
    default_priority = 50

    async def _get(self, path: str, **params: Any) -> Any:
        params["apiKey"] = self.config.credentials.get("api_key", "")
        return await self.get_json(path, params=params)

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        for symbol in {s.upper() for s in symbols}:
            payload = await self._get(f"/v2/aggs/ticker/{symbol}/prev", adjusted="true")
            quote = _parse_prev_close(symbol, payload)
            if quote is not None:
                quotes[symbol] = quote
        return quotes

    async def get_bars(self, symbol: str, *, days: int = 260, end: date | None = None) -> list[Bar]:
        last = end or datetime.now(UTC).date()
        first = last - timedelta(days=int(days * CALENDAR_PAD) + 10)

        payload = await self._get(
            f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{first.isoformat()}/{last.isoformat()}",
            adjusted="true",
            sort="asc",
            limit=50_000,
        )
        if not isinstance(payload, dict):
            raise ProviderError(self.key, f"unexpected aggregates shape for {symbol}")

        rows = payload.get("results")
        if not isinstance(rows, list):
            return []

        bars: list[Bar] = []
        for row in rows:
            bar = _parse_bar(symbol.upper(), row)
            if bar is not None and bar.bar_date <= last:
                bars.append(bar)
        bars.sort(key=lambda b: b.bar_date)
        return bars[-days:]


def _parse_prev_close(symbol: str, payload: Any) -> Quote | None:
    """The free tier has no live quote, so the previous session's close stands in for price."""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        return None

    row = rows[0]
    if not isinstance(row, dict):
        return None
    close = _decimal(row.get("c"))
    if close is None or close <= 0:
        return None

    return Quote(
        symbol=symbol,
        price=close,
        at=_timestamp(row.get("t")) or datetime.now(UTC),
        previous_close=_positive(_decimal(row.get("o"))),
        volume=_decimal(row.get("v")),
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
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None
