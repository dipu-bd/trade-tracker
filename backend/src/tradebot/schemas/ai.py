from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class AICallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    decision_run_id: int | None
    correlation_id: str
    stage: str
    model: str
    endpoint: str
    rung: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    latency_ms: int
    cost_usd: Decimal
    brief_hash: str
    error: str | None
    created_at: datetime


class AICallDetail(AICallOut):
    """The full prompt and raw response. Stored verbatim so a decision can be re-read."""

    system_prompt: str
    user_prompt: str
    response: str
    attempts: dict[str, Any]


class GuardrailRow(BaseModel):
    symbol: str
    reason: str
    asked: str
    applied: str


class CycleTimeline(BaseModel):
    run_id: int
    correlation_id: str
    as_of: date
    status: str
    regime: str
    exposure: Decimal

    ai_enabled: bool
    ai_used: bool
    ai_reason: str
    strategy: str
    rounds: int
    brief_hash: str

    confidence: dict[str, float]
    guardrail: list[GuardrailRow]
    analysts_skipped: dict[str, str]

    entries: list[dict[str, Any]]
    exits: list[dict[str, Any]]
    screened_out: dict[str, str]
    skipped: dict[str, str]
    calls: list[AICallOut]


class AISpend(BaseModel):
    calls: int
    cost_usd: Decimal
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int


class LessonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    closed_at: datetime
    holding_days: int
    realized_return: Decimal
    benchmark_return: Decimal
    alpha: Decimal
    text: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class ChatReply(BaseModel):
    reply: str
    grounded_on: list[str]
    model: str
    cost_usd: Decimal
