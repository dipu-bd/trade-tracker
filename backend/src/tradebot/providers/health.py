from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from tradebot.core.clock import Clock, LiveClock

SAMPLE_SIZE = 50
OPEN_AFTER_FAILURES = 5
HALF_OPEN_AFTER_SECONDS = 60.0


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ProviderHealth:
    """Circuit breaker plus rolling latency, so the router can route around a sick provider."""

    provider_key: str
    clock: Clock = field(default_factory=LiveClock)
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLE_SIZE))
    outcomes: deque[bool] = field(default_factory=lambda: deque(maxlen=SAMPLE_SIZE))
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_error: str | None = None
    requests: int = 0

    def _now(self) -> float:
        return self.clock.now().timestamp()

    @property
    def state(self) -> BreakerState:
        if self.opened_at is None:
            return BreakerState.CLOSED
        if self._now() - self.opened_at >= HALF_OPEN_AFTER_SECONDS:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    @property
    def usable(self) -> bool:
        return self.state is not BreakerState.OPEN

    @property
    def error_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for ok in self.outcomes if not ok) / len(self.outcomes)

    def percentile(self, fraction: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        index = min(len(ordered) - 1, int(fraction * len(ordered)))
        return ordered[index]

    def record_success(self, latency: float) -> None:
        self.requests += 1
        self.latencies.append(latency)
        self.outcomes.append(True)
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self, error: str, *, latency: float | None = None) -> None:
        self.requests += 1
        if latency is not None:
            self.latencies.append(latency)
        self.outcomes.append(False)
        self.consecutive_failures += 1
        self.last_error = error[:500]
        if self.consecutive_failures >= OPEN_AFTER_FAILURES:
            self.opened_at = self._now()

    def snapshot(self) -> dict[str, object]:
        return {
            "provider": self.provider_key,
            "state": self.state.value,
            "requests": self.requests,
            "error_rate": round(self.error_rate, 4),
            "latency_p50": round(self.percentile(0.5), 4),
            "latency_p95": round(self.percentile(0.95), 4),
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }
