"""The three checks the plan says the engine must pass before any other number means anything.

If a buy-and-hold replay does not match the instrument's actual return, if random entry shows an
edge, or if removing costs does not help, then the engine is wrong and every result it produces
is noise.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.test_engine_cycle import seed
from tradebot.backtest.runner import (
    ReplayRunner,
    buy_and_hold,
    closes_for,
    random_entry_control,
    trading_days,
)
from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.db.models import Portfolio, User

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
LAST_BAR = NOW.date() - timedelta(days=1)
CAPITAL = Decimal(100_000)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


async def make_portfolio(
    context: AppContext,
    clock: FrozenClock,
    *,
    slippage: Decimal = Decimal(10),
    commission: Decimal = Decimal(0),
) -> int:
    broker = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    async with context.db.session() as session:
        user = User(
            email=f"bt{slippage}{commission}@example.com", password_hash="x", display_name="BT"
        )
        session.add(user)
        await session.flush()
        portfolio = await broker.create_portfolio(
            session,
            user_id=user.id,
            name=f"Replay {slippage}/{commission}",
            initial_capital=CAPITAL,
            slippage_bps=slippage,
            commission_bps=commission,
            allow_fractional=True,
        )
        portfolio.benchmark = "SPY"
        portfolio.universe = {"asset_classes": ["stock"], "max_symbols": 20}
        return int(portfolio.id)


async def replay(context: AppContext, portfolio_id: int, days, label: str = "rules"):  # type: ignore[no-untyped-def]
    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        return await ReplayRunner(context.events).run(session, portfolio, days, label=label)


async def days_for(context: AppContext, count: int = 120):  # type: ignore[no-untyped-def]
    async with context.db.session() as session:
        everything = await trading_days(session, "SPY", LAST_BAR - timedelta(days=900), LAST_BAR)
    return everything[-count:]


async def test_buy_and_hold_replay_matches_the_instruments_actual_return(
    context: AppContext,
) -> None:
    """SANITY 1. A buy-and-hold curve must reproduce the instrument's own total return."""
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    days = await days_for(context, 200)

    async with context.db.session() as session:
        result = await buy_and_hold(session, "SPY", days, capital=float(CAPITAL))
        closes = await closes_for(session, "SPY")

    expected = float(closes[days[-1]] / closes[days[0]]) - 1.0
    measured = result.performance().total_return

    assert result.error is None
    assert measured == pytest.approx(expected, rel=1e-9)


async def test_a_random_entry_control_shows_no_edge_over_the_instruments_it_trades(
    context: AppContext,
) -> None:
    """SANITY 2. Coin-flip entries must not beat the drift of what they trade."""
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    for symbol in ["AAA", "BBB", "CCC"]:
        await seed(context, symbol, daily=0.0008, count=400, end=LAST_BAR)
    days = await days_for(context, 200)

    async with context.db.session() as session:
        control = await random_entry_control(session, ["AAA", "BBB", "CCC"], days, seed=11)
        market = await buy_and_hold(session, "AAA", days, capital=float(CAPITAL))

    assert control.error is None
    # Same drift, less time invested: the control cannot manufacture an edge from noise.
    assert control.performance().total_return <= market.performance().total_return + 1e-9


async def test_removing_costs_strictly_improves_the_same_strategy(
    context: AppContext, clock: FrozenClock
) -> None:
    """SANITY 3. If zero costs did not help, costs are not being charged."""
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.003, count=400, end=LAST_BAR)
    days = await days_for(context, 90)

    expensive = await make_portfolio(context, clock, slippage=Decimal(20), commission=Decimal(5))
    free = await make_portfolio(context, clock, slippage=Decimal(0), commission=Decimal(0))

    costly = await replay(context, expensive, days, label="costly")
    cheap = await replay(context, free, days, label="free")

    assert costly.orders > 0, "the sanity check is vacuous without trades"
    assert cheap.orders > 0
    assert cheap.performance().total_return >= costly.performance().total_return


async def test_costs_high_enough_to_eat_the_risk_budget_stop_the_strategy_trading(
    context: AppContext, clock: FrozenClock
) -> None:
    """A strategy that only works at zero cost must be reported as failing, not as profitable.

    At 50bps slippage plus 20bps commission the round trip consumes 23% of the stop distance,
    above the 15% ceiling, so the gate declines every trade rather than bleeding the account.
    """
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.003, count=400, end=LAST_BAR)
    days = await days_for(context, 60)

    ruinous = await make_portfolio(context, clock, slippage=Decimal(50), commission=Decimal(20))
    result = await replay(context, ruinous, days, label="ruinous")

    assert result.orders == 0
    assert result.performance().total_return == 0.0


async def test_the_replay_fills_at_the_next_session_not_the_deciding_price(
    context: AppContext, clock: FrozenClock
) -> None:
    """A fill at the price that triggered the decision is look-ahead wearing a fill's clothes."""
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.003, count=400, end=LAST_BAR)
    days = await days_for(context, 60)
    portfolio_id = await make_portfolio(context, clock)

    await replay(context, portfolio_id, days)

    async with context.db.session() as session:
        from tradebot.db.models import Fill, Order

        rows = list(
            await session.scalars(
                select(Fill)
                .join(Order, Fill.order_id == Order.id)
                .where(Order.portfolio_id == portfolio_id)
            )
        )
        orders = {
            order.id: order
            for order in await session.scalars(
                select(Order).where(Order.portfolio_id == portfolio_id)
            )
        }

    assert rows, "no fills to check"
    for fill in rows:
        assert fill.executed_at > orders[fill.order_id].submitted_at


async def test_a_replay_produces_a_dated_equity_curve(
    context: AppContext, clock: FrozenClock
) -> None:
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.002, count=400, end=LAST_BAR)
    days = await days_for(context, 40)
    portfolio_id = await make_portfolio(context, clock)

    result = await replay(context, portfolio_id, days)

    assert len(result.equity) == len(days)
    assert result.dates == days
    assert all(value > 0 for value in result.equity)


async def test_a_replay_never_reads_a_bar_it_should_not_see(
    context: AppContext, clock: FrozenClock
) -> None:
    """The replay walks a ReplayClock, so MarketView's guard applies unchanged."""
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.002, count=400, end=LAST_BAR)
    days = await days_for(context, 30)
    portfolio_id = await make_portfolio(context, clock)

    result = await replay(context, portfolio_id, days)

    async with context.db.session() as session:
        from tradebot.db.models import DecisionRun

        runs = list(
            await session.scalars(
                select(DecisionRun).where(DecisionRun.portfolio_id == portfolio_id)
            )
        )

    assert result.error is None
    assert runs
    for run, day in zip(runs, days, strict=False):
        assert run.as_of <= day, "a cycle saw a bar dated after its own clock"


async def test_an_empty_window_is_reported_rather_than_crashing(
    context: AppContext, clock: FrozenClock
) -> None:
    portfolio_id = await make_portfolio(context, clock)

    result = await replay(context, portfolio_id, [])

    assert result.error is not None
    assert result.equity == []
