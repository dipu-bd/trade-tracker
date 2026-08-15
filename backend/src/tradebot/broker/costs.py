from dataclasses import dataclass
from decimal import Decimal

from tradebot.core.money import bps, quantize_cash, quantize_price
from tradebot.db.models import Portfolio, Side

ZERO = Decimal(0)


@dataclass(frozen=True)
class CostModel:
    """Slippage moves the fill against you; commission is charged on the notional."""

    slippage_bps: Decimal
    commission_bps: Decimal
    min_commission: Decimal

    @classmethod
    def of(cls, portfolio: Portfolio) -> "CostModel":
        return cls(
            slippage_bps=Decimal(portfolio.slippage_bps),
            commission_bps=Decimal(portfolio.commission_bps),
            min_commission=Decimal(portfolio.min_commission),
        )

    def fill_price(self, reference: Decimal, side: str) -> Decimal:
        drift = bps(reference, self.slippage_bps)
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
