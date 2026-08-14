"""Turns portfolio changes into email.

Default mode is `per_run`: one mail dispatched the moment a scan finishes,
listing every add and remove it made. A run is the atomic unit of change, so
this is "as soon as it happens" without eight separate mails landing at once.
Set `NOTIFY_MODE=per_event` for one mail per change instead.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from marketbot.assets.emails import (
    daily_digest_template,
    portfolio_event_template,
    risk_alert_template,
)

_log = logging.getLogger(__name__)


class NotifierService:
    def __init__(self, ctx):
        self._ctx = ctx

    @property
    def mail(self):
        return self._ctx.mail

    @property
    def config(self):
        return self._ctx.config.mail

    def recipient(self, portfolio) -> str:
        return portfolio.notify_email or self.config.notify_email

    # ----------------------------------------------------------------- #
    # Scan results
    # ----------------------------------------------------------------- #

    def notify_run(self, portfolio, run, summary: Dict[str, Any]) -> bool:
        opened = summary.get('opened') or []
        closed = summary.get('closed') or []
        stop_moves = summary.get('stop_moves') or []

        if not opened and not closed:
            _log.info('Nothing added or removed this run; no mail sent')
            return False

        to = self.recipient(portfolio)
        if self.config.notify_mode == 'per_event':
            sent = False
            for item in opened:
                sent |= self._send_single(portfolio, run, summary, added=item)
            for item in closed:
                sent |= self._send_single(portfolio, run, summary, removed=item)
            return sent

        subject = self._subject(portfolio, opened, closed)
        html = portfolio_event_template().render(
            subject=subject,
            portfolio=portfolio,
            run=run,
            regime=summary.get('regime', ''),
            equity=summary.get('equity', 0.0),
            cash=summary.get('cash', 0.0),
            total_return=summary.get('total_return', 0.0),
            open_positions=summary.get('open_positions', 0),
            opened=opened,
            closed=closed,
            stop_moves=stop_moves,
            advice=summary.get('advice') or [],
            advisor_model=summary.get('advisor_model', ''),
            advisor_mode=summary.get('advisor_mode', 'off'),
        )
        return self.mail.send(to, subject, html)

    def _send_single(
        self,
        portfolio,
        run,
        summary: Dict[str, Any],
        added: Optional[dict] = None,
        removed: Optional[dict] = None,
    ) -> bool:
        item = added or removed
        if not item:
            return False
        verb = 'Added' if added else 'Removed'
        subject = f'[{portfolio.name}] {verb} {item["symbol"]}'
        html = portfolio_event_template().render(
            subject=subject,
            portfolio=portfolio,
            run=run,
            regime=summary.get('regime', ''),
            equity=summary.get('equity', 0.0),
            cash=summary.get('cash', 0.0),
            total_return=summary.get('total_return', 0.0),
            open_positions=summary.get('open_positions', 0),
            opened=[added] if added else [],
            closed=[removed] if removed else [],
            stop_moves=[],
            advice=[],
            advisor_model=summary.get('advisor_model', ''),
            advisor_mode=summary.get('advisor_mode', 'off'),
        )
        return self.mail.send(self.recipient(portfolio), subject, html)

    def _subject(self, portfolio, opened: List[dict], closed: List[dict]) -> str:
        parts = []
        if opened:
            parts.append('+' + ', '.join(i['symbol'] for i in opened[:3]))
        if closed:
            parts.append('-' + ', '.join(i['symbol'] for i in closed[:3]))
        detail = ' '.join(parts) or 'no change'
        extra = len(opened) + len(closed) - 6
        if extra > 0:
            detail += f' +{extra} more'
        return f'[{portfolio.name}] {detail}'

    # ----------------------------------------------------------------- #
    # Digest and alerts
    # ----------------------------------------------------------------- #

    def notify_digest(self, portfolio, summary: Dict[str, Any]) -> bool:
        subject = (
            f'[{portfolio.name}] Daily digest — '
            f'{summary.get("total_return", 0.0):+.2f}% since inception'
        )
        html = daily_digest_template().render(
            subject=subject,
            portfolio=portfolio,
            as_of=summary.get('as_of', _now_text()),
            regime=summary.get('regime', ''),
            equity=summary.get('equity', 0.0),
            cash=summary.get('cash', 0.0),
            total_return=summary.get('total_return', 0.0),
            day_return=summary.get('day_return', 0.0),
            realized_pnl=summary.get('realized_pnl', 0.0),
            unrealized_pnl=summary.get('unrealized_pnl', 0.0),
            drawdown_pct=summary.get('drawdown_pct', 0.0),
            positions=summary.get('positions') or [],
        )
        return self.mail.send(self.recipient(portfolio), subject, html)

    def notify_risk(
        self, portfolio, title: str, message: str, summary: Dict[str, Any]
    ) -> bool:
        subject = f'[{portfolio.name}] {title}'
        html = risk_alert_template().render(
            subject=subject,
            portfolio=portfolio,
            title=title,
            message=message,
            as_of=summary.get('as_of', _now_text()),
            equity=summary.get('equity', 0.0),
            cash=summary.get('cash', 0.0),
            total_return=summary.get('total_return', 0.0),
        )
        return self.mail.send(self.recipient(portfolio), subject, html)


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')
