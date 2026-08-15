from decimal import Decimal

import httpx
import pytest
import respx

from tradebot.providers.base import (
    AssetClass,
    Capability,
    ProviderConfig,
    SplitKind,
)
from tradebot.providers.impl.fmp import ACTIVITY_PATHS, CORE_STOCKS, FmpProvider

BASE = "https://financialmodelingprep.com"
CREDS = ProviderConfig(credentials={"api_key": "FMP-TEST"})

QUOTES = [
    {
        "symbol": "AAPL",
        "price": 305.93,
        "change": -0.07,
        "previousClose": 306.0,
        "volume": 28229375,
        "timestamp": 1786737600,
    },
    {
        "symbol": "SPY",
        "price": 776.34,
        "change": -1.54,
        "volume": 29940248,
        "timestamp": 1786737600,
    },
]

# Shape taken from a live response: newest-first, which the parser has to reverse.
EOD = [
    {
        "symbol": "AAPL",
        "date": "2026-08-14",
        "open": 306,
        "high": 307.49,
        "low": 304.3,
        "close": 305.93,
        "volume": 28229375,
    },
    {
        "symbol": "AAPL",
        "date": "2026-08-13",
        "open": 304.21,
        "high": 306,
        "low": 302.05,
        "close": 305.26,
        "volume": 40349300,
    },
    {
        "symbol": "AAPL",
        "date": "2026-08-12",
        "open": 305.1,
        "high": 305.66,
        "low": 300.57,
        "close": 302.25,
        "volume": 41657800,
    },
]

SCREENER = [
    {
        "symbol": "AAPL",
        "companyName": "Apple Inc.",
        "price": 305.93,
        "volume": 28_229_375,
        "exchangeShortName": "NASDAQ",
        "sector": "Technology",
    },
    {
        "symbol": "THIN",
        "companyName": "Thin Corp",
        "price": 4.0,
        "volume": 1000,
        "exchangeShortName": "NYSE",
        "sector": "Industrials",
    },
]


@pytest.fixture
async def provider():  # type: ignore[no-untyped-def]
    instance = FmpProvider(CREDS)
    yield instance
    await instance.aclose()


def test_it_declares_the_broadest_capability_set() -> None:
    provider = FmpProvider(CREDS)
    for capability in (
        Capability.UNIVERSE,
        Capability.QUOTES,
        Capability.BARS,
        Capability.NEWS,
        Capability.CORPORATE_ACTIONS,
    ):
        assert capability in provider.capabilities
    assert provider.supports(Capability.BARS, AssetClass.COMMODITY)
    assert not provider.supports(Capability.BARS, AssetClass.CRYPTO)


def test_it_needs_an_api_key() -> None:
    assert FmpProvider().missing_credentials == ("api_key",)
    assert FmpProvider(CREDS).available


@respx.mock
async def test_the_api_key_is_attached_to_every_request(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/stable/batch-quote").mock(
        return_value=httpx.Response(200, json=QUOTES)
    )

    await provider.get_quotes(["AAPL"])

    assert route.calls[0].request.url.params["apikey"] == "FMP-TEST"


@respx.mock
async def test_quotes_use_previous_close_when_present(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/batch-quote").mock(return_value=httpx.Response(200, json=QUOTES))

    quotes = await provider.get_quotes(["AAPL", "SPY"])

    assert quotes["AAPL"].price == Decimal("305.93")
    assert quotes["AAPL"].previous_close == Decimal("306.0")


@respx.mock
async def test_quotes_derive_previous_close_from_change_when_absent(provider) -> None:  # type: ignore[no-untyped-def]
    """SPY in the fixture has no previousClose, only a change."""
    respx.get(f"{BASE}/stable/batch-quote").mock(return_value=httpx.Response(200, json=QUOTES))

    spy = (await provider.get_quotes(["SPY"]))["SPY"]

    assert spy.previous_close == Decimal("776.34") - Decimal("-1.54")


@respx.mock
async def test_quotes_are_batched(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/stable/batch-quote").mock(
        return_value=httpx.Response(200, json=QUOTES)
    )

    await provider.get_quotes([f"S{i}" for i in range(120)])

    assert route.call_count == 3


@respx.mock
async def test_bars_are_reversed_into_ascending_order(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/historical-price-eod/full").mock(
        return_value=httpx.Response(200, json=EOD)
    )

    bars = await provider.get_bars("AAPL", days=30)

    assert [b.bar_date.isoformat() for b in bars] == ["2026-08-12", "2026-08-13", "2026-08-14"]
    assert bars[-1].close == Decimal("305.93")


@respx.mock
async def test_bars_are_truncated_to_the_requested_count(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/historical-price-eod/full").mock(
        return_value=httpx.Response(200, json=EOD)
    )

    assert len(await provider.get_bars("AAPL", days=2)) == 2


@respx.mock
async def test_the_stock_universe_drops_illiquid_names(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/company-screener").mock(
        return_value=httpx.Response(200, json=SCREENER)
    )

    entries = await provider.list_universe(AssetClass.STOCK)

    assert [e.symbol for e in entries] == ["AAPL"]
    assert entries[0].sector == "Technology"


@respx.mock
async def test_the_commodity_universe_is_a_fixed_list(provider) -> None:  # type: ignore[no-untyped-def]
    entries = await provider.list_universe(AssetClass.COMMODITY)

    assert "GCUSD" in [e.symbol for e in entries]
    assert all(e.asset_class is AssetClass.COMMODITY for e in entries)


@respx.mock
async def test_crypto_is_not_served_by_this_provider(provider) -> None:  # type: ignore[no-untyped-def]
    assert await provider.list_universe(AssetClass.CRYPTO) == []


@respx.mock
async def test_splits_become_a_ratio(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/splits").mock(
        return_value=httpx.Response(
            200, json=[{"date": "2026-06-10", "numerator": 4, "denominator": 1}]
        )
    )
    respx.get(f"{BASE}/stable/dividends").mock(return_value=httpx.Response(200, json=[]))

    actions = await provider.get_corporate_actions("AAPL")

    assert len(actions) == 1
    assert actions[0].kind == SplitKind.SPLIT
    assert actions[0].split_ratio == Decimal(4)


@respx.mock
async def test_dividends_prefer_the_adjusted_amount(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/splits").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/stable/dividends").mock(
        return_value=httpx.Response(
            200, json=[{"date": "2026-05-09", "dividend": 0.25, "adjDividend": 0.24}]
        )
    )

    actions = await provider.get_corporate_actions("AAPL")

    assert actions[0].kind == SplitKind.DIVIDEND
    assert actions[0].cash_amount == Decimal("0.24")


@respx.mock
async def test_corporate_actions_respect_the_since_cutoff(provider) -> None:  # type: ignore[no-untyped-def]
    from datetime import date

    respx.get(f"{BASE}/stable/splits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"date": "2020-08-31", "numerator": 4, "denominator": 1},
                {"date": "2026-06-10", "numerator": 2, "denominator": 1},
            ],
        )
    )
    respx.get(f"{BASE}/stable/dividends").mock(return_value=httpx.Response(200, json=[]))

    actions = await provider.get_corporate_actions("AAPL", since=date(2026, 1, 1))

    assert [a.effective_date.isoformat() for a in actions] == ["2026-06-10"]


