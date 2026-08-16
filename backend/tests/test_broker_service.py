from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from tradebot.broker.ledger import Ledger
from tradebot.broker.reconcile import assert_ok, reconcile
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.core.errors import ValidationError
from tradebot.db.models import (
    Instrument,
    LedgerEntry,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionStatus,
    Side,
    TimeInForce,
    User,
)
from tradebot.providers.base import Quote

# A Wednesday inside regular US trading hours, so equity orders are not gated by the calendar.
NOW = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
CAPITAL = Decimal(100_000)


@pytest.fixture
def broker(context: AppContext) -> BrokerService:
    clock = FrozenClock(NOW)
    return BrokerService(Ledger(clock=clock), context.events, clock=clock)


async def setup(context: AppContext, **portfolio_settings: object) -> tuple[int, int]:
    async with context.db.session() as session:
        session.add(User(email="t@example.com", password_hash="x", display_name="T"))
        instrument = Instrument(symbol="AAA", asset_class="crypto")
        session.add(instrument)
        await session.flush()

        clock = FrozenClock(NOW)
        service = BrokerService(Ledger(clock=clock), context.events, clock=clock)
        portfolio = await service.create_portfolio(
            session,
            user_id=1,
            name="Main",
            initial_capital=CAPITAL,
            allow_fractional=True,
            **portfolio_settings,
        )
        return portfolio.id, instrument.id


def quote(price: str) -> Quote:
    value = Decimal(price)
    return Quote(symbol="AAA", price=value, at=NOW, bid=value, ask=value)


async def buy(
    context: AppContext, broker: BrokerService, ids: tuple[int, int], qty: str, price: str
) -> None:
    portfolio_id, instrument_id = ids
    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        instrument = await session.get(Instrument, instrument_id)
        await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(qty),
            reference_price=Decimal(price),
        )
        await broker.on_quote(session, portfolio, instrument, quote(price))


async def sell(
    context: AppContext, broker: BrokerService, ids: tuple[int, int], qty: str, price: str
) -> None:
    portfolio_id, instrument_id = ids
    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        instrument = await session.get(Instrument, instrument_id)
        await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.SELL,
            qty=Decimal(qty),
            reference_price=Decimal(price),
        )
        await broker.on_quote(session, portfolio, instrument, quote(price))


async def test_initial_capital_lands_in_the_ledger(
    context: AppContext, broker: BrokerService
) -> None:
    portfolio_id, _ = await setup(context)

    async with context.db.session() as session:
        assert await broker.cash(session, portfolio_id) == CAPITAL
        entries = list(await session.scalars(select(LedgerEntry)))

    assert len(entries) == 1
    assert entries[0].balance_after == CAPITAL


async def test_a_buy_moves_cash_and_opens_a_position(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)
    await buy(context, broker, ids, "10", "100")

    async with context.db.session() as session:
        cash = await broker.cash(session, ids[0])
        positions = await broker.open_positions(session, ids[0])

    assert cash < CAPITAL
    assert len(positions) == 1
    assert positions[0].qty == Decimal(10)


async def test_a_round_trip_at_the_same_price_loses_exactly_the_costs(
    context: AppContext, broker: BrokerService
) -> None:
    """Buying and selling at one price must lose money — slippage plus commission, nothing else."""
    ids = await setup(context, slippage_bps=Decimal(10), commission_bps=Decimal(0))
    await buy(context, broker, ids, "10", "100")
    await sell(context, broker, ids, "10", "100")

    async with context.db.session() as session:
        cash = await broker.cash(session, ids[0])
        positions = await broker.open_positions(session, ids[0])

    # 10 shares bought at 100.1 and sold at 99.9 costs 0.2 a share.
    assert cash == CAPITAL - Decimal(2)
    assert positions == []


async def test_fifo_consumes_the_oldest_lot_first(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, slippage_bps=Decimal(0), commission_bps=Decimal(0))
    await buy(context, broker, ids, "10", "100")
    await buy(context, broker, ids, "10", "200")
    await sell(context, broker, ids, "10", "300")

    async with context.db.session() as session:
        position = await session.scalar(
            select(Position).where(Position.status == PositionStatus.OPEN)
        )

    # The 100 lot is consumed, leaving the 200 lot and 200 a share of profit.
    assert position is not None
    assert position.qty == Decimal(10)
    assert position.avg_cost == Decimal(200)
    assert position.realized_pnl == Decimal(2000)


async def test_a_partial_exit_leaves_the_rest_of_the_lot_open(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, slippage_bps=Decimal(0), commission_bps=Decimal(0))
    await buy(context, broker, ids, "10", "100")
    await sell(context, broker, ids, "4", "150")

    async with context.db.session() as session:
        position = await session.scalar(
            select(Position).where(Position.status == PositionStatus.OPEN)
        )

    assert position is not None
    assert position.qty == Decimal(6)
    assert position.realized_pnl == Decimal(200)


