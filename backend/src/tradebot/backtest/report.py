from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tradebot.backtest.ic import SignalQuality
from tradebot.backtest.metrics import Performance, simple_returns
from tradebot.backtest.runner import ReplayResult
from tradebot.backtest.statistics import DeflatedSharpe, deflated_sharpe


@dataclass
class StrategyReport:
    label: str
    performance: Performance
    deflated: DeflatedSharpe
    orders: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "orders": self.orders,
            "error": self.error,
            **self.performance.as_dict(),
            "deflated": self.deflated.as_dict(),
        }


@dataclass
class BacktestReport:
    """The answer to "does this actually beat holding an index fund?", stated plainly.

    The verdict is computed from the numbers rather than written alongside them, so a bad result
    cannot be softened into a caveat under a good-looking curve.
    """

    start: date | None = None
    end: date | None = None
    trials: int = 1
    strategies: list[StrategyReport] = field(default_factory=list)
    benchmark: StrategyReport | None = None
    control: StrategyReport | None = None
    signals: list[SignalQuality] = field(default_factory=list)
    leakage: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def headline(self) -> StrategyReport | None:
        return self.strategies[0] if self.strategies else None

    def verdict(self) -> str:
        best = self.headline
        if best is None:
            return "No strategy was run."
        if best.error:
            return f"The run failed: {best.error}"
        if best.orders == 0:
            return "The strategy placed no orders. There is nothing to evaluate."

        lines: list[str] = []

        if self.benchmark is not None and self.benchmark.error is None:
            gap = best.performance.total_return - self.benchmark.performance.total_return
            if gap <= 0:
                lines.append(
                    f"It did NOT beat {self.benchmark.label}: "
                    f"{best.performance.total_return:+.2%} against "
                    f"{self.benchmark.performance.total_return:+.2%} "
                    f"({gap:+.2%}). On this window an index fund was the better choice."
                )
            else:
                lines.append(
                    f"It beat {self.benchmark.label} by {gap:+.2%} "
                    f"({best.performance.total_return:+.2%} against "
                    f"{self.benchmark.performance.total_return:+.2%})."
                )

        if not best.deflated.is_significant:
            lines.append(
                f"The Sharpe of {best.performance.sharpe:.2f} does NOT survive deflation for "
                f"{best.deflated.trials} configuration(s) tried "
                f"(probability {best.deflated.probability:.2f}, needs 0.95). "
                "Treat it as indistinguishable from luck."
            )
        else:
            lines.append(
                f"The Sharpe of {best.performance.sharpe:.2f} survives deflation for "
                f"{best.deflated.trials} configuration(s) tried."
            )

        if self.control is not None and self.control.error is None:
            edge = best.performance.total_return - self.control.performance.total_return
            if edge <= 0:
                lines.append(
                    f"It did not beat a random-entry control ({edge:+.2%}). "
                    "The result is consistent with drift rather than signal."
                )

        for signal in self.signals:
            lines.append(f"{signal.name}: {signal.verdict()}")

        if self.leakage.get("checked"):
            gap = float(self.leakage.get("gap", 0.0))
            if abs(gap) > 0.5:
                lines.append(
                    f"LEAKAGE WARNING: performance differs by {gap:+.2%} across the model's "
                    "training cutoff. Treat the pre-cutoff result as invalid."
                )

        return " ".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "trials": self.trials,
            "verdict": self.verdict(),
            "strategies": [item.as_dict() for item in self.strategies],
            "benchmark": self.benchmark.as_dict() if self.benchmark else None,
            "control": self.control.as_dict() if self.control else None,
            "signals": [item.as_dict() for item in self.signals],
            "leakage": self.leakage,
            "notes": self.notes,
        }


def summarise(result: ReplayResult, trials: int, periods_per_year: int = 252) -> StrategyReport:
    performance = result.performance(periods_per_year)
    returns = simple_returns(result.equity)
    return StrategyReport(
        label=result.label,
        performance=performance,
        deflated=deflated_sharpe(returns, performance.sharpe, trials),
        orders=result.orders,
        error=result.error,
    )


def leakage_split(before: ReplayResult, after: ReplayResult, cutoff: date) -> dict[str, Any]:
    """The pre/post training-cutoff comparison, reported rather than buried.

    A model asked about a window inside its own training data may simply recall what happened.
    A large gap is a leakage signal, not a strategy result, and a pre-cutoff-only run is invalid.
    """
    pre = before.performance().total_return
    post = after.performance().total_return

    return {
        "checked": True,
        "cutoff": cutoff.isoformat(),
        "pre_cutoff_return": round(pre, 6),
        "post_cutoff_return": round(post, 6),
        "gap": round(pre - post, 6),
        "valid": bool(after.equity),
        "note": (
            "A pre-cutoff result alone is treated as invalid; only the post-cutoff window is "
            "evidence about future behaviour."
        ),
    }
