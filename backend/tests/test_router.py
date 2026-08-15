import pytest

from tests.fakes import FakeProvider, FlakyProvider, KeyedProvider, ThrottledProvider
from tradebot.providers.base import (
    AssetClass,
    Capability,
    Provider,
    ProviderConfig,
    ProviderUnavailableError,
)
from tradebot.providers.health import OPEN_AFTER_FAILURES, BreakerState
from tradebot.providers.router import ProviderRouter


async def fetch(provider: Provider) -> dict[str, object]:
    return await provider.get_quotes(["AAA"])  # type: ignore[attr-defined,no-any-return]


def test_a_provider_without_its_credentials_is_unavailable() -> None:
    assert not KeyedProvider().available
    assert KeyedProvider().missing_credentials == ("api_key",)
    assert KeyedProvider(ProviderConfig(credentials={"api_key": "x"})).available


def test_capability_and_asset_class_are_both_checked() -> None:
    provider = FakeProvider()
    assert provider.supports(Capability.QUOTES, AssetClass.STOCK)
    assert not provider.supports(Capability.QUOTES, AssetClass.CRYPTO)
    assert not provider.supports(Capability.NEWS, AssetClass.STOCK)


async def test_the_highest_priority_available_provider_wins() -> None:
    primary = FakeProvider(ProviderConfig(priority=1))
    secondary = FakeProvider(ProviderConfig(priority=9))
    router = ProviderRouter([secondary, primary])

    await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)
    assert primary.quote_calls == 1
    assert secondary.quote_calls == 0


async def test_failure_fails_over_to_the_next_provider() -> None:
    flaky = FlakyProvider(ProviderConfig(priority=1))
    healthy = FakeProvider(ProviderConfig(priority=2))
    router = ProviderRouter([flaky, healthy])

    result = await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)

    assert "AAA" in result
    assert healthy.quote_calls == 1
    assert [a.provider_key for a in router.last_attempts] == ["flaky", "fake"]


async def test_an_exhausted_tier_degrades_to_the_fallback() -> None:
    throttled = ThrottledProvider(ProviderConfig(priority=1))
    fallback = FakeProvider(ProviderConfig(priority=2))
    router = ProviderRouter([throttled, fallback])

    await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)

    assert fallback.quote_calls == 1
    assert router.health("throttled").last_error == "rate limited"


async def test_all_providers_failing_raises() -> None:
    router = ProviderRouter([FlakyProvider(ProviderConfig(priority=1))])
    with pytest.raises(ProviderUnavailableError, match="all providers failed"):
        await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)


async def test_no_capable_provider_raises_before_any_call() -> None:
    router = ProviderRouter([FakeProvider()])
    with pytest.raises(ProviderUnavailableError, match="no provider"):
        await router.execute(Capability.NEWS, fetch, asset_class=AssetClass.STOCK)


async def test_providers_missing_credentials_are_skipped() -> None:
    keyed = KeyedProvider(ProviderConfig(priority=1))
    healthy = FakeProvider(ProviderConfig(priority=2))
    router = ProviderRouter([keyed, healthy])

    await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)
    assert keyed.quote_calls == 0
    assert healthy.quote_calls == 1


async def test_repeated_failures_open_the_breaker_and_drop_the_provider() -> None:
    flaky = FlakyProvider(ProviderConfig(priority=1))
    healthy = FakeProvider(ProviderConfig(priority=2))
    router = ProviderRouter([flaky, healthy])

    for _ in range(OPEN_AFTER_FAILURES):
        await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)

    assert router.health("flaky").state is BreakerState.OPEN
    assert flaky not in router.candidates(Capability.QUOTES, AssetClass.STOCK)

    before = flaky.quote_calls
    await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)
    assert flaky.quote_calls == before


async def test_success_records_latency_and_keeps_the_breaker_closed() -> None:
    router = ProviderRouter([FakeProvider()])
    await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)

    health = router.health("fake")
    assert health.state is BreakerState.CLOSED
    assert health.requests == 1
    assert health.error_rate == 0.0


async def test_health_snapshot_includes_rate_limit_headroom() -> None:
    router = ProviderRouter([FakeProvider()])
    await router.execute(Capability.QUOTES, fetch, asset_class=AssetClass.STOCK)

    snapshot = router.health_snapshot()[0]
    assert snapshot["provider"] == "fake"
    assert snapshot["state"] == "closed"
    assert "requests_per_minute" in snapshot


async def test_a_capability_with_no_entitled_provider_is_absent_not_broken() -> None:
    """News rests on one provider; when it is unavailable the capability must simply be absent."""
    router = ProviderRouter([FakeProvider()])

    assert router.candidates(Capability.NEWS, AssetClass.STOCK) == []
    with pytest.raises(ProviderUnavailableError, match="no provider"):
        await router.execute(Capability.NEWS, fetch, asset_class=AssetClass.STOCK)


async def test_losing_the_only_news_provider_does_not_affect_other_capabilities() -> None:
    news_only = KeyedProvider(ProviderConfig(priority=1))
    quotes = FakeProvider(ProviderConfig(priority=2))
    router = ProviderRouter([news_only, quotes])

    assert router.candidates(Capability.NEWS, AssetClass.STOCK) == []
    assert router.candidates(Capability.QUOTES, AssetClass.STOCK) == [quotes]
