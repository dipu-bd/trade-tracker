import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.logging import get_logger
from tradebot.providers.base import (
    AssetClass,
    Capability,
    Provider,
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from tradebot.providers.governor import RateLimitGovernor
from tradebot.providers.health import ProviderHealth

_log = get_logger(__name__)

T = TypeVar("T")


@dataclass
class Attempt:
    provider_key: str
    ok: bool
    latency: float
    error: str | None = None


class ProviderRouter:
    """Resolves (capability, asset class) to a live provider, failing over on error.

    Each provider gets its own governor and breaker, so one exhausted free tier degrades to the
    next candidate instead of taking the whole cycle down.
    """

    def __init__(self, providers: list[Provider], *, clock: Clock | None = None) -> None:
        self._clock = clock or LiveClock()
        self._providers = sorted(providers, key=lambda p: p.config.priority)
        self._governors = {
            p.key: RateLimitGovernor(p.rate_limits, clock=self._clock) for p in self._providers
        }
        self._health = {
            p.key: ProviderHealth(provider_key=p.key, clock=self._clock) for p in self._providers
        }
        self.last_attempts: list[Attempt] = []

    @property
    def providers(self) -> list[Provider]:
        return list(self._providers)

    def health(self, key: str) -> ProviderHealth:
        return self._health[key]

    def health_snapshot(self) -> list[dict[str, object]]:
        return [
            {**self._health[p.key].snapshot(), **self._governors[p.key].headroom()}
            for p in self._providers
        ]

    def candidates(
        self, capability: Capability, asset_class: AssetClass | None = None
    ) -> list[Provider]:
        return [
            provider
            for provider in self._providers
            if provider.supports(capability, asset_class)
            and provider.available
            and self._health[provider.key].usable
        ]

    async def execute(
        self,
        capability: Capability,
        call: Callable[[Provider], Awaitable[T]],
        *,
        asset_class: AssetClass | None = None,
        estimated_tokens: int = 0,
    ) -> T:
        candidates = self.candidates(capability, asset_class)
        if not candidates:
            raise ProviderUnavailableError("router", f"no provider for {capability}/{asset_class}")

        self.last_attempts = []
        last_error: Exception | None = None

        for provider in candidates:
            governor = self._governors[provider.key]
            health = self._health[provider.key]
            await governor.acquire(estimated_tokens=estimated_tokens)
            started = time.perf_counter()
            try:
                result = await call(provider)
            except ProviderRateLimitedError as exc:
                latency = time.perf_counter() - started
                governor.record_rate_limited(retry_after=exc.retry_after)
                health.record_failure("rate limited", latency=latency)
                self.last_attempts.append(Attempt(provider.key, False, latency, "rate limited"))
                last_error = exc
            except ProviderError as exc:
                latency = time.perf_counter() - started
                health.record_failure(str(exc), latency=latency)
                self.last_attempts.append(Attempt(provider.key, False, latency, str(exc)))
                _log.warning("provider_failed", provider=provider.key, capability=capability.value)
                last_error = exc
            else:
                latency = time.perf_counter() - started
                governor.record_success()
                health.record_success(latency)
                self.last_attempts.append(Attempt(provider.key, True, latency))
                return result
            finally:
                governor.release()

        raise ProviderUnavailableError(
            "router",
            f"all providers failed for {capability}: {last_error}",
        )
