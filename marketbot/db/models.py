"""SQLAlchemy models backing the portfolio builder.

Enum-like columns are stored as plain strings: SQLite has no native enum, and
keeping them as text makes the database readable with any sqlite3 client.
"""

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Enum-like string constants
# --------------------------------------------------------------------------- #

class AssetClass:
    STOCK = 'STOCK'
    ETF = 'ETF'
    CRYPTO = 'CRYPTO'

    EQUITY_CLASSES = (STOCK, ETF)
    ALL = (STOCK, ETF, CRYPTO)


class Sleeve:
    """A scan targets one sleeve; `ALL` runs both."""

    EQUITY = 'equity'
    CRYPTO = 'crypto'
    ALL = 'all'


class PositionStatus:
    OPEN = 'OPEN'
    CLOSED = 'CLOSED'


class Side:
    BUY = 'BUY'
    SELL = 'SELL'


class Regime:
    BULLISH = 'BULLISH'
    NEUTRAL = 'NEUTRAL'
    BEARISH = 'BEARISH'


class ExitReason:
    STOP_LOSS = 'stop_loss'
    TRAILING_STOP = 'trailing_stop'
    TAKE_PROFIT = 'take_profit'
    TIME_STOP = 'time_stop'
    SIGNAL_EXIT = 'signal_exit'
    REGIME_EXIT = 'regime_exit'
    ROTATION = 'rotation'
    RISK_HALT = 'risk_halt'
    ADVISOR_EXIT = 'advisor_exit'
    MANUAL = 'manual'


class EventType:
    POSITION_OPENED = 'POSITION_OPENED'
    POSITION_CLOSED = 'POSITION_CLOSED'
    STOP_MOVED = 'STOP_MOVED'
    RISK_HALT = 'RISK_HALT'
    REGIME_CHANGE = 'REGIME_CHANGE'


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

class Portfolio(Base):
    __tablename__ = 'portfolios'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    base_currency: Mapped[str] = mapped_column(String(8), default='USD')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Capital
    initial_capital: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)

    # Risk inputs — supplied when the portfolio is created
    risk_pct_per_trade: Mapped[float] = mapped_column(Float, default=1.0)
    max_positions: Mapped[int] = mapped_column(Integer, default=8)
    max_position_pct: Mapped[float] = mapped_column(Float, default=25.0)
    max_per_sector: Mapped[int] = mapped_column(Integer, default=2)
    daily_loss_pct: Mapped[float] = mapped_column(Float, default=6.0)

    # Stop / target geometry
    atr_stop_mult: Mapped[float] = mapped_column(Float, default=2.0)
    etf_atr_stop_mult: Mapped[float] = mapped_column(Float, default=3.0)
    crypto_atr_stop_mult: Mapped[float] = mapped_column(Float, default=2.5)
    take_profit_r: Mapped[float] = mapped_column(Float, default=3.0)
    max_hold_days: Mapped[int] = mapped_column(Integer, default=15)
    etf_max_hold_days: Mapped[int] = mapped_column(Integer, default=30)
    crypto_max_hold_days: Mapped[int] = mapped_column(Integer, default=10)

    # Sleeve budget
    crypto_max_pct: Mapped[float] = mapped_column(Float, default=30.0)
    enable_stocks: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_etfs: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_crypto: Mapped[bool] = mapped_column(Boolean, default=True)

    # Entry / exit thresholds
    entry_score: Mapped[float] = mapped_column(Float, default=60.0)
    etf_entry_score: Mapped[float] = mapped_column(Float, default=50.0)
    exit_score: Mapped[float] = mapped_column(Float, default=35.0)
    rotation_edge: Mapped[float] = mapped_column(Float, default=15.0)

    # Paper execution costs, in basis points
    slippage_bps: Mapped[float] = mapped_column(Float, default=10.0)
    commission_bps: Mapped[float] = mapped_column(Float, default=0.0)
    crypto_commission_bps: Mapped[float] = mapped_column(Float, default=10.0)

    notify_email: Mapped[Optional[str]] = mapped_column(String(255), default=None)

    positions: Mapped[List['Position']] = relationship(back_populates='portfolio')

    @property
    def crypto_commission_or_default(self) -> float:
        return self.crypto_commission_bps