@respx.mock
async def test_quotes_fall_back_to_per_symbol_when_batch_is_not_entitled(provider) -> None:  # type: ignore[no-untyped-def]
    """Observed on a live plan: batch-quote answers 402 while /quote works."""
    batch = respx.get(f"{BASE}/stable/batch-quote").mock(return_value=httpx.Response(402))
    single = respx.get(f"{BASE}/stable/quote").mock(
        return_value=httpx.Response(200, json=[QUOTES[0]])
    )

    quotes = await provider.get_quotes(["AAPL"])

    assert quotes["AAPL"].price == Decimal("305.93")
    assert batch.called and single.called


@respx.mock
async def test_a_denied_batch_endpoint_is_not_retried(provider) -> None:  # type: ignore[no-untyped-def]
    batch = respx.get(f"{BASE}/stable/batch-quote").mock(return_value=httpx.Response(402))
    respx.get(f"{BASE}/stable/quote").mock(return_value=httpx.Response(200, json=[QUOTES[0]]))

    for _ in range(3):
        await provider.get_quotes(["AAPL"])

    assert batch.call_count == 1


@respx.mock
async def test_the_stock_universe_falls_back_to_core_plus_most_actives(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/company-screener").mock(return_value=httpx.Response(402))
    respx.get(f"{BASE}/stable/most-actives").mock(
        return_value=httpx.Response(200, json=[{"symbol": "RARE", "price": 180, "name": "Rare Co"}])
    )

    symbols = [e.symbol for e in await provider.list_universe(AssetClass.STOCK)]

    assert "AAPL" in symbols
    assert "RARE" in symbols
    assert len(symbols) > len(CORE_STOCKS) - 1


@respx.mock
async def test_the_universe_never_selects_on_one_day_gains(provider) -> None:
    """Ranking a universe by today's biggest movers buys the short-term reversal, not momentum."""
    assert all("gainer" not in path for path in ACTIVITY_PATHS)


@respx.mock
async def test_a_penny_name_from_the_activity_list_is_rejected(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/company-screener").mock(return_value=httpx.Response(402))
    respx.get(f"{BASE}/stable/most-actives").mock(
        return_value=httpx.Response(200, json=[{"symbol": "PENNY", "price": 0.4, "name": "Penny"}])
    )

    symbols = [e.symbol for e in await provider.list_universe(AssetClass.STOCK)]

    assert "PENNY" not in symbols


@respx.mock
async def test_the_etf_universe_falls_back_to_a_core_list(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/company-screener").mock(return_value=httpx.Response(402))

    entries = await provider.list_universe(AssetClass.ETF)
    symbols = [e.symbol for e in entries]

    assert "SPY" in symbols and "QQQ" in symbols
    assert all(e.asset_class is AssetClass.ETF for e in entries)


@respx.mock
async def test_unexpected_batch_payload_falls_through_to_single_quotes(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stable/batch-quote").mock(
        return_value=httpx.Response(200, json={"Error Message": "limit reached"})
    )
    respx.get(f"{BASE}/stable/quote").mock(return_value=httpx.Response(200, json=[QUOTES[0]]))

    assert (await provider.get_quotes(["AAPL"]))["AAPL"].price == Decimal("305.93")
