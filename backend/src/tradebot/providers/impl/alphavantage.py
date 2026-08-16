from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from tradebot.providers.base import (
    AssetClass,
    Capability,
    CredentialField,
    NewsItem,
    ProviderError,
    ProviderRateLimitedError,
    Quote,
    RateLimits,
    SupportsNews,
    SupportsQuotes,
)
from tradebot.providers.http import HttpProviderMixin
from tradebot.providers.registry import register

DAILY_REQUEST_LIMIT = 25
MAX_NEWS_TICKERS = 5


@register
class AlphaVantageProvider(HttpProviderMixin, SupportsQuotes, SupportsNews):
    """Alpha Vantage. Ranked last: the free tier allows only ~25 requests a day, so it earns its
    place as a second news source rather than as a price feed."""

    key = "alphavantage"
    label = "Alpha Vantage"
    base_url = "https://www.alphavantage.co"
    capabilities = frozenset({Capability.QUOTES, Capability.NEWS})
    asset_classes = frozenset({AssetClass.STOCK, AssetClass.ETF})
    credential_fields = (CredentialField(name="api_key", label="API key"),)
    rate_limits = RateLimits(
        requests_per_minute=5,
        requests_per_day=DAILY_REQUEST_LIMIT,
        max_concurrency=1,
    )
    default_priority = 60

    async def _get(self, function: str, **params: Any) -> Any:
        params["function"] = function
        params["apikey"] = self.config.credentials.get("api_key", "")
        payload = await self.get_json("/query", params=params)
        _raise_for_advisory(self.key, payload)
        return payload

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        for symbol in {s.upper() for s in symbols}:
            payload = await self._get("GLOBAL_QUOTE", symbol=symbol)
            quote = _parse_quote(symbol, payload)
            if quote is not None:
                quotes[symbol] = quote
        return quotes

    async def get_news(self, symbols: Iterable[str], *, limit: int = 20) -> list[NewsItem]:
        wanted = sorted({s.upper() for s in symbols})[:MAX_NEWS_TICKERS]
        if not wanted:
            return []

        payload = await self._get("NEWS_SENTIMENT", tickers=",".join(wanted), limit=limit)
        feed = payload.get("feed") if isinstance(payload, dict) else None
        if not isinstance(feed, list):
            raise ProviderError(self.key, "unexpected news payload")

        items: list[NewsItem] = []
        for row in feed[:limit]:
            item = _parse_news(row, wanted)
            if item is not None:
                items.append(item)
        items.sort(key=lambda i: i.published_at, reverse=True)
        return items


def _raise_for_advisory(key: str, payload: Any) -> None:
    """Alpha Vantage answers 200 with a prose note when throttled instead of a 429."""
    if not isinstance(payload, dict):
        return
    if "Note" in payload or "Information" in payload:
        raise ProviderRateLimitedError(key, retry_after=60.0)
    if "Error Message" in payload:
        raise ProviderError(key, str(payload["Error Message"])[:200])


def _parse_quote(symbol: str, payload: Any) -> Quote | None:
    if not isinstance(payload, dict):
        return None
    row = payload.get("Global Quote")
    if not isinstance(row, dict):
        return None

    price = _decimal(row.get("05. price"))
    if price is None or price <= 0:
        return None
    return Quote(
        symbol=symbol,
        price=price,
        at=datetime.now(UTC),
        previous_close=_positive(_decimal(row.get("08. previous close"))),
        volume=_decimal(row.get("06. volume")),
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_news(row: Any, wanted: list[str]) -> NewsItem | None:
    if not isinstance(row, dict):
        return None
    headline = str(row.get("title", "")).strip()
    published = _parse_timestamp(row.get("time_published"))
    if not headline or published is None:
        return None

    # Highest relevance among the requested tickers, not the first match: this feed routinely
    # tags several mega-caps in one index-fund article, and the first-listed one is arbitrary.
    scored = [
        (str(entry.get("ticker", "")).upper(), _float(entry.get("relevance_score")))
        for entry in row.get("ticker_sentiment", [])
        if isinstance(entry, dict)
    ]
    matches = [(ticker, score) for ticker, score in scored if ticker in wanted]
    symbol = max(matches, key=lambda pair: pair[1])[0] if matches else None
    if symbol is None:
        # Dropped rather than attributed to the first requested ticker: a headline about an
        # unrelated company, labelled as this instrument's news, manufactures sentiment the
        # model would then reason from.
        return None

    return NewsItem(
        symbol=symbol,
        headline=headline,
        published_at=published,
        source=str(row.get("source", "")),
        url=str(row.get("url", "")),
        summary=str(row.get("summary", ""))[:1000],
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _positive(value: Decimal | None) -> Decimal | None:
    return value if value is not None and value > 0 else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
