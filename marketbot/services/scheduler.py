"""Background cron for the scan cadence.

Defaults are UTC and tuned to a swing horizon: a pre-open look, the main
decision run shortly before the US close, a post-close digest, and crypto every
four hours because that market never shuts.
"""

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from marketbot.db import Sleeve

_log = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, ctx):
        self._ctx = ctx
        self._scheduler: Optional[BackgroundScheduler] = None

    @property
    def config(self):
        return self._ctx.config.scheduler

    def start(self) -> None:
        if not self.config.enabled:
            _log.info('Scheduler disabled')
            return
        if self._scheduler is not None:
            return

        scheduler = BackgroundScheduler(timezone='UTC')
        jobs = (
            ('preopen', self.config.preopen_cron, Sleeve.EQUITY),
            ('main', self.config.main_cron, Sleeve.EQUITY),
            ('crypto', self.config.crypto_cron, Sleeve.CRYPTO),
        )
        for job_id, expression, sleeve in jobs:
            trigger = _trigger(expression)
            if trigger is None:
                continue
            scheduler.add_job(
                self._scan_all,
                trigger=trigger,
                id=f'scan-{job_id}',
                kwargs={'sleeve': sleeve},
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            _log.info(f'Scheduled {job_id} scan ({sleeve}) at "{expression}" UTC')

        digest_trigger = _trigger(self.config.digest_cron)
        if digest_trigger is not None:
            scheduler.add_job(
                self._digest_all,
                trigger=digest_trigger,
                id='daily-digest',
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            _log.info(f'Scheduled daily digest at "{self.config.digest_cron}" UTC')

        scheduler.start()
        self._scheduler = scheduler

    def close(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    # ----------------------------------------------------------------- #
    # Jobs
    # ----------------------------------------------------------------- #

    def _active_portfolio_ids(self):
        with self._ctx.db.session() as session:
            return [
                p.id for p in self._ctx.portfolios.list_all(session) if p.is_active
            ]

    def _scan_all(self, sleeve: str) -> None:
        for portfolio_id in self._active_portfolio_ids():
            try:
                self._ctx.engine.run_scan(portfolio_id, sleeve=sleeve)
            except Exception:  # noqa: BLE001 — one bad portfolio must not
                _log.exception(f'Scheduled scan failed for portfolio {portfolio_id}')

    def _digest_all(self) -> None:
        for portfolio_id in self._active_portfolio_ids():
            try:
                self._ctx.engine.send_digest(portfolio_id)
            except Exception:  # noqa: BLE001
                _log.exception(f'Digest failed for portfolio {portfolio_id}')


def _trigger(expression: str) -> Optional[CronTrigger]:
    expression = (expression or '').strip()
    if not expression:
        return None
    try:
        return CronTrigger.from_crontab(expression, timezone='UTC')
    except ValueError as e:
        _log.error(f'Ignoring invalid cron expression {expression!r}: {e}')
        return None
