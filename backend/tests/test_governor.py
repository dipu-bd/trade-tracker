import asyncio
from datetime import UTC, datetime, timedelta

from tradebot.providers.base import RateLimits
from tradebot.providers.governor import MAX_BACKOFF_SECONDS, RateLimitGovernor

T0 = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


class MovableClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def governor_for(limits: RateLimits, clock: MovableClock) -> RateLimitGovernor:
    """Sleeps yield to the loop without advancing time, so the test drives the clock."""

    async def parked(_seconds: float) -> None:
        await asyncio.sleep(0)

    return RateLimitGovernor(limits, clock=clock, sleep=parked)


async def test_requests_under_the_limit_pass_immediately() -> None:
    governor = governor_for(RateLimits(requests_per_minute=5), MovableClock(T0))
    for _ in range(5):
        await governor.acquire()
        governor.release()
    assert governor.headroom()["requests_per_minute"] == 0


async def test_unmetered_axis_never_blocks() -> None:
    governor = governor_for(RateLimits(), MovableClock(T0))
    for _ in range(50):
        await governor.acquire()
        governor.release()
    assert governor.headroom()["requests_per_minute"] is None


async def test_exceeding_the_minute_limit_queues_rather_than_failing() -> None:
    clock = MovableClock(T0)
    governor = governor_for(RateLimits(requests_per_minute=3), clock)
    for _ in range(3):
        await governor.acquire()
        governor.release()

    pending = asyncio.create_task(governor.acquire())
    await asyncio.sleep(0.05)
    assert not pending.done()

    clock.advance(61)
    await asyncio.wait_for(pending, timeout=2)
    governor.release()


async def test_parallel_callers_stay_inside_a_three_per_minute_tier() -> None:
    """The free-tier case: four analyst passes fired at once must not trip the limit."""
    clock = MovableClock(T0)
    governor = governor_for(RateLimits(requests_per_minute=3, max_concurrency=4), clock)
    admitted: list[int] = []

    async def caller(index: int) -> None:
        await governor.acquire()
        admitted.append(index)
        governor.release()

    tasks = [asyncio.create_task(caller(i)) for i in range(4)]
    await asyncio.sleep(0.05)
    assert len(admitted) == 3

    clock.advance(61)
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
    assert len(admitted) == 4


async def test_daily_limit_is_enforced_independently() -> None:
    clock = MovableClock(T0)
    governor = governor_for(RateLimits(requests_per_day=2), clock)
    for _ in range(2):
        await governor.acquire()
        governor.release()

    pending = asyncio.create_task(governor.acquire())
    await asyncio.sleep(0.05)
    assert not pending.done()

    clock.advance(86_401)
    await asyncio.wait_for(pending, timeout=2)
    governor.release()


async def test_a_429_backs_off_beyond_the_local_window() -> None:
    clock = MovableClock(T0)
    governor = governor_for(RateLimits(requests_per_minute=100), clock)
    governor.record_rate_limited(retry_after=30)

    assert governor.penalised
    clock.advance(31)
    assert not governor.penalised


async def test_repeated_429s_escalate_but_stay_capped() -> None:
    clock = MovableClock(T0)
    governor = governor_for(RateLimits(), clock)
    for _ in range(20):
        governor.record_rate_limited()

    clock.advance(MAX_BACKOFF_SECONDS + 1)
    assert not governor.penalised


async def test_success_resets_the_escalation() -> None:
    clock = MovableClock(T0)
    governor = governor_for(RateLimits(), clock)
    governor.record_rate_limited()
    governor.record_rate_limited()
    governor.record_success()
    governor.record_rate_limited()

    clock.advance(3)
    assert not governor.penalised


async def test_concurrency_cap_is_respected() -> None:
    governor = governor_for(RateLimits(max_concurrency=2), MovableClock(T0))
    await governor.acquire()
    await governor.acquire()

    pending = asyncio.create_task(governor.acquire())
    await asyncio.sleep(0.05)
    assert not pending.done()

    governor.release()
    await asyncio.wait_for(pending, timeout=2)


async def test_token_budget_blocks_when_exhausted() -> None:
    clock = MovableClock(T0)
    governor = governor_for(RateLimits(tokens_per_minute=1000), clock)
    await governor.acquire(estimated_tokens=900)
    governor.release()

    pending = asyncio.create_task(governor.acquire(estimated_tokens=900))
    await asyncio.sleep(0.05)
    assert not pending.done()

    clock.advance(61)
    await asyncio.wait_for(pending, timeout=2)
    governor.release()


async def test_context_manager_releases_the_slot() -> None:
    governor = governor_for(RateLimits(max_concurrency=1), MovableClock(T0))
    async with governor:
        pass
    await asyncio.wait_for(governor.acquire(), timeout=1)
