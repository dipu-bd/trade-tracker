from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db.base import Base, UtcDateTime, utcnow


class Severity:
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_created", "user_id", "created_at"),
        Index("ix_events_correlation", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), default=None, index=True
    )
    portfolio_id: Mapped[int | None] = mapped_column(default=None, index=True)

    domain: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(8), default=Severity.INFO)
    correlation_id: Mapped[str | None] = mapped_column(String(36), default=None)
    message: Mapped[str | None] = mapped_column(String(500), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
