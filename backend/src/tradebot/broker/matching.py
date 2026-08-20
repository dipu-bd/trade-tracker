from dataclasses import dataclass
from decimal import Decimal

from tradebot.db.models import Order, OrderType, Side
from tradebot.providers.base import Quote

ZERO = Decimal(0)


@dataclass(frozen=True)
class Trigger:
    """Whether a quote fills an order, and at what reference price."""

    fills: bool
    reference: Decimal | None = None
    reason: str = ""


def _marketable_side_price(order: Order, quote: Quote) -> Decimal:
    """A buy lifts the ask, a sell hits the bid; midpoint when there is no book."""
    if order.side == Side.BUY:
        return quote.ask or quote.price
    return quote.bid or quote.price


def stop_triggered(order: Order, quote: Quote) -> bool:
    if order.stop_price is None:
        return False
    last = quote.price
    if order.side == Side.BUY:
        return last >= order.stop_price
    return last <= order.stop_price


def arms(order: Order, quote: Quote) -> bool:
    """Whether this quote elects a stop order that was not already elected.

    Kept apart from `evaluate` because election and execution are different events: a
    stop-limit is elected the moment the stop trades through, and only then starts working its
    limit, possibly for many ticks. Folding the two together is what made the arming flag
    unreachable — it was only ever set on a tick that also produced a fill price.
    """
    if order.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT):
        return False
    return not order.stop_armed and stop_triggered(order, quote)


def evaluate(order: Order, quote: Quote, *, stop_armed: bool | None = None) -> Trigger:
    """Decide whether this quote fills the order.

    `stop_armed` carries whether the stop has already been hit on an earlier tick, so a
    stop-limit does not need the stop and the limit to be satisfiable on the same tick. It
    defaults to the flag stored on the order, which is where that state lives between passes.
    """
    if quote.price <= ZERO:
        return Trigger(False, reason="no price")

    if order.order_type == OrderType.MARKET:
        return Trigger(True, _marketable_side_price(order, quote), "market")

    if order.order_type == OrderType.LIMIT:
        return _limit_trigger(order, quote)

    if stop_armed is None:
        stop_armed = bool(order.stop_armed)
    armed = stop_armed or stop_triggered(order, quote)
    if not armed:
        return Trigger(False, reason="stop not reached")

    if order.order_type == OrderType.STOP:
        return Trigger(True, _marketable_side_price(order, quote), "stop")

    return _limit_trigger(order, quote, reason_prefix="stop-limit ")


def _limit_trigger(order: Order, quote: Quote, *, reason_prefix: str = "") -> Trigger:
    limit = order.limit_price
    if limit is None:
        return Trigger(False, reason="missing limit")

    marketable = _marketable_side_price(order, quote)
    if order.side == Side.BUY and marketable <= limit:
        # Price improvement is real: a marketable buy fills at the ask, never above the limit.
        return Trigger(True, min(marketable, limit), f"{reason_prefix}limit")
    if order.side == Side.SELL and marketable >= limit:
        return Trigger(True, max(marketable, limit), f"{reason_prefix}limit")

    return Trigger(False, reason=f"{reason_prefix}limit not marketable")


def fillable_qty(order: Order, quote: Quote, *, participation: Decimal | None = None) -> Decimal:
    """How much of the remainder this tick can absorb.

    Without quoted size the whole remainder fills; a participation cap models a thin book.
    """
    remaining = order.remaining_qty
    if participation is None or quote.volume is None or quote.volume <= ZERO:
        return remaining
    return min(remaining, quote.volume * participation)
