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
