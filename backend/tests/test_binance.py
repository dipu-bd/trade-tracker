from decimal import Decimal

import httpx
import pytest
import respx

from tradebot.providers.base import AssetClass, ProviderError
from tradebot.providers.impl.binance import BinanceProvider
from tradebot.providers.impl.cryptocom import CryptoComProvider

BASE = "https://api.binance.com/api/v3"

TICKERS = [
    {
        "symbol": "BTCUSDT",
        "lastPrice": "63000",
        "prevClosePrice": "62000",
        "bidPrice": "62999",
        "askPrice": "63001",
        "volume": "1200",
        "quoteVolume": "900000000",
        "closeTime": 1786737600000,
    },
    {
        "symbol": "ETHUSDT",
        "lastPrice": "1900",
        "prevClosePrice": "1920",
        "bidPrice": "1899",
        "askPrice": "1901",
        "volume": "5000",
        "quoteVolume": "400000000",
        "closeTime": 1786737600000,
    },
    {
        "symbol": "BTCFDUSD",
        "lastPrice": "63010",
        "prevClosePrice": "62010",
        "volume": "10",
        "quoteVolume": "100000000",
        "closeTime": 1786737600000,
    },
    {"symbol": "USD1USDT", "lastPrice": "1", "quoteVolume": "80000000", "closeTime": 1786737600000},
    {
        "symbol": "FDUSDUSDT",
        "lastPrice": "1",
        "quoteVolume": "70000000",
        "closeTime": 1786737600000,
    },
    {
        "symbol": "BTCUPUSDT",
        "lastPrice": "5",
        "quoteVolume": "60000000",
        "closeTime": 1786737600000,
    },
    {
        "symbol": "ETHBTC",
        "lastPrice": "0.03",
        "quoteVolume": "50000000",
        "closeTime": 1786737600000,
    },
    {"symbol": "TINYUSDT", "lastPrice": "2", "quoteVolume": "1000", "closeTime": 1786737600000},
]

KLINES = [
    [1786564800000, "62000", "63000", "61500", "62500", "10", 0, 0, 0, 0, 0, 0],
    [1786651200000, "62500", "63500", "62000", "63000", "12", 0, 0, 0, 0, 0, 0],
    [1786737600000, "63000", "63200", "62800", "63100", "8", 0, 0, 0, 0, 0, 0],
]


@pytest.fixture
async def provider():  # type: ignore[no-untyped-def]
    instance = BinanceProvider()
    yield instance
    await instance.aclose()


@pytest.mark.parametrize(
    ("venue", "canonical"),
    [
        ("BTCUSDT", "BTC-USD"),
        ("ETHFDUSD", "ETH-USD"),
        ("SOLUSDC", "SOL-USD"),
        ("ADAUSD", "ADA-USD"),
    ],
)
def test_dollar_quotes_collapse_to_one_canonical_leg(venue: str, canonical: str) -> None:
    assert BinanceProvider().to_canonical(venue) == canonical


def test_canonical_round_trips_to_the_venue_symbol() -> None:
    assert BinanceProvider().to_venue("BTC-USD") == "BTCUSDT"


def test_both_crypto_venues_agree_on_the_canonical_symbol() -> None:
    """Without this the same coin would become two instruments."""
    assert BinanceProvider().to_canonical("BTCUSDT") == CryptoComProvider().to_canonical("BTC_USD")


@respx.mock
async def test_universe_excludes_stablecoins_leverage_and_non_dollar_quotes(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKERS))

    symbols = [e.symbol for e in await provider.list_universe(AssetClass.CRYPTO)]

    assert symbols == ["BTC-USD", "ETH-USD"]


@respx.mock
async def test_a_novel_dollar_stablecoin_is_still_excluded(provider) -> None:  # type: ignore[no-untyped-def]
    """USD1 is not on any fixed list; the shape rule is what catches it."""
    respx.get(f"{BASE}/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKERS))

    symbols = [e.symbol for e in await provider.list_universe(AssetClass.CRYPTO)]

    assert "USD1-USD" not in symbols


@respx.mock
async def test_duplicate_dollar_pairs_keep_only_the_deepest(provider) -> None:  # type: ignore[no-untyped-def]
    """BTCUSDT and BTCFDUSD both map to BTC-USD; the higher-volume book wins."""
    respx.get(f"{BASE}/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKERS))

    entries = await provider.list_universe(AssetClass.CRYPTO)
    btc = [e for e in entries if e.symbol == "BTC-USD"]

    assert len(btc) == 1
    assert btc[0].dollar_volume == Decimal("900000000")


@respx.mock
async def test_quotes_are_keyed_by_canonical_symbol(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKERS))

    quotes = await provider.get_quotes(["BTC-USD", "ETH-USD"])

    assert set(quotes) == {"BTC-USD", "ETH-USD"}
    assert quotes["BTC-USD"].price == Decimal("63000")
    assert quotes["BTC-USD"].previous_close == Decimal("62000")
    assert quotes["BTC-USD"].spread == Decimal("2")


@respx.mock
async def test_no_symbols_makes_no_request(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKERS))

    assert await provider.get_quotes([]) == {}
    assert not route.called


@respx.mock
async def test_bars_parse_the_kline_array_and_sort_ascending(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/klines").mock(return_value=httpx.Response(200, json=KLINES))

    bars = await provider.get_bars("BTC-USD", days=30)

    assert [b.bar_date.isoformat() for b in bars] == ["2026-08-12", "2026-08-13", "2026-08-14"]
    assert bars[0].open == Decimal("62000")
    assert bars[-1].close == Decimal("63100")
    assert bars[0].symbol == "BTC-USD"


@respx.mock
async def test_bars_request_the_translated_venue_symbol(provider) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(f"{BASE}/klines").mock(return_value=httpx.Response(200, json=KLINES))

    await provider.get_bars("BTC-USD", days=30)

    assert route.calls[0].request.url.params["symbol"] == "BTCUSDT"


@respx.mock
async def test_malformed_klines_are_skipped(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/klines").mock(
        return_value=httpx.Response(200, json=[*KLINES, [1, "x"], "junk", None])
    )

    assert len(await provider.get_bars("BTC-USD", days=30)) == 3


@respx.mock
async def test_unexpected_payload_shape_raises(provider) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{BASE}/ticker/24hr").mock(return_value=httpx.Response(200, json={"bad": 1}))

    with pytest.raises(ProviderError, match="unexpected"):
        await provider.list_universe(AssetClass.CRYPTO)


def test_the_provider_needs_no_credentials() -> None:
    assert BinanceProvider().available
    assert BinanceProvider().credential_fields == ()


def test_binance_outranks_cryptocom_by_default() -> None:
    assert BinanceProvider.default_priority < CryptoComProvider.default_priority
