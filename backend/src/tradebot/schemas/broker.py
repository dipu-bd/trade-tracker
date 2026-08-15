from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from tradebot.db.models import OrderType, Side, TimeInForce


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    initial_capital: Decimal = Field(gt=0)
    slippage_bps: Decimal = Field(default=Decimal(10), ge=0, le=1000)
    commission_bps: Decimal = Field(default=Decimal(0), ge=0, le=1000)
    min_commission: Decimal = Field(default=Decimal(0), ge=0)
    allow_fractional: bool = False


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_currency: str
    is_active: bool
    initial_capital: Decimal
    slippage_bps: Decimal
    commission_bps: Decimal
    allow_fractional: bool
    created_at: datetime


class PortfolioDetail(PortfolioOut):
    cash: Decimal
    reserved: Decimal
    buying_power: Decimal
    equity: Decimal
    open_positions: int


class OrderCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: str = Field(pattern=f"^({Side.BUY}|{Side.SELL})$")
    qty: Decimal = Field(gt=0)
    order_type: str = Field(
        default=OrderType.MARKET,
        pattern=f"^({OrderType.MARKET}|{OrderType.LIMIT}|{OrderType.STOP}|{OrderType.STOP_LIMIT})$",
    )
    time_in_force: str = Field(
        default=TimeInForce.DAY,
        pattern=f"^({TimeInForce.DAY}|{TimeInForce.GTC}|{TimeInForce.IOC})$",
    )
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    client_order_id: str | None = Field(default=None, max_length=64)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_order_id: str
    instrument_id: int
    side: str
    order_type: str
    time_in_force: str
    qty: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    status: str
    filled_qty: Decimal
    avg_fill_price: Decimal | None
    reserved_cash: Decimal
    reject_reason: str | None
    submitted_at: datetime | None
    closed_at: datetime | None


class FillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    seq: int
    qty: Decimal
    price: Decimal
    fee: Decimal
    slippage_amount: Decimal
    executed_at: datetime


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    status: str
    qty: Decimal
    avg_cost: Decimal
    realized_pnl: Decimal
    fees_paid: Decimal
    opened_at: datetime
    closed_at: datetime | None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    entry_type: str
    amount: Decimal
    balance_after: Decimal
    ref_type: str | None
    ref_id: int | None
    memo: str | None


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snap_date: date
    equity: Decimal
    cash: Decimal
    positions_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    open_positions: int
    drawdown_pct: Decimal


class ReconciliationOut(BaseModel):
    portfolio_id: int
    ok: bool
    cash: Decimal
    problems: list[str]
