from decimal import Decimal

import httpx
import pytest
import respx

from tradebot.providers.base import AssetClass, Capability, ProviderConfig, ProviderError
from tradebot.providers.impl.alpaca import AlpacaProvider

BASE = "https://data.alpaca.markets/v2"
CREDS = ProviderConfig(credentials={"api_key_id": "AK-TEST", "secret_key": "SK-TEST"})

SNAPSHOTS = {
    "AAPL": {
        "latestTrade": {"p": 232.14, "t": "2026-08-14T19:59:59.123456789Z"},
        "latestQuote": {"bp": 232.10, "ap": 232.18},
        "dailyBar": {"c": 232.05, "v": 48_000_000},
        "prevDailyBar": {"c": 229.80},
    },
    "SPY": {
        "latestTrade": {"p": 776.34, "t": "2026-08-14T19:59:59.000000000Z"},
        "latestQuote": {"bp": 776.30, "ap": 776.38},
        "dailyBar": {"c": 776.20, "v": 29_940_248},
        "prevDailyBar": {"c": 777.88},
    },
}

BARS = {
    "bars": {
        "AAPL": [
            {
                "t": "2026-08-12T04:00:00Z",
                "o": 228,
                "h": 231,
                "l": 227.5,
                "c": 230.1,
                "v": 41_000_000,
            },
            {
                "t": "2026-08-13T04:00:00Z",
                "o": 230.2,
                "h": 232,
                "l": 229.4,
                "c": 229.8,
                "v": 39_500_000,
            },
            {
                "t": "2026-08-14T04:00:00Z",
                "o": 229.9,
                "h": 233,
                "l": 229.7,
                "c": 232.05,
                "v": 48_000_000,
            },
        ]
    },
    "next_page_token": None,
}


@pytest.fixture
async def provider():  # type: ignore[no-untyped-def]
    instance = AlpacaProvider(CREDS)
    yield instance
    await instance.aclose()


def test_it_declares_two_credential_fields_and_needs_both() -> None:
    assert AlpacaProvider().missing_credentials == ("api_key_id", "secret_key")
    assert not AlpacaProvider().available
    assert AlpacaProvider(CREDS).available


def test_it_serves_equities_not_crypto() -> None:
    provider = AlpacaProvider(CREDS)
    assert provider.supports(Capability.QUOTES, AssetClass.STOCK)
    assert provider.supports(Capability.BARS, AssetClass.ETF)
    assert not provider.supports(Capability.QUOTES, AssetClass.CRYPTO)


@respx.mock
async def test_quotes_come_from_the_snapshot_endpoint(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stocks/snapshots").mock(return_value=httpx.Response(200, json=SNAPSHOTS))

    quotes = await provider.get_quotes(["AAPL", "SPY"])

    assert set(quotes) == {"AAPL", "SPY"}
    assert quotes["AAPL"].price == Decimal("232.14")
    assert quotes["AAPL"].previous_close == Decimal("229.8")
    assert quotes["AAPL"].spread == Decimal("232.18") - Decimal("232.10")


@respx.mock
async def test_credentials_are_sent_as_alpaca_headers(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/stocks/snapshots").mock(
        return_value=httpx.Response(200, json=SNAPSHOTS)
    )

    await provider.get_quotes(["AAPL"])

    request = route.calls[0].request
    assert request.headers["APCA-API-KEY-ID"] == "AK-TEST"
    assert request.headers["APCA-API-SECRET-KEY"] == "SK-TEST"


@respx.mock
async def test_quotes_are_batched(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/stocks/snapshots").mock(
        return_value=httpx.Response(200, json=SNAPSHOTS)
    )

    await provider.get_quotes([f"SYM{i}" for i in range(250)])

    assert route.call_count == 3


@respx.mock
async def test_no_symbols_makes_no_request(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/stocks/snapshots").mock(
        return_value=httpx.Response(200, json=SNAPSHOTS)
    )

    assert await provider.get_quotes([]) == {}
    assert not route.called


@respx.mock
async def test_a_snapshot_without_a_trade_falls_back_to_the_daily_close(provider) -> None:  # type: ignore[no-untyped-def]
    payload = {"AAPL": {"dailyBar": {"c": 100, "v": 1}, "prevDailyBar": {"c": 99}}}
    respx.get(f"{BASE}/stocks/snapshots").mock(return_value=httpx.Response(200, json=payload))

    assert (await provider.get_quotes(["AAPL"]))["AAPL"].price == Decimal("100")


@respx.mock
async def test_an_unpriced_snapshot_is_dropped_not_fatal(provider) -> None:  # type: ignore[no-untyped-def]
    payload = {"AAPL": {"latestTrade": {}}, "SPY": SNAPSHOTS["SPY"]}
    respx.get(f"{BASE}/stocks/snapshots").mock(return_value=httpx.Response(200, json=payload))

    assert set(await provider.get_quotes(["AAPL", "SPY"])) == {"SPY"}


@respx.mock
async def test_bars_are_sorted_ascending_and_split_adjusted(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/stocks/bars").mock(return_value=httpx.Response(200, json=BARS))

    bars = await provider.get_bars("AAPL", days=30)

    assert [b.bar_date.isoformat() for b in bars] == ["2026-08-12", "2026-08-13", "2026-08-14"]
    assert bars[-1].close == Decimal("232.05")
    assert route.calls[0].request.url.params["adjustment"] == "all"


@respx.mock
async def test_bars_follow_pagination(provider) -> None:  # type: ignore[no-untyped-def]
    first = {"bars": {"AAPL": BARS["bars"]["AAPL"][:2]}, "next_page_token": "more"}
    second = {"bars": {"AAPL": BARS["bars"]["AAPL"][2:]}, "next_page_token": None}
    respx.get(f"{BASE}/stocks/bars").mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
    )

    assert len(await provider.get_bars("AAPL", days=30)) == 3


@respx.mock
async def test_bars_are_truncated_to_the_requested_count(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stocks/bars").mock(return_value=httpx.Response(200, json=BARS))

    assert len(await provider.get_bars("AAPL", days=2)) == 2


@respx.mock
async def test_an_empty_bar_set_is_not_an_error(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stocks/bars").mock(
        return_value=httpx.Response(200, json={"bars": {}, "next_page_token": None})
    )

    assert await provider.get_bars("NOPE", days=30) == []


@respx.mock
async def test_unexpected_payload_shape_raises(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/stocks/snapshots").mock(return_value=httpx.Response(200, json=[1, 2]))

    with pytest.raises(ProviderError, match="unexpected"):
        await provider.get_quotes(["AAPL"])
