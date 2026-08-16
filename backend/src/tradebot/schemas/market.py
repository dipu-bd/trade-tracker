from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    asset_class: str
    name: str
    exchange: str
    sector: str
    currency: str
    is_active: bool
    first_bar_date: date | None
    last_bar_date: date | None
    last_quote_at: datetime | None
    last_quote_price: Decimal | None
    last_quote_source: str | None
    staleness_seconds: float | None = None


class BarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bar_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class QuoteOut(BaseModel):
    symbol: str
    price: Decimal
    at: datetime
    previous_close: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: Decimal | None = None


class ProviderStatusOut(BaseModel):
    """What the provider-health dashboard renders. Never contains a credential value."""

    provider: str
    label: str
    keyless: bool
    configured: bool
    available: bool
    capabilities: list[str]
    asset_classes: list[str]
    missing_credentials: list[str]
    fields: list[dict[str, str]]
    health: dict[str, Any]


class SyncRequest(BaseModel):
    asset_class: str
    limit: int = 200
    refresh_bars: bool = False


class SyncResultOut(BaseModel):
    asset_class: str
    instruments: int
    bars_written: int
    skipped_fresh: int
    failed: list[str]
    gaps: dict[str, int]


class TrackRequest(BaseModel):
    """Explicit symbols to start tracking, rather than a provider's ranked listing."""

    symbols: list[str] = Field(min_length=1, max_length=50)
    asset_class: str = "stock"
