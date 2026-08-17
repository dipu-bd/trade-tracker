import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.logging import get_logger
from tradebot.marketdata.service import IngestReport

_log = get_logger(__name__)

ProgressFn = Callable[[str, int, int], None]


@dataclass
class JobState:
    label: str = ""
    running: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    done: int = 0
    total: int = 0
    current: str = ""
    error: str | None = None
    report: IngestReport = field(default_factory=IngestReport)

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "done": self.done,
            "total": self.total,
            "current": self.current,
            "error": self.error,
            "instruments": self.report.instruments,
            "bars_written": self.report.bars_written,
            "quotes_updated": self.report.quotes_updated,
            "skipped_fresh": self.report.skipped_fresh,
            "failed": list(self.report.failed),
        }


class MarketSyncJob:
    """Single-flight background runner for the market sync.

    A full universe pass is thousands of sequential provider calls, which is minutes of work —
    far past any HTTP timeout — so the request starts it and polls. Single-flight because two
    concurrent passes would fight over the same rate-limit budget and finish slower than one.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or LiveClock()
        self._state = JobState()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._state.running

    def snapshot(self) -> dict[str, object]:
        return self._state.as_dict()

    def start(self, label: str, run: Callable[[ProgressFn], Awaitable[IngestReport]]) -> bool:
        """False when a pass is already in flight, which is not an error — it is the answer."""
        if self._state.running:
            return False

        self._state = JobState(label=label, running=True, started_at=self._clock.now())
        self._task = asyncio.create_task(self._run(run))
        return True

    async def _run(self, run: Callable[[ProgressFn], Awaitable[IngestReport]]) -> None:
        state = self._state
        try:
            state.report = await run(self._progress)
        except Exception as error:
            state.error = str(error)
            _log.warning("market_sync_failed", label=state.label, error=str(error))
        finally:
            state.running = False
            state.current = ""
            state.finished_at = self._clock.now()

    def _progress(self, current: str, done: int, total: int) -> None:
        self._state.current = current
        self._state.done = done
        self._state.total = total

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)
