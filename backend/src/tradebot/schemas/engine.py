from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrategySettings(BaseModel):
    benchmark: str = Field(default="SPY", min_length=1, max_length=32)
    cadence: str = Field(default="daily", min_length=1, max_length=32)
    autopilot: bool = False
    strategy: dict[str, Any] = Field(default_factory=dict)
    universe: dict[str, Any] = Field(default_factory=dict)


class StrategySummary(BaseModel):
    benchmark: str
    cadence: str
    autopilot: bool
    parameter_count: int
    screen: dict[str, Any]
    sizing: dict[str, Any]
    regime: dict[str, Any]
    exits: dict[str, Any]
    turnover: dict[str, Any]
    costs: dict[str, Any]
    universe: dict[str, Any] = Field(default_factory=dict)


class DecisionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    correlation_id: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    as_of: date
    status: str
    regime: str
    exposure: Decimal
    candidates: int
    entries: int
    exits: int
    orders_placed: int
    error: str | None


class DecisionRunDetail(DecisionRunOut):
    detail: dict[str, Any]


class CycleTriggered(BaseModel):
    run_id: int
    correlation_id: str
    as_of: date
    status: str
    orders_placed: int
    entries: int
    exits: int
    regime: str
    error: str | None = None


class MatchRun(BaseModel):
    """The outcome of one matching pass, including why anything is still resting."""

    filled: int
    expired: int
    stops: int
    waiting: dict[str, str]


class ScheduledJob(BaseModel):
    id: str
    cron: str
    next_run: str | None
