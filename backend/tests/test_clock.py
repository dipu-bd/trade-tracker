from datetime import UTC, datetime, timedelta

import pytest

from tradebot.core.clock import Clock, FrozenClock, LiveClock, ReplayClock

T0 = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def test_live_clock_is_timezone_aware() -> None:
    assert LiveClock().now().tzinfo is not None


def test_replay_clock_reports_the_simulated_instant() -> None:
    assert ReplayClock(T0).now() == T0


def test_replay_clock_advances_forward() -> None:
    clock = ReplayClock(T0)
    clock.advance_to(T0 + timedelta(minutes=5))
    assert clock.now() == T0 + timedelta(minutes=5)


def test_replay_clock_refuses_to_move_backwards() -> None:
    clock = ReplayClock(T0)
    with pytest.raises(ValueError, match="backwards"):
        clock.advance_to(T0 - timedelta(seconds=1))


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ReplayClock(datetime(2026, 1, 2, 14, 30))


def test_frozen_clock_does_not_move() -> None:
    clock = FrozenClock(T0)
    assert clock.now() == clock.now() == T0


@pytest.mark.parametrize("clock", [LiveClock(), ReplayClock(T0), FrozenClock(T0)])
def test_every_clock_satisfies_the_protocol(clock: Clock) -> None:
    assert isinstance(clock, Clock)