async def test_selling_everything_closes_the_position(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, slippage_bps=Decimal(0), commission_bps=Decimal(0))
    await buy(context, broker, ids, "10", "100")
    await sell(context, broker, ids, "10", "120")

    async with context.db.session() as session:
        closed = await session.scalar(
            select(Position).where(Position.status == PositionStatus.CLOSED)
        )

    assert closed is not None
    assert closed.qty == Decimal(0)
    assert closed.closed_at is not None


async def test_selling_more_than_held_is_rejected(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)
    await buy(context, broker, ids, "5", "100")

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.SELL,
            qty=Decimal(50),
            reference_price=Decimal(100),
        )

    assert order.status == OrderStatus.REJECTED
    assert "insufficient position" in (order.reject_reason or "")


async def test_an_unaffordable_order_is_rejected(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(100_000),
            reference_price=Decimal(100),
        )

    assert order.status == OrderStatus.REJECTED
    assert "buying power" in (order.reject_reason or "")


async def test_two_open_orders_cannot_jointly_overdraw(
    context: AppContext, broker: BrokerService
) -> None:
    """The reservation exists precisely so the second order sees the first one's claim."""
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        first = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(900),
            order_type=OrderType.LIMIT,
            limit_price=Decimal(100),
        )
        second = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(900),
            order_type=OrderType.LIMIT,
            limit_price=Decimal(100),
        )

    assert first.status == OrderStatus.ACCEPTED
    assert second.status == OrderStatus.REJECTED


async def test_cancelling_releases_the_reservation(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(100),
            order_type=OrderType.LIMIT,
            limit_price=Decimal(100),
        )
        held = await broker.buying_power(session, ids[0])
        await broker.cancel_order(session, order)
        released = await broker.buying_power(session, ids[0])

    assert held < CAPITAL
    assert released == CAPITAL


async def test_the_same_client_order_id_does_not_trade_twice(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        first = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(10),
            reference_price=Decimal(100),
            client_order_id="retry-me",
        )
        second = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(10),
            reference_price=Decimal(100),
            client_order_id="retry-me",
        )

    assert first.id == second.id


async def test_a_limit_order_waits_for_a_marketable_quote(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(10),
            order_type=OrderType.LIMIT,
            limit_price=Decimal(90),
        )
        await broker.on_quote(session, portfolio, instrument, quote("100"))
        assert order.status == OrderStatus.ACCEPTED

        await broker.on_quote(session, portfolio, instrument, quote("89"))

    assert order.status == OrderStatus.FILLED


async def test_an_ioc_order_expires_when_it_cannot_fill(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(10),
            order_type=OrderType.LIMIT,
            limit_price=Decimal(50),
            time_in_force=TimeInForce.GTC,
        )
        order.time_in_force = TimeInForce.IOC
        await broker.on_quote(session, portfolio, instrument, quote("100"))

    assert order.status == OrderStatus.EXPIRED


async def test_a_gap_up_shrinks_the_buy_instead_of_overdrawing_the_account(
    context: AppContext, broker: BrokerService
) -> None:
    """Real data found this too: a fill above the reserved price crashed the whole cycle."""
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await broker.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(999),
            reference_price=Decimal(100),
        )
        await broker.on_quote(session, portfolio, instrument, quote("120"))
        cash = await broker.cash(session, portfolio.id)

    assert cash >= Decimal(0)
    assert order.filled_qty < Decimal(999)
    assert order.filled_qty > Decimal(0)


async def test_a_day_order_placed_after_the_close_survives_into_the_next_session(
    context: AppContext,
) -> None:
    """Real data found this: every order a close-time cycle placed was born already expired.

    2026-08-12 is a Wednesday, so 21:00 UTC is an hour past the 20:00 UTC close and the next
    session is the following calendar day.
    """
    after_close = datetime(2026, 8, 12, 21, 0, tzinfo=UTC)
    clock = FrozenClock(after_close)
    service = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await service.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(10),
            reference_price=Decimal(100),
        )

    assert order.status == OrderStatus.ACCEPTED
    assert order.expires_at is not None
    assert order.expires_at > after_close
    assert order.expires_at.date() > after_close.date()


async def test_a_day_order_placed_during_the_session_still_expires_at_that_close(
    context: AppContext,
) -> None:
    during = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
    clock = FrozenClock(during)
    service = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await service.place_order(
            session,
            portfolio=portfolio,
            instrument=instrument,
            side=Side.BUY,
            qty=Decimal(10),
            reference_price=Decimal(100),
        )

    assert order.expires_at is not None
    assert order.expires_at.date() == during.date()


