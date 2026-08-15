from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from tradebot.providers.base import (
    AssetClass,
    Capability,
    CredentialField,
    NewsItem,
    Quote,
    RateLimits,
    SupportsNews,
    SupportsQuotes,
)
from tradebot.providers.http import HttpProviderMixin
from tradebot.providers.registry import register

NEWS_LOOKBACK_DAYS = 7


@register
class FinnhubProvider(HttpProviderMixin, SupportsQuotes, SupportsNews):
    """Finnhub. Quotes are per-symbol rather than batched, so it serves as a fallback for
    quotes and a primary source for company news; daily candles are paid-tier only."""

    key = "finnhub"
    label = "Finnhub"
    base_url = "https://finnhub.io/api/v1"
    capabilities = frozenset({Capability.QUOTES, Capability.NEWS})
    asset_classes = frozenset({AssetClass.STOCK, AssetClass.ETF})
    credential_fields = (CredentialField(name="api_key", label="API key"),)
    rate_limits = RateLimits(requests_per_minute=60, max_concurrency=4)
    default_priority = 40

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Finnhub-Token": self.config.credentials.get("api_key", "")}

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        for symbol in {s.upper() for s in symbols}:
            payload = await self.get_json(
                "/quote", params={"symbol": symbol}, headers=self._headers
            )
            quote = _parse_quote(symbol, payload)
            if quote is not None:
                quotes[symbol] = quote
        return quotes

    async def get_news(self, symbols: Iterable[str], *, limit: int = 20) -> list[NewsItem]:
        today = datetime.now(UTC).date()
        start = today - timedelta(days=NEWS_LOOKBACK_DAYS)

        items: list[NewsItem] = []
        for symbol in {s.upper() for s in symbols}:
            rows = await self.get_json(
                "/company-news",
                params={
                    "symbol": symbol,
                    "from": start.isoformat(),
                    "to": today.isoformat(),
                },
                headers=self._headers,
            )
            if not isinstance(rows, list):
                continue
            for row in rows[:limit]:
                item = _parse_news(symbol, row)
                if item is not None:
                    items.append(item)
        items.sort(key=lambda i: i.published_at, reverse=True)
        return items


def _parse_quote(symbol: str, payload: Any) -> Quote | None:
    if not isinstance(payload, dict):
        return None
    price = _decimal(payload.get("c"))
    if price is None or price <= 0:
        return None
    return Quote(
        symbol=symbol,
        price=price,
        at=_timestamp(payload.get("t")) or datetime.now(UTC),
        previous_close=_positive(_decimal(payload.get("pc"))),
    )


def _parse_news(symbol: str, row: Any) -> NewsItem | None:
    if not isinstance(row, dict):
        return None
    headline = str(row.get("headline", "")).strip()
    published = _timestamp(row.get("datetime"))
    if not headline or published is None:
        return None
    return NewsItem(
        symbol=symbol,
        headline=headline,
        published_at=published,
        source=str(row.get("source", "")),
        url=str(row.get("url", "")),
        summary=str(row.get("summary", ""))[:1000],
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
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None
