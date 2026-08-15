from decimal import Decimal

import httpx
import pytest
import respx

from tradebot.providers.base import (
    Capability,
    ProviderConfig,
    ProviderError,
    ProviderRateLimitedError,
)
from tradebot.providers.impl.alphavantage import DAILY_REQUEST_LIMIT, AlphaVantageProvider
from tradebot.providers.impl.finnhub import FinnhubProvider
from tradebot.providers.impl.polygon import PolygonProvider

BASE = "https://www.alphavantage.co"
CREDS = ProviderConfig(credentials={"api_key": "AV-TEST"})

NEWS = {
    "feed": [
        {
            "title": "Chipmaker raises guidance",
            "time_published": "20260814T133000",
            "source": "Reuters",
            "url": "https://example.com/a",
            "summary": "Guidance lifted.",
            "ticker_sentiment": [{"ticker": "NVDA"}],
        },
        {
            "title": "Older item",
            "time_published": "20260812T090000",
            "source": "Barrons",
            "url": "https://example.com/b",
            "ticker_sentiment": [{"ticker": "NVDA"}],
        },
    ]
}


@pytest.fixture
async def provider():  # type: ignore[no-untyped-def]
    instance = AlphaVantageProvider(CREDS)
    yield instance
    await instance.aclose()


def test_it_ranks_last_of_every_equity_provider() -> None:
    assert AlphaVantageProvider.default_priority > PolygonProvider.default_priority
    assert AlphaVantageProvider.default_priority > FinnhubProvider.default_priority


def test_the_free_daily_cap_is_declared_to_the_governor() -> None:
    """25 a day is the binding constraint; the governor must queue rather than burn through it."""
    assert AlphaVantageProvider.rate_limits.requests_per_day == DAILY_REQUEST_LIMIT
    assert AlphaVantageProvider.rate_limits.max_concurrency == 1


def test_it_offers_news_which_is_the_reason_it_exists() -> None:
    assert Capability.NEWS in AlphaVantageProvider.capabilities
    assert Capability.BARS not in AlphaVantageProvider.capabilities


@respx.mock
async def test_news_is_parsed_and_ordered_newest_first(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/query").mock(return_value=httpx.Response(200, json=NEWS))

    items = await provider.get_news(["NVDA"])

    assert [i.headline for i in items] == ["Chipmaker raises guidance", "Older item"]
    assert items[0].symbol == "NVDA"
    assert items[0].published_at.year == 2026


@respx.mock
async def test_news_requests_are_capped_to_a_few_tickers(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/query").mock(return_value=httpx.Response(200, json=NEWS))

    await provider.get_news([f"SYM{i}" for i in range(20)])

    assert len(route.calls[0].request.url.params["tickers"].split(",")) <= 5


@respx.mock
async def test_a_throttle_note_becomes_a_rate_limit_error(provider) -> None:  # type: ignore[no-untyped-def]
    """It answers 200 with prose when throttled, so a naive parser would read it as success."""
    respx.get(f"{BASE}/query").mock(
        return_value=httpx.Response(200, json={"Note": "call frequency exceeded"})
    )

    with pytest.raises(ProviderRateLimitedError):
        await provider.get_news(["NVDA"])


@respx.mock
async def test_the_rate_limit_information_variant_is_also_caught(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/query").mock(
        return_value=httpx.Response(200, json={"Information": "daily limit reached"})
    )

    with pytest.raises(ProviderRateLimitedError):
        await provider.get_quotes(["NVDA"])


@respx.mock
async def test_an_error_message_becomes_a_provider_error(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/query").mock(
        return_value=httpx.Response(200, json={"Error Message": "invalid symbol"})
    )

    with pytest.raises(ProviderError, match="invalid symbol"):
        await provider.get_quotes(["NOPE"])


@respx.mock
async def test_quotes_are_parsed_from_the_numbered_field_names(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "Global Quote": {
                    "01. symbol": "NVDA",
                    "05. price": "180.25",
                    "06. volume": "41000000",
                    "08. previous close": "178.10",
                }
            },
        )
    )

    quote = (await provider.get_quotes(["NVDA"]))["NVDA"]

    assert quote.price == Decimal("180.25")
    assert quote.previous_close == Decimal("178.10")


@respx.mock
async def test_an_empty_quote_is_dropped(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/query").mock(return_value=httpx.Response(200, json={"Global Quote": {}}))

    assert await provider.get_quotes(["NOPE"]) == {}


def test_it_needs_an_api_key() -> None:
    assert AlphaVantageProvider().missing_credentials == ("api_key",)
    assert AlphaVantageProvider(CREDS).available
