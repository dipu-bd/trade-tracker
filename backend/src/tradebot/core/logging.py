import logging
import re
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "secret",
        "secret_key",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "set_cookie",
        "ciphertext",
        "data_key",
        "private_key",
        "credential",
        "credentials",
    }
)

REDACTED = "[redacted]"

_BEARER = re.compile(r"(?i)\bbearer\s+[\w.\-]+")
_SK_STYLE = re.compile(r"\b(sk|pk|rk)[-_][A-Za-z0-9\-_]{8,}")


def _scrub_text(value: str) -> str:
    value = _BEARER.sub("Bearer " + REDACTED, value)
    return _SK_STYLE.sub(REDACTED, value)


def _scrub(value: Any, key: str | None = None) -> Any:
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, dict):
        return {k: _scrub(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(v) for v in value)
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    return {k: _scrub(v, k) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))

    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