class Instrument(Base):
    __tablename__ = 'instruments'
    __table_args__ = (UniqueConstraint('symbol', 'asset_class', name='uq_instrument'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(160), default='')
    exchange: Mapped[str] = mapped_column(String(32), default='')
    sector: Mapped[str] = mapped_column(String(64), default='')
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_bar_date: Mapped[Optional[date]] = mapped_column(Date, default=None)
    extra: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    @property
    def is_equity(self) -> bool:
        return self.asset_class in AssetClass.EQUITY_CLASSES


class PriceBar(Base):
    """Cached daily OHLCV. This is what keeps us inside the FMP free tier."""

    __tablename__ = 'price_bars'
    __table_args__ = (UniqueConstraint('instrument_id', 'bar_date', name='uq_bar'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey('instruments.id'), index=True)
    bar_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)


class Position(Base):
    __tablename__ = 'positions'

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey('portfolios.id'), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey('instruments.id'), index=True)
    status: Mapped[str] = mapped_column(String(16), default=PositionStatus.OPEN, index=True)

    qty: Mapped[float] = mapped_column(Float)
    avg_entry: Mapped[float] = mapped_column(Float)
    entry_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    entry_score: Mapped[float] = mapped_column(Float, default=0.0)

    initial_stop: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    high_water: Mapped[float] = mapped_column(Float)
    r_value: Mapped[float] = mapped_column(Float)  # per-share risk at entry
    target_price: Mapped[float] = mapped_column(Float, default=0.0)
    max_hold_days: Mapped[int] = mapped_column(Integer, default=15)
    atr_at_entry: Mapped[float] = mapped_column(Float, default=0.0)

    exit_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, default=None)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)

    portfolio: Mapped['Portfolio'] = relationship(back_populates='positions')
    instrument: Mapped['Instrument'] = relationship()

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_entry) * self.qty

    def r_multiple(self, price: float) -> float:
        """How many multiples of initial risk this position is up (or down)."""
        if self.r_value <= 0:
            return 0.0
        return (price - self.avg_entry) / self.r_value


class Trade(Base):
    """An individual simulated fill."""

    __tablename__ = 'trades'

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey('portfolios.id'), index=True)
    position_id: Mapped[Optional[int]] = mapped_column(ForeignKey('positions.id'), default=None)
    instrument_id: Mapped[int] = mapped_column(ForeignKey('instruments.id'))
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey('scan_runs.id'), default=None)

    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    gross: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(32), default='')
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    instrument: Mapped['Instrument'] = relationship()


class ScanRun(Base):
    __tablename__ = 'scan_runs'

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey('portfolios.id'), index=True)
    sleeve: Mapped[str] = mapped_column(String(16), default=Sleeve.ALL)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    regime: Mapped[str] = mapped_column(String(16), default=Regime.NEUTRAL)
    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    opened: Mapped[int] = mapped_column(Integer, default=0)
    closed: Mapped[int] = mapped_column(Integer, default=0)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(String(500), default=None)


class Signal(Base):
    """Every scored candidate, kept so the weights can be tuned against results."""

    __tablename__ = 'signals'

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('scan_runs.id'), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey('instruments.id'))
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    score: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    gap_pct: Mapped[float] = mapped_column(Float, default=0.0)
    rvol: Mapped[float] = mapped_column(Float, default=0.0)
    atr_pct: Mapped[float] = mapped_column(Float, default=0.0)
    rsi: Mapped[float] = mapped_column(Float, default=0.0)
    adx: Mapped[float] = mapped_column(Float, default=0.0)
    mom_20: Mapped[float] = mapped_column(Float, default=0.0)
    mom_60: Mapped[float] = mapped_column(Float, default=0.0)
    trend_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    decision: Mapped[str] = mapped_column(String(32), default='')
    flags: Mapped[List[str]] = mapped_column(JSON, default=list)

    instrument: Mapped['Instrument'] = relationship()


class LlmAdvice(Base):
    """One row per advisor verdict — the audit trail for the LLM stage."""

    __tablename__ = 'llm_advice'

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('scan_runs.id'), index=True)
    provider: Mapped[str] = mapped_column(String(32), default='')
    model: Mapped[str] = mapped_column(String(80), default='')
    mode: Mapped[str] = mapped_column(String(16), default='off')

    symbol: Mapped[str] = mapped_column(String(32), default='')
    proposed_action: Mapped[str] = mapped_column(String(32), default='')
    verdict: Mapped[str] = mapped_column(String(32), default='')
    reason: Mapped[str] = mapped_column(String(1000), default='')
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)

    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = 'events'

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey('portfolios.id'), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey('scan_runs.id'), default=None)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)


class PortfolioSnapshot(Base):
    __tablename__ = 'portfolio_snapshots'
    __table_args__ = (UniqueConstraint('portfolio_id', 'snap_date', name='uq_snapshot'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey('portfolios.id'), index=True)
    snap_date: Mapped[date] = mapped_column(Date, index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    positions_value: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiUsage(Base):
    """Daily request counter, so the FMP free-tier budget is never blown."""

    __tablename__ = 'api_usage'
    __table_args__ = (UniqueConstraint('provider', 'usage_date', name='uq_api_usage'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    usage_date: Mapped[date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(Integer, default=0)
