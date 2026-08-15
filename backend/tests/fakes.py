from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tradebot.providers.base import (
    AssetClass,
    Bar,
    Capability,
    CredentialField,
    ProviderConfig,
    ProviderError,
    ProviderRateLimitedError,
    Quote,
    RateLimits,
    SupportsBars,
    SupportsQuotes,
    SupportsUniverse,
    UniverseEntry,
)


class FakeProvider(SupportsUniverse, SupportsQuotes, SupportsBars):
    key = "fake"
    label = "Fake"
    capabilities = frozenset({Capability.UNIVERSE, Capability.QUOTES, Capability.BARS})
    asset_classes = frozenset({AssetClass.STOCK, AssetClass.ETF})
    rate_limits = RateLimits(max_concurrency=8)
    default_priority = 10

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)
        self.quote_calls = 0
        self.bar_calls = 0
        self.universe_calls = 0
        self.fail_with: Exception | None = None
        self.prices: dict[str, Decimal] = {}

    async def list_universe(self, asset_class: AssetClass) -> list[UniverseEntry]:
        self.universe_calls += 1
        self._maybe_fail()
        return [UniverseEntry(symbol="AAA", asset_class=asset_class, name="Alpha")]

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        self.quote_calls += 1
        self._maybe_fail()
        now = datetime.now(UTC)
        return {
            symbol: Quote(
                symbol=symbol,
                price=self.prices.get(symbol, Decimal("100")),
                at=now,
                previous_close=Decimal("99"),
            )
            for symbol in symbols
        }

    async def get_bars(self, symbol: str, *, days: int = 260, end: date | None = None) -> list[Bar]:
        self.bar_calls += 1
        self._maybe_fail()
        last = end or date(2026, 1, 30)
        return [
            Bar(
                symbol=symbol,
                bar_date=last - timedelta(days=days - index - 1),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100") + Decimal(index),
                volume=Decimal("1000000"),
            )
            for index in range(days)
        ]

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with


class KeyedProvider(FakeProvider):
    key = "keyed"
    label = "Keyed"
    credential_fields = (CredentialField(name="api_key", label="API key"),)
    default_priority = 5


class FlakyProvider(FakeProvider):
    key = "flaky"
    label = "Flaky"
    default_priority = 1

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)
        self.fail_with = ProviderError("flaky", "boom")


class ThrottledProvider(FakeProvider):
    key = "throttled"
    label = "Throttled"
    default_priority = 1

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)
        self.fail_with = ProviderRateLimitedError("throttled", retry_after=0.01)
