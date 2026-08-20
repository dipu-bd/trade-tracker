from dataclasses import dataclass, replace
from decimal import Decimal

from tradebot.core.money import QTY, bps, quantize_cash, quantize_price, quantize_qty
from tradebot.db.models import Portfolio, Side

ZERO = Decimal(0)


@dataclass(frozen=True)
class CostModel:
    """Slippage moves the fill against you; commission is charged on the notional."""

    slippage_bps: Decimal
    commission_bps: Decimal
    min_commission: Decimal
    impact_coefficient: Decimal = Decimal("0.1")
    participation: Decimal = ZERO

    @classmethod
    def of(cls, portfolio: Portfolio) -> "CostModel":
        return cls(
            slippage_bps=Decimal(portfolio.slippage_bps),
            commission_bps=Decimal(portfolio.commission_bps),
            min_commission=Decimal(portfolio.min_commission),
            impact_coefficient=Decimal(
                str((portfolio.strategy or {}).get("impact_coefficient", 0.1))
            ),
        )

    def at_participation(self, participation: Decimal) -> "CostModel":
        """The same model, told how large this order is against the instrument's daily volume."""
        return replace(self, participation=max(participation, ZERO))

    @property
    def impact_bps(self) -> Decimal:
        """Almgren's square-root law: impact grows with the root of participation.

        A flat slippage guess prices a $1k order and a $10m order in the same instrument
        identically, which is the assumption that makes an illiquid backtest look tradable.
        """
        if self.participation <= ZERO:
            return ZERO
        root = self.participation.sqrt()
        return quantize_price(self.impact_coefficient * root * Decimal(10_000))

    def fill_price(self, reference: Decimal, side: str) -> Decimal:
        drift = bps(reference, self.slippage_bps + self.impact_bps)
        moved = reference + drift if side == Side.BUY else reference - drift
        return quantize_price(max(moved, Decimal("0.00000001")))

    def slippage_amount(self, reference: Decimal, fill: Decimal, qty: Decimal) -> Decimal:
        return quantize_cash(abs(fill - reference) * qty)

    def commission(self, notional: Decimal) -> Decimal:
        charged = bps(abs(notional), self.commission_bps)
        return quantize_cash(max(charged, self.min_commission) if notional else ZERO)

    def buy_cost(self, qty: Decimal, price: Decimal) -> Decimal:
        notional = qty * price
        return quantize_cash(notional + self.commission(notional))

    def reservation(self, qty: Decimal, reference: Decimal) -> Decimal:
        """What to hold against buying power before the fill price is known.

        Reserved at the worst plausible fill so two open orders cannot each pass a cash check
        and jointly overdraw the account.
        """
        worst = self.fill_price(reference, Side.BUY)
        return self.buy_cost(qty, worst)

    def affordable_qty(
        self, budget: Decimal, reference: Decimal, *, whole_units: bool = False
    ) -> Decimal:
        """The largest quantity whose `reservation` still fits inside `budget`.

        Solved rather than searched. The commission is `max(notional * rate, minimum)`, so a
        notional only clears the budget when it leaves room for *both* terms; the binding one
        is whichever leaves less. Sizing that ignores the charge entirely is what had a cycle
        ask for its whole cash balance and then get rejected for the commission on top of it,
        by exactly one minimum commission, on every pass.
        """
        # `fill_price` floors at one price tick, so a missing or zero reference would otherwise
        # come back as a position of billions rather than as nothing to size.
        if reference <= ZERO or budget <= ZERO:
            return ZERO

        worst = self.fill_price(reference, Side.BUY)
        rate = self.commission_bps / Decimal(10_000)
        notional = min(budget / (Decimal(1) + rate), budget - self.min_commission)
        if notional <= ZERO:
            return ZERO

        step = Decimal(1) if whole_units else QTY
        qty = quantize_qty(notional / worst, whole_units=whole_units)
        # Quantization already rounds down, so this corrects for the half-up rounding inside
        # `buy_cost` rather than searching: it is a guard, not the algorithm.
        while qty > ZERO and self.buy_cost(qty, worst) > budget:
            qty = quantize_qty(qty - step, whole_units=whole_units)
        return max(qty, ZERO)
