from dataclasses import asdict, fields
from typing import Any

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select

from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.broker.service import load_portfolio
from tradebot.core.errors import ConflictError, NotFoundError
from tradebot.db.models import DecisionRun
from tradebot.engine.config import SECTIONS, parameter_count, strategy_config
from tradebot.engine.presets import BY_KEY, PRESETS
from tradebot.engine.runner import EngineRunner
from tradebot.schemas.engine import (
    CycleTriggered,
    DecisionRunDetail,
    DecisionRunOut,
    ScheduledJob,
    StrategySettings,
    StrategySummary,
)
from tradebot.workers.scheduler import CADENCES, EngineScheduler

router = APIRouter(prefix="/portfolios", tags=["engine"])


def _serialisable(section: object) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for entry in fields(section):  # type: ignore[arg-type]
        value = getattr(section, entry.name)
        out[entry.name] = sorted(value) if isinstance(value, frozenset) else value
    return out


@router.get("/{portfolio_id}/strategy", response_model=StrategySummary)
async def get_strategy(portfolio_id: int, user: CurrentUser, session: DbSession) -> StrategySummary:
    """The strategy actually in force, with every default resolved."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    config = strategy_config(portfolio)

    return StrategySummary(
        benchmark=portfolio.benchmark,
        cadence=portfolio.cadence,
        autopilot=portfolio.autopilot,
        parameter_count=parameter_count(config),
        costs=asdict(config.costs),
        **{name: _serialisable(getattr(config, name)) for name in SECTIONS},
    )


@router.put("/{portfolio_id}/strategy", response_model=StrategySummary)
async def update_strategy(
    portfolio_id: int, body: StrategySettings, user: CurrentUser, session: DbSession
) -> StrategySummary:
    """Replace the strategy settings. Unknown keys inside a section are ignored, not rejected."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    if body.cadence not in {cadence.name for cadence in CADENCES}:
        raise NotFoundError(f"unknown cadence: {body.cadence}")

    portfolio.benchmark = body.benchmark.upper()
    portfolio.cadence = body.cadence
    portfolio.autopilot = body.autopilot
    portfolio.strategy = body.strategy
    portfolio.universe = body.universe
    await session.flush()

    return await get_strategy(portfolio_id, user, session)


@router.post(
    "/{portfolio_id}/cycles",
    response_model=CycleTriggered,
    status_code=status.HTTP_201_CREATED,
)
async def run_cycle(
    portfolio_id: int, request: Request, user: CurrentUser, context: Context, session: DbSession
) -> CycleTriggered:
    """Run one decision cycle now. Serialized per portfolio against the scheduled run."""
    await load_portfolio(session, portfolio_id, user.id)
    await session.commit()

    runner: EngineRunner = getattr(request.app.state, "scheduler", EngineScheduler(context)).runner
    try:
        report = await runner.run_portfolio(portfolio_id, trigger="manual")
    except RuntimeError as error:
        raise ConflictError(str(error)) from error

    decision = report.decision
    return CycleTriggered(
        run_id=report.run_id,
        correlation_id=report.correlation_id,
        as_of=report.as_of,
        status="ok" if report.ok else "failed",
        orders_placed=len(report.orders),
        entries=len(decision.entries) if decision else 0,
        exits=len(decision.exits) if decision else 0,
        regime=decision.regime.state.value if decision else "",
        error=report.error,
    )


@router.get("/{portfolio_id}/cycles", response_model=list[DecisionRunOut])
async def list_cycles(
    portfolio_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[DecisionRunOut]:
    """Decision runs newest first."""
    await load_portfolio(session, portfolio_id, user.id)
    rows = await session.scalars(
        select(DecisionRun)
        .where(DecisionRun.portfolio_id == portfolio_id)
        .order_by(DecisionRun.id.desc())
        .limit(limit)
    )
    return [DecisionRunOut.model_validate(row) for row in rows]


@router.get("/{portfolio_id}/cycles/{run_id}", response_model=DecisionRunDetail)
async def get_cycle(
    portfolio_id: int, run_id: int, user: CurrentUser, session: DbSession
) -> DecisionRunDetail:
    """One run with its full reasoning: what was screened out, skipped, entered and exited."""
    await load_portfolio(session, portfolio_id, user.id)
    run = await session.scalar(
        select(DecisionRun).where(
            DecisionRun.id == run_id, DecisionRun.portfolio_id == portfolio_id
        )
    )
    if run is None:
        raise NotFoundError("decision run not found")
    return DecisionRunDetail.model_validate(run)


@router.post("/{portfolio_id}/strategy/preset/{preset_key}", response_model=StrategySummary)
async def apply_preset(
    portfolio_id: int, preset_key: str, user: CurrentUser, session: DbSession
) -> StrategySummary:
    """Apply a preset wholesale. A starting point for the wizard, not a recommendation."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    preset = BY_KEY.get(preset_key)
    if preset is None:
        raise NotFoundError(f"unknown preset: {preset_key}")

    portfolio.benchmark = preset.benchmark
    portfolio.cadence = preset.cadence
    portfolio.strategy = preset.strategy
    portfolio.universe = preset.universe
    portfolio.quality = preset.quality
    portfolio.deliberation = preset.deliberation
    await session.flush()

    return await get_strategy(portfolio_id, user, session)


schedule_router = APIRouter(prefix="/engine", tags=["engine"])


@schedule_router.get("/presets", response_model=list[dict[str, Any]])
async def list_presets(_user: CurrentUser) -> list[dict[str, Any]]:
    """The shipped strategy templates the configuration wizard offers."""
    return [preset.as_dict() for preset in PRESETS]


@schedule_router.get("/schedule", response_model=list[ScheduledJob])
async def get_schedule(request: Request, _user: CurrentUser) -> list[ScheduledJob]:
    """The UTC cron jobs and when each next fires."""
    scheduler: EngineScheduler | None = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return []
    return [ScheduledJob(**job) for job in scheduler.jobs()]
