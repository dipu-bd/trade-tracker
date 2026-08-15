from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class LiveClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ReplayClock:
    """Backtest clock. Only ever moves forward, so a replay cannot revisit a past instant."""

    def __init__(self, start: datetime) -> None:
        self._now = _require_aware(start)

    def now(self) -> datetime:
        return self._now

    def advance_to(self, moment: datetime) -> None:
        moment = _require_aware(moment)
        if moment < self._now:
            raise ValueError(f"clock cannot move backwards: {moment} < {self._now}")
        self._now = moment


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self._now = _require_aware(moment)

    def now(self) -> datetime:
        return self._now


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("clock requires timezone-aware datetimes")
    return moment.astimezone(UTC)
