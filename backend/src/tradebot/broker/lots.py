from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.core.errors import ValidationError
from tradebot.core.money import quantize_cash, quantize_price
from tradebot.db.models import Lot, Position

ZERO = Decimal(0)
DUST = Decimal("0.0000000001")


@dataclass(frozen=True)
class Consumption:
    lot_id: int
    qty: Decimal
    cost_basis: Decimal
    proceeds: Decimal
    realized: Decimal


async def open_lots(session: AsyncSession, position_id: int) -> list[Lot]:
    rows = await session.scalars(
        select(Lot)
        .where(Lot.position_id == position_id, Lot.qty_open > ZERO)
        .order_by(Lot.opened_at, Lot.id)
    )
    return list(rows)


async def consume_fifo(
    session: AsyncSession, position: Position, qty: Decimal, price: Decimal
) -> list[Consumption]:
    """Sell `qty` against the oldest lots first, returning what each contributed.

    Realized profit is per lot rather than against an average, which is what keeps a scale-in
    followed by a partial exit honest.
    """
    if qty <= ZERO:
        raise ValidationError("sell quantity must be positive")

    lots = await open_lots(session, position.id)
    available = sum((lot.qty_open for lot in lots), ZERO)
    if qty - available > DUST:
        raise ValidationError(f"cannot sell {qty}; only {available} open")

    remaining = qty
    consumed: list[Consumption] = []

    for lot in lots:
        if remaining <= ZERO:
            break
        taken = min(lot.qty_open, remaining)
        proceeds = taken * price
        realized = quantize_cash(proceeds - (taken * lot.cost_basis))

        lot.qty_open = lot.qty_open - taken
        remaining -= taken
        consumed.append(
            Consumption(
                lot_id=lot.id,
                qty=taken,
                cost_basis=lot.cost_basis,
                proceeds=quantize_cash(proceeds),
                realized=realized,
            )
        )

    await session.flush()
    return consumed


async def project(session: AsyncSession, position: Position) -> None:
    """Recompute quantity and average cost from the lots. Lots are the truth; these are cached."""
    lots = await open_lots(session, position.id)
    qty = sum((lot.qty_open for lot in lots), ZERO)
    cost = sum((lot.qty_open * lot.cost_basis for lot in lots), ZERO)

    position.qty = qty
    position.avg_cost = quantize_price(cost / qty) if qty > ZERO else ZERO
    await session.flush()


def unrealized(position: Position, mark: Decimal) -> Decimal:
    return quantize_cash((mark - position.avg_cost) * position.qty)


def market_value(position: Position, mark: Decimal) -> Decimal:
    return quantize_cash(position.qty * mark)
