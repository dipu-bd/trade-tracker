from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradebot.broker.costs import CostModel
from tradebot.broker.matching import evaluate, stop_triggered
from tradebot.broker.orders import (
    is_expired,
    record_fill,
    transition,
    validate_request,
)
from tradebot.core.errors import ValidationError
from tradebot.db.models import Order, OrderStatus, OrderType, Portfolio, Side, TimeInForce
from tradebot.providers.base import Quote

NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def make_order(**kwargs: object) -> Order:
    defaults: dict[str, object] = {
        "portfolio_id": 1,
        "instrument_id": 1,
        "client_order_id": "c1",
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "time_in_force": TimeInForce.DAY,
        "qty": Decimal(10),
        "status": OrderStatus.ACCEPTED,
        "filled_qty": Decimal(0),
        "reserved_cash": Decimal(0),
    }
    return Order(**{**defaults, **kwargs})  # type: ignore[arg-type]


def quote(price: str, *, bid: str | None = None, ask: str | None = None) -> Quote:
    return Quote(
        symbol="AAA",
        price=Decimal(price),
        at=NOW,
        bid=Decimal(bid) if bid else None,
        ask=Decimal(ask) if ask else None,
    )


COSTS = CostModel(slippage_bps=Decimal(10), commission_bps=Decimal(5), min_commission=Decimal(1))


def test_slippage_moves_the_fill_against_the_trader() -> None:
    assert COSTS.fill_price(Decimal(100), Side.BUY) == Decimal("100.1")
    assert COSTS.fill_price(Decimal(100), Side.SELL) == Decimal("99.9")


def test_commission_respects_its_floor() -> None:
    assert COSTS.commission(Decimal(100)) == Decimal(1)
    assert COSTS.commission(Decimal(100_000)) == Decimal(50)


def test_a_reservation_covers_the_worst_plausible_fill() -> None:
    """Two open orders must not each pass a cash check and jointly overdraw."""
    reservation = COSTS.reservation(Decimal(10), Decimal(100))

    assert reservation > Decimal(1000)
    assert reservation == COSTS.buy_cost(Decimal(10), Decimal("100.1"))


@pytest.mark.parametrize(
    ("order_type", "limit", "stop", "ok"),
    [
        (OrderType.MARKET, None, None, True),
        (OrderType.LIMIT, Decimal(10), None, True),
        (OrderType.LIMIT, None, None, False),
        (OrderType.STOP, None, Decimal(10), True),
        (OrderType.STOP, None, None, False),
        (OrderType.STOP_LIMIT, Decimal(10), Decimal(11), True),
        (OrderType.STOP_LIMIT, Decimal(10), None, False),
        (OrderType.MARKET, Decimal(10), None, False),
    ],
)
def test_order_price_requirements(
    order_type: str, limit: Decimal | None, stop: Decimal | None, ok: bool
) -> None:
    call = lambda: validate_request(  # noqa: E731
        side=Side.BUY,
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        qty=Decimal(1),
        limit_price=limit,
        stop_price=stop,
        allow_fractional=False,
    )
    if ok:
        call()
    else:
        with pytest.raises(ValidationError):
            call()


def test_fractional_quantity_is_rejected_for_whole_unit_instruments() -> None:
    with pytest.raises(ValidationError, match="fractional"):
        validate_request(
            side=Side.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            qty=Decimal("1.5"),
            limit_price=None,
            stop_price=None,
            allow_fractional=False,
        )


def test_zero_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="positive"):
        validate_request(
            side=Side.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            qty=Decimal(0),
            limit_price=None,
            stop_price=None,
            allow_fractional=True,
        )


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (OrderStatus.NEW, OrderStatus.ACCEPTED),
        (OrderStatus.NEW, OrderStatus.REJECTED),
        (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.ACCEPTED, OrderStatus.FILLED),
        (OrderStatus.ACCEPTED, OrderStatus.CANCELED),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED),
    ],
)
def test_legal_transitions(start: str, target: str) -> None:
    order = make_order(status=start)
    transition(order, target, at=NOW)
    assert order.status == target


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (OrderStatus.FILLED, OrderStatus.CANCELED),
        (OrderStatus.CANCELED, OrderStatus.FILLED),
        (OrderStatus.REJECTED, OrderStatus.ACCEPTED),
        (OrderStatus.EXPIRED, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.NEW, OrderStatus.FILLED),
        (OrderStatus.ACCEPTED, OrderStatus.REJECTED),
    ],
)
def test_illegal_transitions_are_refused(start: str, target: str) -> None:
    order = make_order(status=start)
    with pytest.raises(ValidationError, match="illegal order transition"):
        transition(order, target, at=NOW)


