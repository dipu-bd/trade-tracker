from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tradebot.db.base import Base, TimestampMixin, UtcDateTime

PRICE = Numeric(20, 8)
QUANTITY = Numeric(28, 10)


class Instrument(Base, TimestampMixin):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("symbol", "asset_class", name="uq_instrument_symbol_class"),
        Index("ix_instruments_active", "asset_class", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    exchange: Mapped[str] = mapped_column(String(32), default="")
    sector: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    first_bar_date: Mapped[date | None] = mapped_column(Date, default=None)
    last_bar_date: Mapped[date | None] = mapped_column(Date, default=None)
    last_quote_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    last_quote_price: Mapped[Decimal | None] = mapped_column(PRICE, default=None)
    last_quote_source: Mapped[str | None] = mapped_column(String(32), default=None)

    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    bars: Mapped[list["PriceBar"]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan"
    )


class PriceBar(Base):
    """A daily OHLCV bar, stored split- and dividend-adjusted."""

    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint("instrument_id", "bar_date", name="uq_bar_instrument_date"),
        Index("ix_price_bars_instrument_date", "instrument_id", "bar_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    bar_date: Mapped[date] = mapped_column(Date, index=True)

    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[Decimal] = mapped_column(QUANTITY)

    source: Mapped[str] = mapped_column(String(32), default="")
    adjusted: Mapped[bool] = mapped_column(Boolean, default=False)

    instrument: Mapped[Instrument] = relationship(back_populates="bars")


class CorporateActionRecord(Base, TimestampMixin):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("instrument_id", "effective_date", "kind", name="uq_corporate_action"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16))

    split_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)
    cash_amount: Mapped[Decimal | None] = mapped_column(PRICE, default=None)

    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    source: Mapped[str] = mapped_column(String(32), default="")


class ProviderUsage(Base):
    """Daily request counter per provider, so budgets survive a restart."""

    __tablename__ = "provider_usage"
    __table_args__ = (UniqueConstraint("provider_key", "usage_date", name="uq_provider_usage_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(32), index=True)
    usage_date: Mapped[date] = mapped_column(Date, index=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    rate_limited: Mapped[int] = mapped_column(Integer, default=0)
