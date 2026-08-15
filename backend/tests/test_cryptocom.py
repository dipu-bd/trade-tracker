from decimal import Decimal

import httpx
import pytest
import respx

from tradebot.providers.base import (
    AssetClass,
    ProviderError,
    ProviderRateLimitedError,
)
from tradebot.providers.impl.cryptocom import CryptoComProvider

BASE = "https://api.crypto.com/exchange/v1/public"

TICKERS = {
    "result": {
        "data": [
            {
                "i": "BTC_USD",
                "a": "63000.5",
                "c": "0.02",
                "b": "62999",
                "k": "63001",
                "v": "1200",
                "vv": "138000000",
                "t": 1786737600000,
            },
            {
                "i": "ETH_USD",
                "a": "1900",
                "c": "-0.01",
                "b": "1899",
                "k": "1901",
                "v": "5000",
                "vv": "82000000",
                "t": 1786737600000,
            },
            {"i": "USDT_USD", "a": "1.0", "c": "0.0", "vv": "99000000", "t": 1786737600000},
            {"i": "BTC3L_USD", "a": "10", "c": "0.1", "vv": "50000000", "t": 1786737600000},
            {"i": "BTCUSD-PERP", "a": "63000", "c": "0.02", "vv": "900000000", "t": 1786737600000},
            {"i": "DOGE_USDT", "a": "0.1", "c": "0.01", "vv": "70000000", "t": 1786737600000},
            {"i": "TINY_USD", "a": "5", "c": "0.01", "vv": "1000", "t": 1786737600000},
        ]
    }
}

CANDLES = {
    "result": {
        "data": [
            {"t": 1786564800000, "o": "62000", "h": "63000", "l": "61500", "c": "62500", "v": "10"},
            {"t": 1786651200000, "o": "62500", "h": "63500", "l": "62000", "c": "63000", "v": "12"},
            {"t": 1786737600000, "o": "63000", "h": "63200", "l": "62800", "c": "63100", "v": "8"},
        ]
    }
}


@pytest.fixture
async def provider():  # type: ignore[no-untyped-def]
    instance = CryptoComProvider()
    yield instance
    await instance.aclose()


@respx.mock
async def test_universe_excludes_stablecoins_leverage_perps_and_foreign_quotes(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-tickers").mock(return_value=httpx.Response(200, json=TICKERS))

    symbols = [e.symbol for e in await provider.list_universe(AssetClass.CRYPTO)]

    assert symbols == ["BTC-USD", "ETH-USD"]


@respx.mock
async def test_universe_is_ranked_by_volume(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-tickers").mock(return_value=httpx.Response(200, json=TICKERS))

    entries = await provider.list_universe(AssetClass.CRYPTO)

    assert entries[0].dollar_volume > entries[1].dollar_volume
    assert entries[0].asset_class is AssetClass.CRYPTO


@respx.mock
async def test_universe_is_empty_for_non_crypto(provider) -> None:  # type: ignore[no-untyped-def]
    assert await provider.list_universe(AssetClass.STOCK) == []


@respx.mock
async def test_quotes_derive_previous_close_from_the_change_ratio(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-tickers").mock(return_value=httpx.Response(200, json=TICKERS))

    quotes = await provider.get_quotes(["BTC-USD"])

    btc = quotes["BTC-USD"]
    assert btc.price == Decimal("63000.5")
    assert btc.previous_close == Decimal("63000.5") / Decimal("1.02")
    assert btc.spread == Decimal("2")


@respx.mock
async def test_quotes_ignore_unrequested_symbols(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-tickers").mock(return_value=httpx.Response(200, json=TICKERS))

    assert set(await provider.get_quotes(["ETH-USD"])) == {"ETH-USD"}


@respx.mock
async def test_no_symbols_makes_no_request(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/get-tickers").mock(return_value=httpx.Response(200, json=TICKERS))

    assert await provider.get_quotes([]) == {}
    assert not route.called


@respx.mock
async def test_bars_are_returned_oldest_first(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-candlestick").mock(return_value=httpx.Response(200, json=CANDLES))

    bars = await provider.get_bars("BTC-USD", days=30)

    assert [b.bar_date.isoformat() for b in bars] == ["2026-08-12", "2026-08-13", "2026-08-14"]
    assert bars[0].close == Decimal("62500")


@respx.mock
async def test_bars_are_truncated_to_the_requested_count(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-candlestick").mock(return_value=httpx.Response(200, json=CANDLES))

    assert len(await provider.get_bars("BTC-USD", days=2)) == 2


@respx.mock
async def test_malformed_rows_are_skipped_not_fatal(provider) -> None:  # type: ignore[no-untyped-def]
    payload = {"result": {"data": [*CANDLES["result"]["data"], {"t": None, "o": "x"}, "junk"]}}
    respx.get(f"{BASE}/get-candlestick").mock(return_value=httpx.Response(200, json=payload))

    assert len(await provider.get_bars("BTC-USD", days=30)) == 3


@respx.mock
async def test_unexpected_payload_shape_raises(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-tickers").mock(return_value=httpx.Response(200, json={"result": {}}))

    with pytest.raises(ProviderError, match="unexpected"):
        await provider.list_universe(AssetClass.CRYPTO)


@respx.mock
async def test_a_429_becomes_a_rate_limited_error_with_retry_after(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-tickers").mock(
        return_value=httpx.Response(429, headers={"retry-after": "12"})
    )

    with pytest.raises(ProviderRateLimitedError) as caught:
        await provider.list_universe(AssetClass.CRYPTO)
    assert caught.value.retry_after == 12.0


@respx.mock
async def test_a_denied_endpoint_is_not_retried(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/get-tickers").mock(return_value=httpx.Response(403))

    for _ in range(3):
        with pytest.raises(ProviderError):
            await provider.list_universe(AssetClass.CRYPTO)

    assert route.call_count == 1


@respx.mock
async def test_a_timeout_becomes_a_provider_error(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/get-tickers").mock(side_effect=httpx.ConnectTimeout("slow"))

    with pytest.raises(ProviderError, match="timeout"):
        await provider.list_universe(AssetClass.CRYPTO)


def test_the_provider_needs_no_credentials() -> None:
    assert CryptoComProvider().available
    assert CryptoComProvider().credential_fields == ()
