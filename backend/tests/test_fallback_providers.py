from decimal import Decimal

import httpx
import pytest
import respx

from tradebot.providers.base import AssetClass, Capability, ProviderConfig, ProviderError
from tradebot.providers.impl.alpaca import AlpacaProvider
from tradebot.providers.impl.finnhub import FinnhubProvider
from tradebot.providers.impl.fmp import FmpProvider
from tradebot.providers.impl.polygon import PolygonProvider

FINNHUB = "https://finnhub.io/api/v1"
POLYGON = "https://api.polygon.io"
CREDS = ProviderConfig(credentials={"api_key": "TEST-KEY"})


@pytest.fixture
async def finnhub():  # type: ignore[no-untyped-def]
    instance = FinnhubProvider(CREDS)
    yield instance
    await instance.aclose()


@pytest.fixture
async def polygon():  # type: ignore[no-untyped-def]
    instance = PolygonProvider(CREDS)
    yield instance
    await instance.aclose()


def test_fallbacks_rank_below_the_primary_equity_providers() -> None:
    assert AlpacaProvider.default_priority < FmpProvider.default_priority
    assert FmpProvider.default_priority < FinnhubProvider.default_priority
    assert FinnhubProvider.default_priority < PolygonProvider.default_priority


def test_finnhub_offers_quotes_and_news_but_not_bars() -> None:
    assert Capability.QUOTES in FinnhubProvider.capabilities
    assert Capability.NEWS in FinnhubProvider.capabilities
    assert Capability.BARS not in FinnhubProvider.capabilities


def test_polygons_free_tier_limit_is_declared() -> None:
    assert PolygonProvider.rate_limits.requests_per_minute == 5
    assert PolygonProvider.rate_limits.max_concurrency == 1


@respx.mock
async def test_finnhub_quote_uses_current_and_previous_close(finnhub) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{FINNHUB}/quote").mock(
        return_value=httpx.Response(
            200, json={"c": 305.93, "pc": 306.0, "t": 1786737600, "h": 307, "l": 304}
        )
    )

    quote = (await finnhub.get_quotes(["AAPL"]))["AAPL"]

    assert quote.price == Decimal("305.93")
    assert quote.previous_close == Decimal("306.0")


@respx.mock
async def test_finnhub_sends_its_token_as_a_header(finnhub) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{FINNHUB}/quote").mock(
        return_value=httpx.Response(200, json={"c": 1, "pc": 1, "t": 1786737600})
    )

    await finnhub.get_quotes(["AAPL"])

    assert route.calls[0].request.headers["X-Finnhub-Token"] == "TEST-KEY"


@respx.mock
async def test_finnhub_drops_an_unpriced_symbol(finnhub) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{FINNHUB}/quote").mock(return_value=httpx.Response(200, json={"c": 0, "pc": 0}))

    assert await finnhub.get_quotes(["DELISTED"]) == {}


@respx.mock
async def test_finnhub_news_is_newest_first(finnhub) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{FINNHUB}/company-news").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"headline": "older", "datetime": 1786651200, "source": "Reuters", "url": "u1"},
                {"headline": "newer", "datetime": 1786737600, "source": "Reuters", "url": "u2"},
            ],
        )
    )

    items = await finnhub.get_news(["AAPL"])

    assert [i.headline for i in items] == ["newer", "older"]
    assert items[0].symbol == "AAPL"


@respx.mock
async def test_finnhub_news_skips_headlineless_rows(finnhub) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{FINNHUB}/company-news").mock(
        return_value=httpx.Response(
            200,
            json=[{"headline": "", "datetime": 1786737600}, {"headline": "ok", "datetime": None}],
        )
    )

    assert await finnhub.get_news(["AAPL"]) == []


@respx.mock
async def test_polygon_quote_comes_from_the_previous_close(polygon) -> None:  # type: ignore[no-untyped-def]
    respx.get(url__regex=rf"{POLYGON}/v2/aggs/ticker/AAPL/prev.*").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"c": 305.93, "o": 304.21, "v": 28229375, "t": 1786737600000}]},
        )
    )

    quote = (await polygon.get_quotes(["AAPL"]))["AAPL"]

    assert quote.price == Decimal("305.93")
    assert quote.volume == Decimal("28229375")


@respx.mock
async def test_polygon_sends_its_key_as_a_query_param(polygon) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(url__regex=rf"{POLYGON}/v2/aggs/ticker/AAPL/prev.*").mock(
        return_value=httpx.Response(200, json={"results": [{"c": 1, "o": 1, "t": 1786737600000}]})
    )

    await polygon.get_quotes(["AAPL"])

    assert route.calls[0].request.url.params["apiKey"] == "TEST-KEY"


@respx.mock
async def test_polygon_bars_are_parsed_and_sorted(polygon) -> None:  # type: ignore[no-untyped-def]
    respx.get(url__regex=rf"{POLYGON}/v2/aggs/ticker/AAPL/range.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"t": 1786651200000, "o": 304, "h": 306, "l": 302, "c": 305, "v": 40349300},
                    {"t": 1786564800000, "o": 305, "h": 306, "l": 300, "c": 302, "v": 41657800},
                ]
            },
        )
    )

    bars = await polygon.get_bars("AAPL", days=30)

    assert [b.bar_date.isoformat() for b in bars] == ["2026-08-12", "2026-08-13"]
    assert bars[-1].close == Decimal("305")


@respx.mock
async def test_polygon_returns_nothing_for_an_empty_result_set(polygon) -> None:  # type: ignore[no-untyped-def]
    respx.get(url__regex=rf"{POLYGON}/v2/aggs/ticker/NOPE/range.*").mock(
        return_value=httpx.Response(200, json={"status": "OK", "resultsCount": 0})
    )

    assert await polygon.get_bars("NOPE", days=30) == []


@respx.mock
async def test_polygon_unexpected_shape_raises(polygon) -> None:  # type: ignore[no-untyped-def]
    respx.get(url__regex=rf"{POLYGON}/v2/aggs/ticker/AAPL/range.*").mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )

    with pytest.raises(ProviderError, match="unexpected"):
        await polygon.get_bars("AAPL", days=30)


def test_every_equity_provider_declares_the_same_asset_classes() -> None:
    """A capability lookup for stocks must find all four, or failover would silently skip one."""
    for provider in (AlpacaProvider, FmpProvider, FinnhubProvider, PolygonProvider):
        assert AssetClass.STOCK in provider.asset_classes
        assert AssetClass.ETF in provider.asset_classes
