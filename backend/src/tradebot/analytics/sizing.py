from dataclasses import dataclass

from tradebot.analytics.features import Features
from tradebot.analytics.signals import Regime, vol_scalar


@dataclass(frozen=True, slots=True)
class SizingConfig:
    target_vol: float = 0.20
    risk_per_trade: float = 0.01
    atr_stop_multiple: float = 3.0
    kelly_fraction: float = 0.5
    max_position_weight: float = 0.15
    max_gross_exposure: float = 1.00
    max_positions: int = 12
    min_position_weight: float = 0.005
    max_vol_scalar: float = 1.5

    # Ablation seams. Defaults leave the strategy exactly as it trades; flipping one off is how
    # the backtester asks which component is actually producing the return.
    vol_scaling: bool = True
    unscaled_weight: float = 1.0


@dataclass(frozen=True, slots=True)
class Sizing:
    symbol: str
    weight: float
    vol_weight: float
    atr_cap: float
    stop_distance: float
    binding: str

    @property
    def is_actionable(self) -> bool:
        return self.weight > 0


def kelly_fraction_from(win_rate: float, payoff_ratio: float) -> float:
    """f* = W - (1-W)/R, floored at zero.

    Exposed for the backtester to feed measured statistics back in; the live sizer does not
    call it, because an edge estimated from the same data that generated the trades is the
    definition of an overfitted parameter.
    """
    if payoff_ratio <= 0:
        return 0.0
    return max(0.0, win_rate - (1.0 - win_rate) / payoff_ratio)


def size_position(
    features: Features,
    regime: Regime,
    confidence: float = 1.0,
    config: SizingConfig | None = None,
) -> Sizing:
    """Target portfolio weight for one candidate.

    Kelly is applied as a discipline rather than a computed f*: with no trustworthy per-trade
    edge estimate, deliberately sizing below the growth-optimal point is the defensible move,
    and pretending to derive f* from an unmeasurable edge would be worse than not claiming it.

    Confidence and the regime scale the weight *after* the caps rather than before. Folded in
    beforehand they are swallowed whenever a cap binds — which is most of the time — and the
    meta-labeler's confidence would stop mapping to size at all.
    """
    config = config or SizingConfig()
    confidence = max(0.0, min(confidence, 1.0))

    if config.vol_scaling:
        vol_weight = vol_scalar(features, config.target_vol, config.max_vol_scalar)
    else:
        vol_weight = config.unscaled_weight
    stop_distance = config.atr_stop_multiple * features.atr_pct

    atr_cap = 0.0 if stop_distance <= 0 else config.risk_per_trade / stop_distance

    desired = config.kelly_fraction * vol_weight
    capped = min(desired, atr_cap, config.max_position_weight)
    weight = capped * regime.exposure * confidence

    if weight < config.min_position_weight:
        weight = 0.0

    binding = _binding(desired, atr_cap, config.max_position_weight, regime.exposure, confidence)
    return Sizing(features.symbol, weight, vol_weight, atr_cap, stop_distance, binding)


def _binding(
    desired: float, atr_cap: float, position_cap: float, exposure: float, confidence: float
) -> str:
    if confidence < 1.0:
        return "confidence"
    if exposure < 1.0 and min(desired, atr_cap, position_cap) > 0:
        return "regime"
    smallest = min(desired, atr_cap, position_cap)
    if smallest == atr_cap:
        return "atr_risk"
    if smallest == position_cap:
        return "position_cap"
    return "vol_target"


def fit_to_budget(
    sizings: list[Sizing], available_weight: float, config: SizingConfig | None = None
) -> list[Sizing]:
    """Truncate and scale a ranked candidate list so the book stays inside gross exposure.

    Ranked order is preserved and the tail is dropped rather than everything shrunk, because
    shrinking every position to fit produces a book of positions too small to matter.
    """
    config = config or SizingConfig()
    room = max(0.0, min(available_weight, config.max_gross_exposure))

    kept: list[Sizing] = []
    used = 0.0
    for sizing in sizings:
        if len(kept) >= config.max_positions:
            break
        if used + sizing.weight > room:
            remaining = room - used
            if remaining < config.min_position_weight:
                break
            kept.append(_with_weight(sizing, remaining))
            used = room
            break
        kept.append(sizing)
        used += sizing.weight

    return kept


def _with_weight(sizing: Sizing, weight: float) -> Sizing:
    return Sizing(
        symbol=sizing.symbol,
        weight=weight,
        vol_weight=sizing.vol_weight,
        atr_cap=sizing.atr_cap,
        stop_distance=sizing.stop_distance,
        binding="gross_exposure",
    )
