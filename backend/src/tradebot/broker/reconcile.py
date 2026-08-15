from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.broker.ledger import Ledger
from tradebot.broker.lots import open_lots
from tradebot.core.money import quantize_cash
from tradebot.db.models import Lot, Order, OrderStatus, Position, PositionStatus

ZERO = Decimal(0)
DUST = Decimal("0.0000000001")


@dataclass
class ReconciliationReport:
    portfolio_id: int
    cash_projected: Decimal = ZERO
    cash_replayed: Decimal = ZERO
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


async def reconcile(
    session: AsyncSession, ledger: Ledger, portfolio_id: int
) -> ReconciliationReport:
    """Assert the projections still equal a replay of the ledger.

    A single accounting bug quietly invalidates months of results, so this runs as a job rather
    than living only in tests.
    """
    report = ReconciliationReport(portfolio_id=portfolio_id)

    report.cash_projected = await ledger.balance(session, portfolio_id)
    report.cash_replayed = await ledger.replay(session, portfolio_id)
    if report.cash_projected != report.cash_replayed:
        report.problems.append(
            f"cash {report.cash_projected} does not match replay {report.cash_replayed}"
        )

    drifted = await ledger.find_drift(session, portfolio_id)
    if drifted:
        report.problems.append(f"ledger running balance drifts at entries {drifted[:5]}")

    positions = await session.scalars(select(Position).where(Position.portfolio_id == portfolio_id))
    for position in positions:
        lots = await open_lots(session, position.id)
        lot_qty = sum((lot.qty_open for lot in lots), ZERO)
        if abs(lot_qty - position.qty) > DUST:
            report.problems.append(
                f"position {position.id}: qty {position.qty} but open lots total {lot_qty}"
            )
        if position.status == PositionStatus.CLOSED and lot_qty > DUST:
            report.problems.append(f"position {position.id} is closed with {lot_qty} still open")
        if position.qty < ZERO:
            report.problems.append(f"position {position.id} has negative quantity")

    await _check_orders(session, portfolio_id, report)
    await _check_lots(session, portfolio_id, report)
    return report


async def _check_orders(
    session: AsyncSession, portfolio_id: int, report: ReconciliationReport
) -> None:
    orders = await session.scalars(select(Order).where(Order.portfolio_id == portfolio_id))
    for order in orders:
        if order.filled_qty > order.qty + DUST:
            report.problems.append(f"order {order.id} filled {order.filled_qty} of {order.qty}")
        if order.status in OrderStatus.TERMINAL and order.reserved_cash > DUST:
            report.problems.append(
                f"order {order.id} is {order.status} but still reserves {order.reserved_cash}"
            )
        if order.status == OrderStatus.FILLED and abs(order.filled_qty - order.qty) > DUST:
            report.problems.append(f"order {order.id} is FILLED but only {order.filled_qty} done")


async def _check_lots(
    session: AsyncSession, portfolio_id: int, report: ReconciliationReport
) -> None:
    rows = await session.scalars(
        select(Lot).join(Position).where(Position.portfolio_id == portfolio_id)
    )
    for lot in rows:
        if lot.qty_open > lot.qty_original + DUST:
            report.problems.append(
                f"lot {lot.id} has {lot.qty_open} open of {lot.qty_original} original"
            )
        if lot.qty_open < ZERO:
            report.problems.append(f"lot {lot.id} has negative open quantity")


def assert_ok(report: ReconciliationReport) -> None:
    if not report.ok:
        raise AssertionError(
            f"portfolio {report.portfolio_id} failed reconciliation: {'; '.join(report.problems)}"
        )


def summarize(report: ReconciliationReport) -> dict[str, object]:
    return {
        "portfolio_id": report.portfolio_id,
        "ok": report.ok,
        "cash": str(quantize_cash(report.cash_replayed)),
        "problems": report.problems,
    }