async def test_equity_equals_cash_plus_marked_positions(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, slippage_bps=Decimal(0), commission_bps=Decimal(0))
    await buy(context, broker, ids, "10", "100")

    async with context.db.session() as session:
        cash = await broker.cash(session, ids[0])
        equity = await broker.equity(session, ids[0], {ids[1]: Decimal(150)})

    assert cash == CAPITAL - Decimal(1000)
    assert equity == cash + Decimal(1500)


async def test_a_snapshot_records_the_equity_curve(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, slippage_bps=Decimal(0), commission_bps=Decimal(0))
    await buy(context, broker, ids, "10", "100")

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        snapshot = await broker.snapshot(session, portfolio, {ids[1]: Decimal(150)})

    assert snapshot.open_positions == 1
    assert snapshot.positions_value == Decimal(1500)
    assert snapshot.unrealized_pnl == Decimal(500)


async def test_a_drawdown_is_recorded_against_the_high_water_mark(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, slippage_bps=Decimal(0), commission_bps=Decimal(0))
    await buy(context, broker, ids, "100", "100")

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        snapshot = await broker.snapshot(session, portfolio, {ids[1]: Decimal(50)})

    assert snapshot.equity < CAPITAL
    assert snapshot.drawdown_pct > Decimal(0)


async def test_a_deposit_must_be_positive(context: AppContext) -> None:
    ledger = Ledger(clock=FrozenClock(NOW))
    portfolio_id, _ = await setup(context)

    async with context.db.session() as session:
        with pytest.raises(ValidationError, match="positive"):
            await ledger.deposit(session, portfolio_id=portfolio_id, amount=Decimal(-5))


async def test_the_ledger_refuses_to_overdraw(context: AppContext) -> None:
    ledger = Ledger(clock=FrozenClock(NOW))
    portfolio_id, _ = await setup(context)

    async with context.db.session() as session:
        with pytest.raises(ValidationError, match="overdraw"):
            await ledger.post(
                session,
                portfolio_id=portfolio_id,
                entry_type="BUY",
                amount=-(CAPITAL * 2),
            )


async def test_reconciliation_passes_after_a_full_trading_sequence(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)
    await buy(context, broker, ids, "10", "100")
    await buy(context, broker, ids, "5", "120")
    await sell(context, broker, ids, "8", "130")

    async with context.db.session() as session:
        report = await reconcile(session, Ledger(clock=FrozenClock(NOW)), ids[0])

    assert_ok(report)
    assert report.cash_projected == report.cash_replayed


async def test_seeding_a_holding_debits_cash_and_opens_a_position(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        order = await broker.seed_holding(
            session,
            portfolio=portfolio,
            instrument=instrument,
            qty=Decimal(10),
            cost_basis=Decimal(100),
            opened_at=NOW,
        )
        cash = await broker.cash(session, portfolio.id)
        position = await broker._open_position(session, portfolio.id, instrument.id)

    assert order.status == OrderStatus.FILLED
    assert cash == CAPITAL - Decimal(1000)
    assert position is not None
    assert position.qty == Decimal(10)
    assert position.avg_cost == Decimal(100)


async def test_a_seeded_holding_charges_no_slippage_or_commission(
    context: AppContext,
) -> None:
    """The trade happened at another broker, which already took its cut."""
    clock = FrozenClock(NOW)
    service = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    ids = await setup(context, slippage_bps=Decimal(50), commission_bps=Decimal(20))

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        await service.seed_holding(
            session,
            portfolio=portfolio,
            instrument=instrument,
            qty=Decimal(10),
            cost_basis=Decimal(100),
            opened_at=NOW,
        )
        cash = await service.cash(session, portfolio.id)

    assert cash == CAPITAL - Decimal(1000), "cost basis must be exactly what was entered"


async def test_a_seeded_holding_reconciles_against_a_ledger_replay(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        await broker.seed_holding(
            session,
            portfolio=portfolio,
            instrument=instrument,
            qty=Decimal(4),
            cost_basis=Decimal(250),
            opened_at=NOW,
        )
        assert_ok(await reconcile(session, Ledger(clock=FrozenClock(NOW)), portfolio.id))


async def test_a_seeded_holding_can_then_be_sold_normally(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        await broker.seed_holding(
            session,
            portfolio=portfolio,
            instrument=instrument,
            qty=Decimal(10),
            cost_basis=Decimal(100),
            opened_at=NOW,
        )

    await sell(context, broker, ids, "10", "120")

    async with context.db.session() as session:
        position = await broker._open_position(session, ids[0], ids[1])
        assert position is None, "selling the seeded quantity closes the position"


async def test_seeding_more_than_the_cash_allows_is_rejected(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, ids[0])
        instrument = await session.get(Instrument, ids[1])
        with pytest.raises(ValidationError):
            await broker.seed_holding(
                session,
                portfolio=portfolio,
                instrument=instrument,
                qty=Decimal(10_000),
                cost_basis=Decimal(100),
                opened_at=NOW,
            )
