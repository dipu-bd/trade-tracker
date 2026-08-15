import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from tradebot.core.clock import Clock, LiveClock
from tradebot.providers.base import RateLimits

MAX_BACKOFF_SECONDS = 60.0


@dataclass
class _Window:
    """Sliding count over a span, kept as timestamps so a burst cannot hide inside a bucket."""

    span_seconds: float
    limit: int
    stamps: list[float] = field(default_factory=list)

    def prune(self, now: float) -> None:
        cutoff = now - self.span_seconds
        if self.stamps and self.stamps[0] <= cutoff:
            self.stamps = [stamp for stamp in self.stamps if stamp > cutoff]

    def has_room(self, now: float, count: int = 1) -> bool:
        if self.limit <= 0:
            return True
        self.prune(now)
        return len(self.stamps) + count <= self.limit

    def record(self, now: float, count: int = 1) -> None:
        if self.limit > 0:
            self.stamps.extend([now] * count)

    def retry_after(self, now: float, count: int = 1) -> float:
        if self.limit <= 0:
            return 0.0
        self.prune(now)
        excess = len(self.stamps) + count - self.limit
        if excess <= 0:
            return 0.0
        if excess > len(self.stamps):
            raise ValueError(f"request of {count} exceeds the limit of {self.limit}")
        return max(0.0, self.stamps[excess - 1] + self.span_seconds - now)


class RateLimitGovernor:
    """Queues requests to stay inside a provider tier rather than letting them 429.

    Free tiers are the binding constraint here, not cost, so exceeding a limit must never drop a
    decision cycle — callers wait, and an observed 429 tightens the local view of the limit.
    """

    def __init__(
        self,
        limits: RateLimits,
        *,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._limits = limits
        self._clock = clock or LiveClock()
        self._sleep = sleep or asyncio.sleep
        self._minute = _Window(60.0, limits.requests_per_minute)
        self._day = _Window(86_400.0, limits.requests_per_day)
        self._tokens = _Window(60.0, limits.tokens_per_minute)
        self._semaphore = asyncio.Semaphore(max(1, limits.max_concurrency))
        self._lock = asyncio.Lock()
        self._penalty_until = 0.0
        self._consecutive_429 = 0

    def _now(self) -> float:
        return self._clock.now().timestamp()

    @property
    def penalised(self) -> bool:
        return self._now() < self._penalty_until

    def headroom(self) -> dict[str, int | None]:
        now = self._now()
        self._minute.prune(now)
        self._day.prune(now)
        return {
            "requests_per_minute": (
                None if self._minute.limit <= 0 else self._minute.limit - len(self._minute.stamps)
            ),
            "requests_per_day": (
                None if self._day.limit <= 0 else self._day.limit - len(self._day.stamps)
            ),
        }

    def _wait_needed(self, now: float, estimated_tokens: int) -> float:
        waits = [
            self._penalty_until - now,
            self._minute.retry_after(now),
            self._day.retry_after(now),
        ]
        if estimated_tokens and self._tokens.limit > 0:
            waits.append(self._tokens.retry_after(now, estimated_tokens))
        return max([0.0, *waits])

    async def acquire(self, *, estimated_tokens: int = 0) -> None:
        await self._semaphore.acquire()
        try:
            while True:
                async with self._lock:
                    now = self._now()
                    wait = self._wait_needed(now, estimated_tokens)
                    if wait <= 0:
                        self._minute.record(now)
                        self._day.record(now)
                        self._tokens.record(now, estimated_tokens)
                        return
                jitter = random.uniform(0, 0.25)  # noqa: S311
                await self._sleep(wait + jitter)
        except BaseException:
            self._semaphore.release()
            raise

    def release(self) -> None:
        self._semaphore.release()

    def record_success(self) -> None:
        self._consecutive_429 = 0

    def record_rate_limited(self, *, retry_after: float | None = None) -> None:
        """A 429 means the advertised limit was wrong, so back off on top of the local window."""
        self._consecutive_429 += 1
        backoff = retry_after if retry_after is not None else 2.0**self._consecutive_429
        self._penalty_until = self._now() + min(backoff, MAX_BACKOFF_SECONDS)

    async def __aenter__(self) -> "RateLimitGovernor":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
