from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Query

from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.backtest.service import BacktestService
from tradebot.broker.service import load_portfolio

router = APIRouter(prefix="/portfolios", tags=["backtest"])

DEFAULT_WINDOW_DAYS = 365


@router.post("/{portfolio_id}/backtest", response_model=dict[str, Any])
async def run_backtest(
    portfolio_id: int,
    user: CurrentUser,
    context: Context,
    session: DbSession,
    start: date | None = None,
    end: date | None = None,
    control: bool = Query(default=True),
) -> dict[str, Any]:
    """Replay the strategy and answer whether it beat holding an index fund.

    The verdict is derived from the numbers, so a result that loses to the benchmark or fails
    deflation says so as the headline rather than as a footnote.
    """
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    finish = end or context.clock.now().date()
    begin = start or finish - timedelta(days=DEFAULT_WINDOW_DAYS)

    service = BacktestService(context.events)
    report = await service.run(session, portfolio, begin, finish, with_control=control)
    return report.as_dict()


@router.post("/{portfolio_id}/backtest/ablation", response_model=dict[str, Any])
async def run_ablation(
    portfolio_id: int,
    user: CurrentUser,
    context: Context,
    session: DbSession,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Compare the arms on identical windows with identical trial accounting.

    Currently rules-only is the only arm with no model configured; the AI arms appear once a
    portfolio has model endpoints, since each needs a live provider to answer.
    """
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    finish = end or context.clock.now().date()
    begin = start or finish - timedelta(days=DEFAULT_WINDOW_DAYS)

    service = BacktestService(context.events)
    report = await service.ablate(session, portfolio, begin, finish)
    return report.as_dict()


@router.post("/{portfolio_id}/backtest/leakage", response_model=dict[str, Any])
async def run_leakage_check(
    portfolio_id: int,
    cutoff: date,
    user: CurrentUser,
    context: Context,
    session: DbSession,
    span_days: int = Query(default=180, ge=30, le=1000),
) -> dict[str, Any]:
    """Run the strategy either side of a model's training cutoff and report the gap."""
    portfolio = await load_portfolio(session, portfolio_id, user.id)
    service = BacktestService(context.events)
    return await service.leakage_check(session, portfolio, cutoff, span_days)
