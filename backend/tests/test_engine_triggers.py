from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.test_engine_cycle import NOW, seed
from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.db.models import (
    Event,
    Instrument,
    Order,
    Portfolio,
    Position,
    PositionStatus,
    User,
)
from tradebot.engine.triggers import StopTrigger


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


def broker_for(context: AppContext, clock: FrozenClock) -> BrokerService:
    return BrokerService(Ledger(clock=clock), context.events, clock=clock)


async def open_position(
    context: AppContext, clock: FrozenClock, *, stop: Decimal | None, qty: Decimal = Decimal(10)
) -> tuple[int, int]:
    instrument_id = await seed(context, "AAA")
    async with context.db.session() as session:
        user = User(email="stops@example.com", password_hash="x", display_name="Stops")
        session.add(user)
        await session.flush()

        portfolio = await broker_for(context, clock).create_portfolio(
            session,
            user_id=user.id,
            name="Stops",
            initial_capital=Decimal(100_000),
            allow_fractional=True,
        )
        session.add(
            Position(
                portfolio_id=portfolio.id,
                instrument_id=instrument_id,
                status=PositionStatus.OPEN,
                qty=qty,
                avg_cost=Decimal(100),
                opened_at=NOW,
                stop_price=stop,
                highest_close=Decimal(110),
            )
        )
        return int(portfolio.id), instrument_id


async def test_a_quote_through_the_stop_places_a_protective_sell(
    context: AppContext, clock: FrozenClock
) -> None:
    portfolio_id, _ = await open_position(context, clock, stop=Decimal(90))

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)
        breaches = await trigger.on_quote(session, "AAA", Decimal("89.50"))

    assert len(breaches) == 1
    assert breaches[0].portfolio_id == portfolio_id
    assert breaches[0].stop_price == Decimal(90)

    async with context.db.session() as session:
        order = await session.scalar(select(Order))

    assert order.side == "SELL"
    assert order.qty == Decimal(10)


async def test_a_quote_above_the_stop_does_nothing(context: AppContext, clock: FrozenClock) -> None:
    await open_position(context, clock, stop=Decimal(90))

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)
        breaches = await trigger.on_quote(session, "AAA", Decimal("90.01"))

    assert breaches == []

    async with context.db.session() as session:
        assert await session.scalar(select(Order)) is None


async def test_a_position_without_a_stop_is_left_alone(
    context: AppContext, clock: FrozenClock
) -> None:
    await open_position(context, clock, stop=None)

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)

        assert await trigger.on_quote(session, "AAA", Decimal("1")) == []


async def test_a_breach_is_not_acted_on_twice(context: AppContext, clock: FrozenClock) -> None:
    """The first sell is still working, so a second tick must not stack another one."""
    await open_position(context, clock, stop=Decimal(90))

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)
        first = await trigger.on_quote(session, "AAA", Decimal("89.50"))
        second = await trigger.on_quote(session, "AAA", Decimal("89.00"))

    assert len(first) == 1
    assert second == []

    async with context.db.session() as session:
        orders = list(await session.scalars(select(Order)))

    assert len(orders) == 1


async def test_a_breach_records_a_risk_event(context: AppContext, clock: FrozenClock) -> None:
    await open_position(context, clock, stop=Decimal(90))

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)
        await trigger.on_quote(session, "AAA", Decimal("89.50"))

    async with context.db.session() as session:
        event = await session.scalar(select(Event).where(Event.kind == "stop_triggered"))

    assert event.domain == "risk"
    assert event.severity == "warning"
    assert event.payload["symbol"] == "AAA"


async def test_an_untracked_symbol_is_ignored(context: AppContext, clock: FrozenClock) -> None:
    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)

        assert await trigger.on_quote(session, "NOPE", Decimal(1)) == []


async def test_only_the_breached_portfolio_is_touched(
    context: AppContext, clock: FrozenClock
) -> None:
    """Two portfolios can hold the same name with different stops."""
    await open_position(context, clock, stop=Decimal(90))

    async with context.db.session() as session:
        instrument = await session.scalar(select(Instrument).where(Instrument.symbol == "AAA"))
        user = User(email="second@example.com", password_hash="x", display_name="Second")
        session.add(user)
        await session.flush()
        other = await broker_for(context, clock).create_portfolio(
            session,
            user_id=user.id,
            name="Patient",
            initial_capital=Decimal(50_000),
            allow_fractional=True,
        )
        session.add(
            Position(
                portfolio_id=other.id,
                instrument_id=instrument.id,
                status=PositionStatus.OPEN,
                qty=Decimal(5),
                avg_cost=Decimal(100),
                opened_at=NOW,
                stop_price=Decimal(50),
            )
        )
        other_id = int(other.id)

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)
        breaches = await trigger.on_quote(session, "AAA", Decimal("89.50"))

    assert len(breaches) == 1
    assert breaches[0].portfolio_id != other_id

    async with context.db.session() as session:
        orders = list(await session.scalars(select(Order).where(Order.portfolio_id == other_id)))

    assert orders == []


async def test_a_closed_position_is_never_re_sold(context: AppContext, clock: FrozenClock) -> None:
    await open_position(context, clock, stop=Decimal(90))
    async with context.db.session() as session:
        position = await session.scalar(select(Position))
        position.status = PositionStatus.CLOSED
        position.qty = Decimal(0)

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)

        assert await trigger.on_quote(session, "AAA", Decimal("50")) == []


async def test_the_portfolio_is_reachable_for_the_order(
    context: AppContext, clock: FrozenClock
) -> None:
    portfolio_id, _ = await open_position(context, clock, stop=Decimal(90))

    async with context.db.session() as session:
        trigger = StopTrigger(broker_for(context, clock), context.events, clock)
        await trigger.on_quote(session, "AAA", Decimal("89.50"))

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        order = await session.scalar(select(Order))

    assert order.portfolio_id == portfolio.id
