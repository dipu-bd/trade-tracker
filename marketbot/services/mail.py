"""SMTP delivery.

Follows the same shape as the mail service in lightnovel-crawler: one cached
connection, probed with NOOP and transparently reconnected when the server has
dropped it, with sends serialised behind a lock.

The one addition here is `SMTP_TLS_VERIFY`. ProtonMail Bridge listens on
127.0.0.1:1025 and presents a self-signed certificate, so STARTTLS against it
needs a context that does not verify — hence the flag, defaulting off.
"""

import logging
import ssl
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from functools import cached_property
from smtplib import SMTP, SMTPServerDisconnected
from threading import Lock
from typing import Optional

_log = logging.getLogger(__name__)

MAX_SUBJECT_CHARS = 200


def _header_safe(value: str, max_length: int = MAX_SUBJECT_CHARS) -> str:
    """Strip CR/LF and control characters so header assembly cannot break."""
    cleaned = ''.join(
        c for c in value if c == '\t' or (c >= ' ' and c != '\x7f')
    )
    return cleaned.strip()[:max_length]


class MailService:
    def __init__(self, ctx):
        self._ctx = ctx
        self._lock = Lock()

    @property
    def config(self):
        return self._ctx.config.mail

    @property
    def enabled(self) -> bool:
        return self.config.smtp_enabled

    @property
    def sender(self) -> str:
        return self.config.smtp_sender or self.config.smtp_username

    # ----------------------------------------------------------------- #
    # Connection
    # ----------------------------------------------------------------- #

    @cached_property
    def server(self) -> SMTP:
        cfg = self.config
        if not cfg.smtp_enabled:
            raise RuntimeError('SMTP is disabled')

        _log.info(f'Connecting to SMTP server {cfg.smtp_server}:{cfg.smtp_port}')
        server = SMTP(cfg.smtp_server, cfg.smtp_port, timeout=30)
        try:
            if cfg.smtp_starttls:
                server.starttls(context=self._tls_context())
            if cfg.smtp_username:
                server.login(cfg.smtp_username, cfg.smtp_password)
            _log.info(f'SMTP connected: {cfg.smtp_server}')
            return server
        except Exception:
            server.close()
            raise

    def _tls_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if not self.config.smtp_tls_verify:
            # Proton Bridge and other local relays use self-signed certs.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _drop_connection(self) -> None:
        server = self.__dict__.pop('server', None)
        if server is not None:
            try:
                server.close()
            except Exception:  # noqa: BLE001
                pass

    def _ensure_connection(self) -> SMTP:
        server = self.server
        try:
            status, _ = server.noop()
        except Exception:  # noqa: BLE001
            status = -1
        if status != 250:
            _log.info('SMTP connection went stale, reconnecting')
            self._drop_connection()
            server = self.server
        return server

    def close(self) -> None:
        self._drop_connection()

    # ----------------------------------------------------------------- #
    # Sending
    # ----------------------------------------------------------------- #

    def send(self, to: str, subject: str, html_body: str) -> bool:
        """Deliver one HTML mail. Returns False instead of raising."""
        if not to:
            _log.warning(f'No recipient for mail {subject!r}; skipping')
            return False

        if not self.enabled:
            _log.info(f'SMTP disabled — would have sent {subject!r} to {to}')
            _log.debug(html_body)
            return False

        message = MIMEText(html_body, 'html', 'utf-8')
        message['Subject'] = _header_safe(subject)
        message['From'] = self.sender
        message['To'] = to
        message['Date'] = formatdate(localtime=True)
        message['Message-ID'] = make_msgid(domain='marketbot')

        try:
            with self._lock:
                server = self._ensure_connection()
                try:
                    server.sendmail(self.sender, [to], message.as_string())
                except SMTPServerDisconnected:
                    self._drop_connection()
                    self.server.sendmail(self.sender, [to], message.as_string())
            _log.info(f'Sent mail {subject!r} to {to}')
            return True
        except Exception as e:  # noqa: BLE001 — mail must never fail a scan
            _log.error(f'Failed to send mail {subject!r} to {to}: {e}')
            return False