def test_a_terminal_transition_stamps_closed_at() -> None:
    order = make_order()
    transition(order, OrderStatus.CANCELED, at=NOW)
    assert order.closed_at == NOW


def test_a_partial_fill_advances_the_average() -> None:
    order = make_order(qty=Decimal(10))

    record_fill(order, Decimal(4), Decimal(100), at=NOW)
    record_fill(order, Decimal(6), Decimal(110), at=NOW)

    assert order.filled_qty == Decimal(10)
    assert order.status == OrderStatus.FILLED
    assert order.avg_fill_price == Decimal(106)


def test_a_fill_beyond_the_remainder_is_refused() -> None:
    order = make_order(qty=Decimal(10), filled_qty=Decimal(8), status=OrderStatus.PARTIALLY_FILLED)

    with pytest.raises(ValidationError, match="exceeds remaining"):
        record_fill(order, Decimal(5), Decimal(100), at=NOW)


def test_gtc_never_expires() -> None:
    order = make_order(time_in_force=TimeInForce.GTC, expires_at=NOW)
    assert not is_expired(order, NOW)


def test_a_day_order_expires_at_its_deadline() -> None:
    order = make_order(time_in_force=TimeInForce.DAY, expires_at=NOW)
    assert is_expired(order, NOW)


def test_a_market_order_fills_at_the_far_side() -> None:
    buy = evaluate(make_order(), quote("100", bid="99.9", ask="100.1"), stop_armed=False)
    sell = evaluate(
        make_order(side=Side.SELL), quote("100", bid="99.9", ask="100.1"), stop_armed=False
    )

    assert buy.fills and buy.reference == Decimal("100.1")
    assert sell.fills and sell.reference == Decimal("99.9")


def test_a_buy_limit_fills_only_when_marketable() -> None:
    order = make_order(order_type=OrderType.LIMIT, limit_price=Decimal(100))

    assert not evaluate(order, quote("101", ask="101"), stop_armed=False).fills
    assert evaluate(order, quote("99", ask="99"), stop_armed=False).fills


def test_a_buy_limit_never_pays_above_its_limit() -> None:
    order = make_order(order_type=OrderType.LIMIT, limit_price=Decimal(100))

    trigger = evaluate(order, quote("98", ask="98"), stop_armed=False)

    assert trigger.reference == Decimal(98)


def test_a_sell_limit_fills_only_at_or_above_its_limit() -> None:
    order = make_order(side=Side.SELL, order_type=OrderType.LIMIT, limit_price=Decimal(100))

    assert not evaluate(order, quote("99", bid="99"), stop_armed=False).fills
    assert evaluate(order, quote("101", bid="101"), stop_armed=False).fills


def test_a_sell_stop_triggers_on_the_way_down() -> None:
    order = make_order(side=Side.SELL, order_type=OrderType.STOP, stop_price=Decimal(90))

    assert not stop_triggered(order, quote("95"))
    assert stop_triggered(order, quote("89"))


def test_a_buy_stop_triggers_on_the_way_up() -> None:
    order = make_order(order_type=OrderType.STOP, stop_price=Decimal(110))

    assert not stop_triggered(order, quote("105"))
    assert stop_triggered(order, quote("111"))


def test_an_untriggered_stop_does_not_fill() -> None:
    order = make_order(side=Side.SELL, order_type=OrderType.STOP, stop_price=Decimal(90))

    assert not evaluate(order, quote("95", bid="95"), stop_armed=False).fills


def test_a_stop_limit_stays_armed_across_ticks() -> None:
    """The stop and the limit need not be satisfiable on the same tick."""
    order = make_order(
        side=Side.SELL,
        order_type=OrderType.STOP_LIMIT,
        stop_price=Decimal(90),
        limit_price=Decimal(88),
    )

    first = evaluate(order, quote("89", bid="87"), stop_armed=False)
    second = evaluate(order, quote("89", bid="89"), stop_armed=True)

    assert not first.fills
    assert second.fills


def test_a_quote_without_a_price_never_fills() -> None:
    assert not evaluate(make_order(), quote("0"), stop_armed=False).fills


def test_cost_model_reads_portfolio_settings() -> None:
    portfolio = Portfolio(
        user_id=1,
        name="p",
        initial_capital=Decimal(1000),
        slippage_bps=Decimal(25),
        commission_bps=Decimal(10),
        min_commission=Decimal(2),
    )
    model = CostModel.of(portfolio)

    assert model.slippage_bps == Decimal(25)
    assert model.fill_price(Decimal(100), Side.BUY) == Decimal("100.25")
