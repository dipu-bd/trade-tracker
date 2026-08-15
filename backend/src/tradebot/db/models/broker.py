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

MONEY = Numeric(28, 10)
PRICE = Numeric(20, 8)
QUANTITY = Numeric(28, 10)


class Side:
    BUY = "BUY"
    SELL = "SELL"


class OrderType:
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce:
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


class OrderStatus:
    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"

    TERMINAL = frozenset({FILLED, CANCELED, EXPIRED, REJECTED})
    OPEN = frozenset({ACCEPTED, PARTIALLY_FILLED})


class EntryType:
    DEPOSIT = "DEPOSIT"
    BUY = "BUY"
    SELL = "SELL"
    FEE = "FEE"
    DIVIDEND = "DIVIDEND"
    ADJUSTMENT = "ADJUSTMENT"


class PositionStatus:
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_portfolio_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    base_currency: Mapped[str] = mapped_column(String(8), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    initial_capital: Mapped[Decimal] = mapped_column(MONEY)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(10))
    commission_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(0))
    min_commission: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    allow_fractional: Mapped[bool] = mapped_column(Boolean, default=False)

    benchmark: Mapped[str] = mapped_column(String(32), default="SPY")
    cadence: Mapped[str] = mapped_column(String(32), default="daily")
    autopilot: Mapped[bool] = mapped_column(Boolean, default=False)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    universe: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    orders: Mapped[list["Order"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class LedgerEntry(Base):
    """Append-only. Never updated, never deleted — cash is a replay of this table."""

    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_portfolio_seq", "portfolio_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    entry_type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    balance_after: Mapped[Decimal] = mapped_column(MONEY)
    ref_type: Mapped[str | None] = mapped_column(String(16), default=None)
    ref_id: Mapped[int | None] = mapped_column(Integer, default=None)
    memo: Mapped[str | None] = mapped_column(String(200), default=None)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_order_client_id"),
        Index("ix_orders_portfolio_status", "portfolio_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    client_order_id: Mapped[str] = mapped_column(String(64))

    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(12))
    time_in_force: Mapped[str] = mapped_column(String(4), default=TimeInForce.DAY)
    qty: Mapped[Decimal] = mapped_column(QUANTITY)
    limit_price: Mapped[Decimal | None] = mapped_column(PRICE, default=None)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE, default=None)

    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.NEW, index=True)
    filled_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal(0))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(PRICE, default=None)
    reserved_cash: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))

    submitted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    reject_reason: Mapped[str | None] = mapped_column(String(200), default=None)

    portfolio: Mapped[Portfolio] = relationship(back_populates="orders")
    fills: Mapped[list["Fill"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    @property
    def remaining_qty(self) -> Decimal:
        return self.qty - self.filled_qty

    @property
    def is_open(self) -> bool:
        return self.status in OrderStatus.OPEN


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("order_id", "seq", name="uq_fill_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)

    qty: Mapped[Decimal] = mapped_column(QUANTITY)
    price: Mapped[Decimal] = mapped_column(PRICE)
    fee: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    slippage_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    executed_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)

    order: Mapped[Order] = relationship(back_populates="fills")


class Position(Base, TimestampMixin):
    """A projection of its lots. Quantity and cost are derived, never authoritative."""

    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_portfolio_status", "portfolio_id", "status"),
        UniqueConstraint("portfolio_id", "instrument_id", "status", name="uq_open_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(8), default=PositionStatus.OPEN)

    qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal(0))
    avg_cost: Mapped[Decimal] = mapped_column(PRICE, default=Decimal(0))
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    fees_paid: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))

    opened_at: Mapped[datetime] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    stop_price: Mapped[Decimal | None] = mapped_column(PRICE, default=None)
    highest_close: Mapped[Decimal | None] = mapped_column(PRICE, default=None)
    laddered: Mapped[bool] = mapped_column(Boolean, default=False)

    lots: Mapped[list["Lot"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )


class Lot(Base):
    """One purchase, consumed oldest-first on a sale.

    FIFO is what keeps a scale-in followed by a partial exit honest.
    """

    __tablename__ = "lots"
    __table_args__ = (Index("ix_lots_position_opened", "position_id", "opened_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    fill_id: Mapped[int | None] = mapped_column(
        ForeignKey("fills.id", ondelete="SET NULL"), default=None
    )

    qty_original: Mapped[Decimal] = mapped_column(QUANTITY)
    qty_open: Mapped[Decimal] = mapped_column(QUANTITY)
    cost_basis: Mapped[Decimal] = mapped_column(PRICE)
    fee_paid: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)

    position: Mapped[Position] = relationship(back_populates="lots")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (UniqueConstraint("portfolio_id", "snap_date", name="uq_snapshot_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    snap_date: Mapped[date] = mapped_column(Date, index=True)

    equity: Mapped[Decimal] = mapped_column(MONEY)
    cash: Mapped[Decimal] = mapped_column(MONEY)
    positions_value: Mapped[Decimal] = mapped_column(MONEY)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(0))


class DecisionRun(Base):
    """One decision cycle, kept so "why did it buy this?" has an answer."""

    __tablename__ = "decision_runs"
    __table_args__ = (Index("ix_decision_runs_portfolio_started", "portfolio_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    trigger: Mapped[str] = mapped_column(String(24), default="scheduled")

    started_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    as_of: Mapped[date] = mapped_column(Date)

    status: Mapped[str] = mapped_column(String(16), default="running")
    regime: Mapped[str] = mapped_column(String(16), default="")
    exposure: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(0))

    candidates: Mapped[int] = mapped_column(Integer, default=0)
    entries: Mapped[int] = mapped_column(Integer, default=0)
    exits: Mapped[int] = mapped_column(Integer, default=0)
    orders_placed: Mapped[int] = mapped_column(Integer, default=0)

    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(500), default=None)
