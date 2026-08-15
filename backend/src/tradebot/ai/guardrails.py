from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from tradebot.ai.schema import Verdict
from tradebot.analytics.policy import TurnoverConfig, in_cooldown
from tradebot.analytics.sizing import SizingConfig
from tradebot.engine.strategy import Decision, PortfolioState


class ClampReason(StrEnum):
    UNKNOWN_SYMBOL = "unknown_symbol"
    NOT_A_CANDIDATE = "not_a_candidate"
    PROTECTIVE_EXIT = "protective_exit"
    CONFIDENCE_RANGE = "confidence_range"
    WEIGHT_RANGE = "weight_range"
    WEIGHT_INCREASE = "weight_increase"
    DUPLICATE = "duplicate"
    COOLDOWN = "cooldown"
    BREAKER = "breaker"
    POSITION_LIMIT = "position_limit"
    CASH_FLOOR = "cash_floor"


@dataclass(frozen=True, slots=True)
class Clamp:
    symbol: str
    reason: ClampReason
    asked: str
    applied: str

    def as_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "reason": self.reason.value,
            "asked": self.asked[:200],
            "applied": self.applied[:200],
        }


@dataclass(frozen=True, slots=True)
class GuardrailConfig:
    max_position_weight: float = 0.15
    cash_floor: float = 0.02
    max_positions: int = 12
    daily_loss_breaker: float = 0.05


@dataclass
class GuardrailResult:
    """What the model asked for, what survived, and every difference between them."""

    confidence: dict[str, float] = field(default_factory=dict)
    weight_cap: dict[str, float] = field(default_factory=dict)
    theses: dict[str, str] = field(default_factory=dict)
    clamps: list[Clamp] = field(default_factory=list)
    accepted: int = 0

    @property
    def diff(self) -> list[dict[str, str]]:
        return [clamp.as_dict() for clamp in self.clamps]

    @property
    def clean(self) -> bool:
        return not self.clamps


def apply(
    verdicts: list[Verdict],
    decision: Decision,
    state: PortfolioState,
    as_of: date,
    config: GuardrailConfig | None = None,
    sizing: SizingConfig | None = None,
    turnover: TurnoverConfig | None = None,
    daily_loss: float = 0.0,
) -> GuardrailResult:
    """Clamp untrusted model output down to what the rules already permitted.

    The model is a meta-labeler: every candidate it may speak about was proposed by the
    deterministic layer first, and everything it can say only reduces exposure. A verdict naming
    anything else is dropped rather than argued with, because a model that can add a symbol is a
    stock picker, which is the design this project explicitly rejects.
    """
    config = config or GuardrailConfig()
    sizing = sizing or SizingConfig()
    turnover = turnover or TurnoverConfig()

    result = GuardrailResult()
    proposed = {entry.symbol: entry for entry in decision.entries}
    exiting = {action.symbol for action in decision.exits}
    breaker_tripped = daily_loss >= config.daily_loss_breaker
    seen: set[str] = set()

    for verdict in verdicts:
        symbol = verdict.symbol.strip().upper()

        if not symbol:
            result.clamps.append(Clamp("", ClampReason.UNKNOWN_SYMBOL, verdict.symbol, "dropped"))
            continue

        if symbol in exiting:
            result.clamps.append(
                Clamp(
                    symbol,
                    ClampReason.PROTECTIVE_EXIT,
                    f"take={verdict.take}",
                    "ignored, the exit stands",
                )
            )
            continue

        if symbol not in proposed:
            held = symbol in state.holdings
            reason = ClampReason.NOT_A_CANDIDATE if held else ClampReason.UNKNOWN_SYMBOL
            result.clamps.append(Clamp(symbol, reason, f"take={verdict.take}", "dropped"))
            continue

        if symbol in seen:
            result.clamps.append(Clamp(symbol, ClampReason.DUPLICATE, "second verdict", "dropped"))
            continue
        seen.add(symbol)

        confidence = _confidence(verdict, result)

        if breaker_tripped and confidence > 0:
            result.clamps.append(
                Clamp(
                    symbol,
                    ClampReason.BREAKER,
                    f"confidence={confidence:.2f}",
                    "0.0, daily-loss breaker tripped",
                )
            )
            confidence = 0.0

        if confidence > 0 and in_cooldown(state.last_exit.get(symbol), as_of, turnover):
            result.clamps.append(
                Clamp(symbol, ClampReason.COOLDOWN, f"confidence={confidence:.2f}", "0.0")
            )
            confidence = 0.0

        at_limit = len(state.holdings) >= config.max_positions and symbol not in state.holdings
        if confidence > 0 and at_limit:
            result.clamps.append(
                Clamp(
                    symbol,
                    ClampReason.POSITION_LIMIT,
                    f"confidence={confidence:.2f}",
                    f"0.0, already holding {len(state.holdings)}",
                )
            )
            confidence = 0.0

        result.confidence[symbol] = confidence
        if verdict.thesis:
            result.theses[symbol] = verdict.thesis[:600]

        cap = _weight_cap(verdict, proposed[symbol].target_weight, config, result)
        if cap is not None:
            result.weight_cap[symbol] = cap

        if confidence > 0:
            result.accepted += 1

    for symbol in proposed:
        if symbol not in result.confidence:
            result.confidence[symbol] = 0.0

    if state.cash / state.equity < config.cash_floor and state.equity > 0:
        for symbol in list(result.confidence):
            if result.confidence[symbol] > 0:
                result.clamps.append(
                    Clamp(symbol, ClampReason.CASH_FLOOR, "entry", "0.0, cash floor reached")
                )
                result.confidence[symbol] = 0.0
        result.accepted = 0

    return result


def _confidence(verdict: Verdict, result: GuardrailResult) -> float:
    if not verdict.take:
        return 0.0

    raw = verdict.confidence
    if raw != raw or raw in (float("inf"), float("-inf")):
        result.clamps.append(
            Clamp(verdict.symbol.upper(), ClampReason.CONFIDENCE_RANGE, str(raw), "0.0")
        )
        return 0.0

    clamped = max(0.0, min(raw, 1.0))
    if clamped != raw:
        result.clamps.append(
            Clamp(
                verdict.symbol.upper(),
                ClampReason.CONFIDENCE_RANGE,
                f"{raw:.4f}",
                f"{clamped:.4f}",
            )
        )
    return clamped


def _weight_cap(
    verdict: Verdict, rules_weight: float, config: GuardrailConfig, result: GuardrailResult
) -> float | None:
    """A volunteered weight is accepted only as a lower cap, never as a raise."""
    if verdict.weight is None:
        return None

    symbol = verdict.symbol.upper()
    asked = verdict.weight

    if asked != asked or asked < 0 or asked > 1:
        result.clamps.append(
            Clamp(symbol, ClampReason.WEIGHT_RANGE, f"{asked}", "ignored, outside 0-1")
        )
        return None

    ceiling = min(rules_weight, config.max_position_weight)
    if asked > ceiling:
        result.clamps.append(
            Clamp(
                symbol,
                ClampReason.WEIGHT_INCREASE,
                f"{asked:.4f}",
                f"{ceiling:.4f}, the model may only reduce",
            )
        )
        return ceiling

    return asked
