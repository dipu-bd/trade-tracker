from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

CASH = Decimal("0.0000000001")
PRICE = Decimal("0.00000001")
QTY = Decimal("0.0000000001")

ZERO = Decimal(0)


def to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, (int, str)):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal: {value!r}") from exc
    raise TypeError(f"cannot convert {type(value).__name__} to Decimal")


def quantize_cash(value: Decimal) -> Decimal:
    return value.quantize(CASH, rounding=ROUND_HALF_UP)


def quantize_price(value: Decimal) -> Decimal:
    return value.quantize(PRICE, rounding=ROUND_HALF_UP)


def quantize_qty(value: Decimal, *, whole_units: bool = False) -> Decimal:
    if whole_units:
        return value.quantize(Decimal(1), rounding=ROUND_DOWN)
    return value.quantize(QTY, rounding=ROUND_DOWN)


def bps(value: Decimal, basis_points: Decimal) -> Decimal:
    return value * basis_points / Decimal(10_000)
