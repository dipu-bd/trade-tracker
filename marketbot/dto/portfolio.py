from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PortfolioCreate(BaseModel):
    """Everything the owner decides when the portfolio is opened.

    Only `name` and `initial_capital` are required; the rest fall back to the
    server defaults so a minimal request still produces a sane risk profile.
    """

    name: str = Field(min_length=1, max_length=120)
    initial_capital: float = Field(gt=0)
    base_currency: str = 'USD'
    notify_email: Optional[str] = None

    # Risk
    risk_pct_per_trade: Optional[float] = Field(default=None, gt=0, le=10)
    max_positions: Optional[int] = Field(default=None, ge=1, le=50)
    max_position_pct: Optional[float] = Field(default=None, gt=0, le=100)
    max_per_sector: Optional[int] = Field(default=None, ge=1, le=20)
    daily_loss_pct: Optional[float] = Field(default=None, ge=0, le=100)

    # Stop / target geometry
    atr_stop_mult: Optional[float] = Field(default=None, gt=0, le=10)
    etf_atr_stop_mult: Optional[float] = Field(default=None, gt=0, le=10)
    crypto_atr_stop_mult: Optional[float] = Field(default=None, gt=0, le=10)
    take_profit_r: Optional[float] = Field(default=None, ge=0, le=20)
    max_hold_days: Optional[int] = Field(default=None, ge=1, le=365)
    etf_max_hold_days: Optional[int] = Field(default=None, ge=1, le=365)
    crypto_max_hold_days: Optional[int] = Field(default=None, ge=1, le=365)

    # Sleeves
    crypto_max_pct: Optional[float] = Field(default=None, ge=0, le=100)
    enable_stocks: Optional[bool] = None
    enable_etfs: Optional[bool] = None
    enable_crypto: Optional[bool] = None

    # Thresholds
    entry_score: Optional[float] = Field(default=None, ge=0, le=100)
    etf_entry_score: Optional[float] = Field(default=None, ge=0, le=100)
    exit_score: Optional[float] = Field(default=None, ge=0, le=100)
    rotation_edge: Optional[float] = Field(default=None, ge=0, le=100)

    # Paper execution costs
    slippage_bps: Optional[float] = Field(default=None, ge=0, le=500)
    commission_bps: Optional[float] = Field(default=None, ge=0, le=500)
    crypto_commission_bps: Optional[float] = Field(default=None, ge=0, le=500)


class PortfolioUpdate(BaseModel):
    is_active: Optional[bool] = None
    notify_email: Optional[str] = None
    risk_pct_per_trade: Optional[float] = Field(default=None, gt=0, le=10)
    max_positions: Optional[int] = Field(default=None, ge=1, le=50)
    max_position_pct: Optional[float] = Field(default=None, gt=0, le=100)
    max_per_sector: Optional[int] = Field(default=None, ge=1, le=20)
    daily_loss_pct: Optional[float] = Field(default=None, ge=0, le=100)
    take_profit_r: Optional[float] = Field(default=None, ge=0, le=20)
    crypto_max_pct: Optional[float] = Field(default=None, ge=0, le=100)
    enable_stocks: Optional[bool] = None
    enable_etfs: Optional[bool] = None
    enable_crypto: Optional[bool] = None
    entry_score: Optional[float] = Field(default=None, ge=0, le=100)
    exit_score: Optional[float] = Field(default=None, ge=0, le=100)
    rotation_edge: Optional[float] = Field(default=None, ge=0, le=100)


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    asset_class: str
    status: str
    qty: float
    avg_entry: float
    stop_price: float
    target_price: float
    entry_score: float
    entry_at: datetime
    exit_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_pnl: float = 0.0
    last_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    r_multiple: Optional[float] = None


class TradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    side: str
    qty: float
    price: float
    gross: float
    fees: float
    reason: str
    executed_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    payload: dict
    created_at: datetime


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snap_date: date
    equity: float
    cash: float
    positions_value: float
    realized_pnl: float
    unrealized_pnl: float
    open_positions: int
    drawdown_pct: float


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_currency: str
    is_active: bool
    created_at: datetime
    initial_capital: float
    cash: float
    risk_pct_per_trade: float
    max_positions: int
    max_position_pct: float
    daily_loss_pct: float
    crypto_max_pct: float
    enable_stocks: bool
    enable_etfs: bool
    enable_crypto: bool
    entry_score: float
    exit_score: float
    take_profit_r: float
    notify_email: Optional[str] = None


class PortfolioDetail(PortfolioOut):
    equity: float = 0.0
    positions_value: float = 0.0
    total_return_pct: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    regime: str = ''
    positions: List[PositionOut] = []
