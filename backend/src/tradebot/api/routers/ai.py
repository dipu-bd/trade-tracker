from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from tradebot.ai.chat import AnalystChat
from tradebot.ai.client import AIClient
from tradebot.ai.deliberation import STRATEGIES
from tradebot.ai.pipeline import QUALITY_PROFILES, tiers_for
from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.broker.service import load_portfolio
from tradebot.core.errors import NotFoundError, ValidationError
from tradebot.db.models import AICall, DecisionRun, Lesson, Portfolio
from tradebot.schemas.ai import (
    AICallDetail,
    AICallOut,
    AISpend,
    ChatReply,
    ChatRequest,
    CycleTimeline,
    GuardrailRow,
    LessonOut,
    ModelSettings,
    ModelSummary,
)

router = APIRouter(prefix="/portfolios", tags=["ai"])

TIERS = ("quick", "quick_fallback", "deep", "deep_fallback")


@router.get("/{portfolio_id}/ai/calls", response_model=list[AICallOut])
async def list_ai_calls(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    stage: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AICallOut]:
    """Every model call, newest first. The index for the "why did it buy this?" screen."""
    await load_portfolio(session, portfolio_id, user.id)
    stmt = select(AICall).where(AICall.portfolio_id == portfolio_id)
    if stage:
        stmt = stmt.where(AICall.stage == stage)

    rows = await session.scalars(stmt.order_by(AICall.id.desc()).limit(limit))
    return [AICallOut.model_validate(row) for row in rows]


@router.get("/{portfolio_id}/ai/calls/{call_id}", response_model=AICallDetail)
async def get_ai_call(
    portfolio_id: int, call_id: int, user: CurrentUser, session: DbSession
) -> AICallDetail:
    """One call with its full prompt and raw response, exactly as sent and received."""
    await load_portfolio(session, portfolio_id, user.id)
    call = await session.scalar(
        select(AICall).where(AICall.id == call_id, AICall.portfolio_id == portfolio_id)
    )
    if call is None:
        raise NotFoundError("ai call not found")
    return AICallDetail.model_validate(call)


@router.get("/{portfolio_id}/ai/spend", response_model=AISpend)
async def get_ai_spend(portfolio_id: int, user: CurrentUser, session: DbSession) -> AISpend:
    """Cost and token totals. Information for the dashboard, never a gate on a decision."""
    await load_portfolio(session, portfolio_id, user.id)
    row = (
        await session.execute(
            select(
                func.count(AICall.id),
                func.coalesce(func.sum(AICall.cost_usd), 0),
                func.coalesce(func.sum(AICall.prompt_tokens), 0),
                func.coalesce(func.sum(AICall.completion_tokens), 0),
                func.coalesce(func.sum(AICall.cached_tokens), 0),
            ).where(AICall.portfolio_id == portfolio_id)
        )
    ).one()

    return AISpend(
        calls=row[0],
        cost_usd=row[1],
        prompt_tokens=row[2],
        completion_tokens=row[3],
        cached_tokens=row[4],
    )


@router.get("/{portfolio_id}/cycles/{run_id}/timeline", response_model=CycleTimeline)
async def get_cycle_timeline(
    portfolio_id: int, run_id: int, user: CurrentUser, session: DbSession
) -> CycleTimeline:
    """One decision cycle end to end: what the model asked for, what was clamped, what traded.

    This is the screen that makes the guardrail diff a product feature rather than a claim.
    """
    await load_portfolio(session, portfolio_id, user.id)
    run = await session.scalar(
        select(DecisionRun).where(
            DecisionRun.id == run_id, DecisionRun.portfolio_id == portfolio_id
        )
    )
    if run is None:
        raise NotFoundError("decision run not found")

    calls = await session.scalars(
        select(AICall).where(AICall.decision_run_id == run.id).order_by(AICall.id)
    )
    detail: dict[str, Any] = run.detail or {}
    ai: dict[str, Any] = detail.get("ai") or {}

    return CycleTimeline(
        run_id=run.id,
        correlation_id=run.correlation_id,
        as_of=run.as_of,
        status=run.status,
        regime=run.regime,
        exposure=run.exposure,
        ai_enabled=bool(ai.get("enabled")),
        ai_used=bool(ai.get("used")),
        ai_reason=str(ai.get("reason") or ""),
        strategy=str(ai.get("strategy") or ""),
        rounds=int(ai.get("rounds") or 0),
        brief_hash=str(ai.get("brief_hash") or ""),
        confidence=ai.get("confidence") or {},
        guardrail=[GuardrailRow(**row) for row in (ai.get("guardrail_diff") or [])],
        analysts_skipped=ai.get("analysts_skipped") or {},
        entries=detail.get("entries") or [],
        exits=detail.get("exits") or [],
        screened_out=detail.get("screened_out") or {},
        skipped=detail.get("skipped") or {},
        calls=[AICallOut.model_validate(row) for row in calls],
    )


