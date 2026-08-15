"""The five invariants named in the plan, plus the drift detection that backs them.

If any of these can fail silently, months of simulated results become unverifiable.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.test_broker_service import NOW, buy, sell, setup
from tradebot.broker.ledger import Ledger
from tradebot.broker.lots import open_lots
from tradebot.broker.reconcile import reconcile
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.db.models import (
    EntryType,
    Fill,
    LedgerEntry,
    Order,
    OrderStatus,
    Position,
)

ZERO = Decimal(0)


@pytest.fixture
def broker(context: AppContext) -> BrokerService:
    clock = FrozenClock(NOW)
    return BrokerService(Ledger(clock=clock), context.events, clock=clock)


async def trade_a_bit(context: AppContext, broker: BrokerService) -> tuple[int, int]:
    ids = await setup(context)
    await buy(context, broker, ids, "10", "100")
    await buy(context, broker, ids, "5", "120")
    await sell(context, broker, ids, "7", "130")
    await buy(context, broker, ids, "3", "115")
    await sell(context, broker, ids, "4", "125")
    return ids


async def test_replaying_the_ledger_reproduces_cash_exactly(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await trade_a_bit(context, broker)
    ledger = Ledger(clock=FrozenClock(NOW))

    async with context.db.session() as session:
        projected = await ledger.balance(session, ids[0])
        replayed = await ledger.replay(session, ids[0])

    assert projected == replayed


async def test_open_lots_sum_to_position_quantity(
    context: AppContext, broker: BrokerService
) -> None:
    await trade_a_bit(context, broker)

    async with context.db.session() as session:
        positions = list(await session.scalars(select(Position)))
        for position in positions:
            lots = await open_lots(session, position.id)
            assert sum((lot.qty_open for lot in lots), ZERO) == position.qty


async def test_equity_equals_cash_plus_marked_positions(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await trade_a_bit(context, broker)
    mark = Decimal(140)

    async with context.db.session() as session:
        cash = await broker.cash(session, ids[0])
        positions = await broker.open_positions(session, ids[0])
        equity = await broker.equity(session, ids[0], {ids[1]: mark})

    expected = cash + sum((p.qty * mark for p in positions), ZERO)
    assert equity == expected


async def test_realized_pnl_matches_the_ledger(context: AppContext, broker: BrokerService) -> None:
    """Position-level realized profit must agree with the cash the ledger actually moved."""
    ids = await trade_a_bit(context, broker)

    async with context.db.session() as session:
        positions = list(await session.scalars(select(Position)))
        position_realized = sum((p.realized_pnl for p in positions), ZERO)

        entries = list(
            await session.scalars(select(LedgerEntry).where(LedgerEntry.portfolio_id == ids[0]))
        )
        traded = sum(
            (
                e.amount
                for e in entries
                if e.entry_type in (EntryType.BUY, EntryType.SELL, EntryType.FEE)
            ),
            ZERO,
        )
        lots_open = ZERO
        for position in positions:
            for lot in await open_lots(session, position.id):
                lots_open += lot.qty_open * lot.cost_basis

    # Cash spent plus the cost still tied up in open lots is exactly realized profit.
    assert traded + lots_open == position_realized


async def test_no_order_fills_beyond_its_quantity(
    context: AppContext, broker: BrokerService
) -> None:
    await trade_a_bit(context, broker)

    async with context.db.session() as session:
        orders = list(await session.scalars(select(Order)))
        for order in orders:
            fills = list(await session.scalars(select(Fill).where(Fill.order_id == order.id)))
            filled = sum((f.qty for f in fills), ZERO)
            assert filled <= order.qty
            assert order.filled_qty == filled


async def test_reservations_net_to_zero_at_a_terminal_state(
    context: AppContext, broker: BrokerService
) -> None:
    await trade_a_bit(context, broker)

    async with context.db.session() as session:
        orders = list(await session.scalars(select(Order)))
        for order in orders:
            if order.status in OrderStatus.TERMINAL:
                assert order.reserved_cash == ZERO


async def test_reconciliation_reports_clean_after_trading(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await trade_a_bit(context, broker)

    async with context.db.session() as session:
        report = await reconcile(session, Ledger(clock=FrozenClock(NOW)), ids[0])

    assert report.ok, report.problems


async def test_reconciliation_detects_a_tampered_position(
    context: AppContext, broker: BrokerService
) -> None:
    """The check has to be able to fail, or it proves nothing."""
    ids = await trade_a_bit(context, broker)

    async with context.db.session() as session:
        position = await session.scalar(select(Position))
        position.qty = position.qty + Decimal(999)

    async with context.db.session() as session:
        report = await reconcile(session, Ledger(clock=FrozenClock(NOW)), ids[0])

    assert not report.ok
    assert any("open lots total" in problem for problem in report.problems)


async def test_reconciliation_detects_a_tampered_ledger_balance(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await trade_a_bit(context, broker)

    async with context.db.session() as session:
        entry = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.portfolio_id == ids[0]).order_by(LedgerEntry.id)
        )
        entry.balance_after = entry.balance_after + Decimal(1)

    async with context.db.session() as session:
        report = await reconcile(session, Ledger(clock=FrozenClock(NOW)), ids[0])

    assert not report.ok
    assert any("drifts" in problem for problem in report.problems)


async def test_cash_never_goes_negative_through_a_long_sequence(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context)
    ledger = Ledger(clock=FrozenClock(NOW))

    for index in range(12):
        await buy(context, broker, ids, "5", str(100 + index))
        async with context.db.session() as session:
            assert await ledger.balance(session, ids[0]) >= ZERO

    async with context.db.session() as session:
        assert (await reconcile(session, ledger, ids[0])).ok


async def test_every_fill_has_a_matching_pair_of_ledger_entries(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, commission_bps=Decimal(5), min_commission=Decimal(1))
    await buy(context, broker, ids, "10", "100")

    async with context.db.session() as session:
        fill = await session.scalar(select(Fill))
        entries = list(
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.ref_type == "fill", LedgerEntry.ref_id == fill.id
                )
            )
        )

    kinds = sorted(e.entry_type for e in entries)
    assert kinds == [EntryType.BUY, EntryType.FEE]


async def test_a_closed_position_holds_no_open_lots(
    context: AppContext, broker: BrokerService
) -> None:
    ids = await setup(context, slippage_bps=ZERO, commission_bps=ZERO)
    await buy(context, broker, ids, "10", "100")
    await sell(context, broker, ids, "10", "110")

    async with context.db.session() as session:
        position = await session.scalar(select(Position))
        lots = await open_lots(session, position.id)

    assert lots == []
    assert position.qty == ZERO
