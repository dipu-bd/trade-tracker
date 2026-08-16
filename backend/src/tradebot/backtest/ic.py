from dataclasses import dataclass
from statistics import fmean

from tradebot.backtest.metrics import information_coefficient
from tradebot.backtest.statistics import t_statistic

MIN_OBSERVATIONS = 20
SIGNIFICANT_T = 2.0


@dataclass(frozen=True, slots=True)
class SignalQuality:
    """How well one score ordered the returns that followed it."""

    name: str
    observations: int
    mean_ic: float
    t_stat: float
    windows: int

    @property
    def is_reliable(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS and self.t_stat >= SIGNIFICANT_T

    @property
    def weight(self) -> float:
        """Influence this signal has earned, in [0, 1].

        Decays toward zero when the out-of-sample IC is not reliably positive. This is the
        feature that most distinguishes the project from the prior art: it is built to say when
        the model is not working, rather than to produce a flattering curve.
        """
        if self.observations < MIN_OBSERVATIONS:
            return 0.0
        if self.mean_ic <= 0:
            return 0.0
        return min(1.0, max(0.0, self.t_stat / SIGNIFICANT_T))

    def as_dict(self) -> dict[str, float | int | bool | str]:
        return {
            "name": self.name,
            "observations": self.observations,
            "mean_ic": round(self.mean_ic, 4),
            "t_stat": round(self.t_stat, 4),
            "windows": self.windows,
            "reliable": self.is_reliable,
            "weight": round(self.weight, 4),
        }

    def verdict(self) -> str:
        if self.observations < MIN_OBSERVATIONS:
            return f"{self.observations} observations is too few to judge; influence held at zero"
        if self.mean_ic <= 0:
            return "out-of-sample IC is not positive; influence decayed to zero"
        if not self.is_reliable:
            return f"IC {self.mean_ic:+.3f} is positive but not distinguishable from noise"
        return f"IC {self.mean_ic:+.3f} (t={self.t_stat:.2f}) is reliably positive"


def rolling_ic(scores: list[float], forward: list[float], window: int = 20) -> list[float]:
    """IC measured in windows rather than once, because an edge that decayed is not an edge.

    The documented LLM news edge is expected to decay as adoption rises, so a single full-sample
    number would keep reporting an advantage long after it had gone.
    """
    if len(scores) != len(forward) or len(scores) < window:
        return []

    return [
        information_coefficient(scores[start : start + window], forward[start : start + window])
        for start in range(len(scores) - window + 1)
    ]


def assess(name: str, scores: list[float], forward: list[float], window: int = 20) -> SignalQuality:
    series = rolling_ic(scores, forward, window)
    if not series:
        overall = information_coefficient(scores, forward)
        return SignalQuality(name, len(scores), overall, 0.0, 0)

    return SignalQuality(
        name=name,
        observations=len(scores),
        mean_ic=fmean(series),
        t_stat=t_statistic(series),
        windows=len(series),
    )


def apply_deweighting(confidence: dict[str, float], quality: SignalQuality) -> dict[str, float]:
    """Scale the model's confidence by the influence it has earned.

    Applied to confidence rather than to the rules, so a failing AI layer degrades the system
    back toward rules-only rather than breaking it.
    """
    weight = quality.weight
    return {symbol: value * weight for symbol, value in confidence.items()}
