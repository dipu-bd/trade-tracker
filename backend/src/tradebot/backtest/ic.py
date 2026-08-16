from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import TYPE_CHECKING

from tradebot.backtest.metrics import information_coefficient
from tradebot.backtest.statistics import t_statistic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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
    def is_warming_up(self) -> bool:
        return self.observations < MIN_OBSERVATIONS

    @property
    def weight(self) -> float:
        """Influence this signal has earned, in [0, 1].

        Decays toward zero once the out-of-sample IC is measurably not positive. This is the
        feature that most distinguishes the project from the prior art: it is built to say when
        the model is not working, rather than to produce a flattering curve.

        Absence of evidence is not evidence of failure, so a signal with too little history
        passes through unchanged. Withholding influence during warm-up would be self-defeating:
        zero confidence opens no positions, and no positions generate no evidence, so the signal
        could never earn back the influence it was never given.
        """
        if self.is_warming_up:
            return 1.0
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
            "warming_up": self.is_warming_up,
            "weight": round(self.weight, 4),
        }

    def verdict(self) -> str:
        if self.is_warming_up:
            return (
                f"{self.observations} observations is too few to judge; "
                f"influence unchanged until {MIN_OBSERVATIONS}"
            )
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


async def measure_from_decisions(
    session: "AsyncSession",
    portfolio_id: int,
    *,
    horizon: int = 21,
    name: str = "llm_confidence",
) -> SignalQuality:
    """Score the model's own past confidence against what the market did next.

    Reads the confidence maps the guardrails already recorded on each decision run, pairs each
    with the instrument's forward return over `horizon` sessions, and judges the pairing. This is
    the loop that lets the system discover its AI is not working without anyone asking it to.
    """
    from sqlalchemy import select

    from tradebot.db.models import DecisionRun, Instrument, PriceBar

    runs = await session.scalars(
        select(DecisionRun)
        .where(DecisionRun.portfolio_id == portfolio_id, DecisionRun.status == "ok")
        .order_by(DecisionRun.id)
    )

    pairs: list[tuple[float, float]] = []
    closes: dict[str, list[tuple[date, float]]] = {}

    for run in runs:
        confidence = ((run.detail or {}).get("ai") or {}).get("confidence") or {}
        for symbol, score in confidence.items():
            if symbol not in closes:
                rows = await session.execute(
                    select(PriceBar.bar_date, PriceBar.close)
                    .join(Instrument, PriceBar.instrument_id == Instrument.id)
                    .where(Instrument.symbol == symbol)
                    .order_by(PriceBar.bar_date)
                )
                closes[symbol] = [(row[0], float(row[1])) for row in rows.all()]

            series = closes[symbol]
            start = next((i for i, (day, _) in enumerate(series) if day >= run.as_of), None)
            if start is None or start + horizon >= len(series):
                continue

            entry = series[start][1]
            exit_price = series[start + horizon][1]
            if entry <= 0:
                continue
            pairs.append((float(score), exit_price / entry - 1.0))

    if not pairs:
        return SignalQuality(name, 0, 0.0, 0.0, 0)

    scores = [item[0] for item in pairs]
    forward = [item[1] for item in pairs]
    return assess(name, scores, forward, window=min(MIN_OBSERVATIONS, max(3, len(pairs) // 4)))
