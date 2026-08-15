from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from tradebot.analytics.features import Features
from tradebot.analytics.signals import TrendSignal


class ExitReason(StrEnum):
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    SIGNAL_LOST = "signal_lost"
    TIME_STOP = "time_stop"
    PROFIT_LADDER = "profit_ladder"
    REGIME = "regime"


@dataclass(frozen=True, slots=True)
class ExitConfig:
    atr_stop_multiple: float = 3.0
    trail_multiple: float = 3.0
    time_stop_days: int = 90
    time_stop_min_r: float = 0.5
    ladder_at_r: float = 2.0
    ladder_fraction: float = 0.33
    min_hold_days: int = 3


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    qty: float
    entry_price: float
    entry_date: date
    highest_close: float
    stop_price: float
    laddered: bool = False

    def r_multiple(self, close: float, stop_distance: float) -> float:
        """Profit measured in units of initial risk. Zero when risk was never defined."""
        if stop_distance <= 0:
            return 0.0
        return (close - self.entry_price) / (self.entry_price * stop_distance)


@dataclass(frozen=True, slots=True)
class ExitAction:
    symbol: str
    reason: ExitReason
    fraction: float
    detail: str

    @property
    def is_full(self) -> bool:
        return self.fraction >= 1.0


def update_stop(holding: Holding, features: Features, config: ExitConfig | None = None) -> float:
    """Chandelier trailing stop. Ratchets up only — a stop that can fall is not a stop."""
    config = config or ExitConfig()
    peak = max(holding.highest_close, features.close)
    trailing = peak - config.trail_multiple * features.atr_14
    return max(holding.stop_price, trailing)


def evaluate_exit(
    holding: Holding,
    features: Features,
    trend: TrendSignal,
    today: date,
    risk_off: bool = False,
    config: ExitConfig | None = None,
) -> ExitAction | None:
    """The exit ladder, in priority order. Protective exits ignore the minimum hold."""
    config = config or ExitConfig()
    close = features.close
    held_days = (today - holding.entry_date).days

    if close <= holding.stop_price:
        reason = (
            ExitReason.TRAILING_STOP
            if holding.highest_close > holding.entry_price
            else ExitReason.STOP_LOSS
        )
        return ExitAction(
            holding.symbol, reason, 1.0, f"close {close:.4f} <= stop {holding.stop_price:.4f}"
        )

    if held_days < config.min_hold_days:
        return None

    if risk_off:
        return ExitAction(holding.symbol, ExitReason.REGIME, 1.0, "risk-off regime")

    if not trend.is_long:
        return ExitAction(
            holding.symbol, ExitReason.SIGNAL_LOST, 1.0, f"trend score {trend.score:+.2f}"
        )

    stop_distance = config.atr_stop_multiple * features.atr_pct
    r = holding.r_multiple(close, stop_distance)

    if not holding.laddered and r >= config.ladder_at_r:
        return ExitAction(
            holding.symbol,
            ExitReason.PROFIT_LADDER,
            config.ladder_fraction,
            f"+{r:.1f}R, trimming {config.ladder_fraction:.0%}",
        )

    if held_days >= config.time_stop_days and r < config.time_stop_min_r:
        return ExitAction(
            holding.symbol,
            ExitReason.TIME_STOP,
            1.0,
            f"{held_days}d held at {r:+.1f}R, below {config.time_stop_min_r}R",
        )

    return None
