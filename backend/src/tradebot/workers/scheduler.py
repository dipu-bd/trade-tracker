from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from tradebot.context import AppContext
from tradebot.core.logging import get_logger
from tradebot.engine.runner import EngineRunner

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Cadence:
    name: str
    cron: str
    description: str


CADENCES = (
    Cadence("daily", "45 20 * * mon-fri", "Once after the US equity close"),
    Cadence("twice_daily", "45 13,20 * * mon-fri", "Shortly after the open and after the close"),
    Cadence("hourly", "5 13-20 * * mon-fri", "Every hour through the US session"),
    Cadence("crypto_daily", "5 0 * * *", "Once at UTC midnight, for 24/7 sleeves"),
)

BY_NAME = {cadence.name: cadence for cadence in CADENCES}


class EngineScheduler:
    """UTC cron only.

    The cron expressions are wall-clock triggers for *when to look*; the engine still reads the
    time from the injected clock, so a replay is driven by its cursor and never by this.
    """

    def __init__(self, context: AppContext) -> None:
        self._context = context
        self._runner = EngineRunner(context)
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    @property
    def runner(self) -> EngineRunner:
        return self._runner

    def start(self) -> None:
        for cadence in CADENCES:
            self._scheduler.add_job(
                self._runner.run_due,
                CronTrigger.from_crontab(cadence.cron, timezone="UTC"),
                args=[cadence.name],
                id=f"cycle:{cadence.name}",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        self._scheduler.start()
        _log.info("scheduler_started", cadences=[item.name for item in CADENCES])

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def jobs(self) -> list[dict[str, str | None]]:
        return [
            {
                "id": job.id,
                "cron": BY_NAME[job.id.split(":", 1)[1]].cron,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in self._scheduler.get_jobs()
        ]