@router.get("/{portfolio_id}/ai/lessons", response_model=list[LessonOut])
async def list_lessons(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[LessonOut]:
    """Reflection memory: what the system learned from its own closed trades."""
    await load_portfolio(session, portfolio_id, user.id)
    rows = await session.scalars(
        select(Lesson)
        .where(Lesson.portfolio_id == portfolio_id)
        .order_by(Lesson.closed_at.desc())
        .limit(limit)
    )
    return [LessonOut.model_validate(row) for row in rows]


@router.get("/{portfolio_id}/ai/models", response_model=ModelSummary)
async def get_models(
    portfolio_id: int, user: CurrentUser, context: Context, session: DbSession
) -> ModelSummary:
    """The two-tier model configuration. Never includes an API key — only which one to use."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    stored = dict(portfolio.models or {})
    settings = ModelSettings(
        **{name: stored.get(name) for name in TIERS},
        ai_enabled=portfolio.ai_enabled,
        quality=portfolio.quality,
        deliberation=portfolio.deliberation,
    )

    available = set(await context.providers.llm_keys(session, user.id))
    wanted = {
        endpoint.credential
        for endpoint in (getattr(settings, name) for name in TIERS)
        if endpoint is not None
    }
    return ModelSummary(
        **settings.model_dump(),
        configured=bool(stored),
        missing_credentials=sorted(wanted - available),
    )


@router.put("/{portfolio_id}/ai/models", response_model=ModelSummary)
async def update_models(
    portfolio_id: int,
    body: ModelSettings,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> ModelSummary:
    """Replace the model configuration.

    Rejected rather than silently stored when a named credential is absent, because an endpoint
    with no key fails at the next cycle instead of at the moment the mistake was made.
    """
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    if body.quality not in QUALITY_PROFILES:
        raise ValidationError(f"unknown quality: {body.quality}")
    if body.deliberation not in STRATEGIES:
        raise ValidationError(f"unknown deliberation strategy: {body.deliberation}")
    if body.ai_enabled and body.deep is None:
        raise ValidationError("a deep model is required to enable the AI")

    available = set(await context.providers.llm_keys(session, user.id))
    for name in TIERS:
        endpoint = getattr(body, name)
        if endpoint is not None and endpoint.credential not in available:
            raise ValidationError(
                f"no stored credential named {endpoint.credential!r} for the {name} endpoint"
            )

    portfolio.models = {
        name: endpoint.model_dump()
        for name in TIERS
        if (endpoint := getattr(body, name)) is not None
    }
    portfolio.ai_enabled = body.ai_enabled
    portfolio.quality = body.quality
    portfolio.deliberation = body.deliberation
    await session.flush()

    return await get_models(portfolio_id, user, context, session)


@router.get("/{portfolio_id}/ai/summary", response_model=dict[str, Any])
async def get_ai_summary(
    portfolio_id: int, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """Whether the AI layer is on, and how often its judgement is being clamped."""
    portfolio: Portfolio = await load_portfolio(session, portfolio_id, user.id)
    runs = list(
        await session.scalars(
            select(DecisionRun)
            .where(DecisionRun.portfolio_id == portfolio_id)
            .order_by(DecisionRun.id.desc())
            .limit(50)
        )
    )

    used = sum(1 for run in runs if (run.detail or {}).get("ai", {}).get("used"))
    clamps = sum(len((run.detail or {}).get("ai", {}).get("guardrail_diff") or []) for run in runs)

    return {
        "ai_enabled": portfolio.ai_enabled,
        "quality": portfolio.quality,
        "deliberation": portfolio.deliberation,
        "configured": bool(portfolio.models),
        "cycles_sampled": len(runs),
        "cycles_with_ai": used,
        "guardrail_clamps": clamps,
    }


@router.post("/{portfolio_id}/chat", response_model=ChatReply)
async def analyst_chat(
    portfolio_id: int,
    body: ChatRequest,
    request: Request,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> ChatReply:
    """Ask about this portfolio. Read-only: there is no path from here to the broker."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)

    keys = await context.providers.llm_keys(session, user.id)
    quick, deep = tiers_for(portfolio, keys)
    tier = quick or deep
    if tier is None:
        raise ValidationError("no model configured for this portfolio")

    client = getattr(request.app.state, "ai_client", None) or AIClient(clock=context.clock)
    reply, sources, model, cost = await AnalystChat(client, tier).ask(
        session,
        portfolio,
        body.message,
        [(item.role, item.content) for item in body.history],
    )
    return ChatReply(reply=reply, grounded_on=sources, model=model, cost_usd=cost)
