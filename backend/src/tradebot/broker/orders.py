from datetime import datetime
from decimal import Decimal

from tradebot.core.errors import ValidationError
from tradebot.db.models import Order, OrderStatus, OrderType, Side, TimeInForce

ZERO = Decimal(0)

LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    OrderStatus.NEW: frozenset({OrderStatus.ACCEPTED, OrderStatus.REJECTED}),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}

REQUIRES_LIMIT = frozenset({OrderType.LIMIT, OrderType.STOP_LIMIT})
REQUIRES_STOP = frozenset({OrderType.STOP, OrderType.STOP_LIMIT})


def validate_request(
    *,
    side: str,
    order_type: str,
    time_in_force: str,
    qty: Decimal,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
    allow_fractional: bool,
) -> None:
    if side not in (Side.BUY, Side.SELL):
        raise ValidationError(f"unknown side: {side}")
    if order_type not in (
        OrderType.MARKET,
        OrderType.LIMIT,
        OrderType.STOP,
        OrderType.STOP_LIMIT,
    ):
        raise ValidationError(f"unknown order type: {order_type}")
    if time_in_force not in (TimeInForce.DAY, TimeInForce.GTC, TimeInForce.IOC):
        raise ValidationError(f"unknown time in force: {time_in_force}")

    if qty <= ZERO:
        raise ValidationError("quantity must be positive")
    if not allow_fractional and qty != qty.to_integral_value():
        raise ValidationError("this instrument does not support fractional quantities")

    if order_type in REQUIRES_LIMIT and (limit_price is None or limit_price <= ZERO):
        raise ValidationError(f"{order_type} requires a positive limit price")
    if order_type in REQUIRES_STOP and (stop_price is None or stop_price <= ZERO):
        raise ValidationError(f"{order_type} requires a positive stop price")
    if order_type == OrderType.MARKET and (limit_price or stop_price):
        raise ValidationError("a market order takes no limit or stop price")


def transition(order: Order, to_status: str, *, at: datetime, reason: str | None = None) -> None:
    allowed = LEGAL_TRANSITIONS.get(order.status, frozenset())
    if to_status not in allowed:
        raise ValidationError(f"illegal order transition {order.status} -> {to_status}")

    order.status = to_status
    if to_status in OrderStatus.TERMINAL:
        order.closed_at = at
    if reason is not None:
        order.reject_reason = reason[:200]


def record_fill(order: Order, qty: Decimal, price: Decimal, *, at: datetime) -> None:
    """Advances filled quantity and the running average, then moves the status accordingly."""
    if qty <= ZERO:
        raise ValidationError("fill quantity must be positive")
    if qty > order.remaining_qty:
        raise ValidationError(
            f"fill of {qty} exceeds remaining {order.remaining_qty} on order {order.id}"
        )

    previous_notional = (order.avg_fill_price or ZERO) * order.filled_qty
    order.filled_qty = order.filled_qty + qty
    order.avg_fill_price = (previous_notional + (qty * price)) / order.filled_qty

    target = OrderStatus.FILLED if order.remaining_qty <= ZERO else OrderStatus.PARTIALLY_FILLED
    transition(order, target, at=at)


def is_expired(order: Order, now: datetime) -> bool:
    if order.time_in_force == TimeInForce.GTC:
        return False
    return order.expires_at is not None and now >= order.expires_at
