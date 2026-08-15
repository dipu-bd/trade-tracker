from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt


@dataclass(frozen=True, slots=True)
class CostConfig:
    slippage_bps: float = 10.0
    commission_bps: float = 0.0
    min_commission: float = 0.0
    impact_coefficient: float = 0.1


@dataclass(frozen=True, slots=True)
class TurnoverConfig:
    no_trade_band: float = 0.02
    relative_band: float = 0.25
    monthly_turnover_cap: float = 2.0
    cooldown_days: int = 5
    max_cost_share_of_risk: float = 0.15


def round_trip_cost(config: CostConfig, participation: float = 0.0) -> float:
    """Round-trip frictions as a fraction of notional.

    The square-root impact term is Almgren's, and it matters here because a strategy that only
    works at zero cost must be reported as failing rather than as profitable.
    """
    linear = 2.0 * (config.slippage_bps + config.commission_bps) / 10_000.0
    impact = 2.0 * config.impact_coefficient * sqrt(max(participation, 0.0))
    return linear + impact


def passes_cost_gate(
    stop_distance: float, config: CostConfig, turnover: TurnoverConfig, participation: float = 0.0
) -> bool:
    """A trade must not spend a large share of its own risk budget on frictions.

    Framed against the stop distance rather than a forecast return, because a return forecast
    precise enough to compare with costs is exactly what we do not have.
    """
    if stop_distance <= 0:
        return False
    return round_trip_cost(config, participation) / stop_distance <= turnover.max_cost_share_of_risk


def needs_rebalance(current: float, target: float, config: TurnoverConfig | None = None) -> bool:
    """No-trade band: rebalance on drift, never continuously.

    Both an absolute and a relative band, so a 0.5% position is not churned by a 0.4% drift and
    a 30% position is not left far from target by one.
    """
    config = config or TurnoverConfig()
    drift = abs(target - current)
    if drift < config.no_trade_band:
        return False
    return not (current > 0 and drift / current < config.relative_band)


def in_cooldown(last_exit: date | None, today: date, config: TurnoverConfig | None = None) -> bool:
    config = config or TurnoverConfig()
    if last_exit is None:
        return False
    return today < last_exit + timedelta(days=config.cooldown_days)


@dataclass(frozen=True, slots=True)
class TurnoverBudget:
    """Traded notional against equity over a trailing month, as a fraction."""

    used: float
    cap: float

    @property
    def remaining(self) -> float:
        return max(0.0, self.cap - self.used)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining <= 0

    def admits(self, weight: float) -> bool:
        return weight <= self.remaining


def truncate_to_turnover(
    proposals: list[tuple[str, float]], budget: TurnoverBudget
) -> tuple[list[tuple[str, float]], list[str]]:
    """Take ranked proposals in order until the budget is spent; report what was dropped."""
    kept: list[tuple[str, float]] = []
    dropped: list[str] = []
    remaining = budget.remaining

    for symbol, weight in proposals:
        if weight <= remaining:
            kept.append((symbol, weight))
            remaining -= weight
        else:
            dropped.append(symbol)

    return kept, dropped
