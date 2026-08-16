from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from math import isfinite, sqrt
from statistics import fmean, pstdev

from tradebot.analytics.indicators import TRADING_DAYS, max_drawdown


@dataclass(frozen=True, slots=True)
class TradeResult:
    symbol: str
    holding_days: int
    return_pct: float
    r_multiple: float


@dataclass(frozen=True, slots=True)
class Performance:
    periods: int
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    drawdown_days: int
    volatility: float

    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    average_r: float = 0.0
    exposure: float = 0.0
    turnover: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "periods": self.periods,
            "total_return": round(self.total_return, 6),
            "cagr": round(self.cagr, 6),
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "calmar": round(self.calmar, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "drawdown_days": self.drawdown_days,
            "volatility": round(self.volatility, 6),
            "trades": self.trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "expectancy": round(self.expectancy, 6),
            "average_r": round(self.average_r, 4),
            "exposure": round(self.exposure, 4),
            "turnover": round(self.turnover, 4),
        }


def simple_returns(equity: Sequence[float]) -> list[float]:
    out: list[float] = []
    for previous, current in pairwise(equity):
        out.append(0.0 if previous <= 0 else current / previous - 1.0)
    return out


def sharpe(returns: Sequence[float], periods_per_year: int = TRADING_DAYS) -> float:
    """Annualized Sharpe. Zero when there is no dispersion to divide by.

    Never a risk-free adjustment: a paper portfolio has no financing leg, and subtracting a rate
    we did not pay would flatter the number.
    """
    if len(returns) < 2:
        return 0.0
    deviation = pstdev(returns)
    if deviation == 0:
        return 0.0
    return fmean(returns) / deviation * sqrt(periods_per_year)


def sortino(returns: Sequence[float], periods_per_year: int = TRADING_DAYS) -> float:
    """Sharpe's downside-only sibling: upside dispersion is not risk."""
    if len(returns) < 2:
        return 0.0
    downside = [value for value in returns if value < 0]
    if not downside:
        return 0.0
    deviation = sqrt(fmean([value * value for value in downside]))
    if deviation == 0:
        return 0.0
    return fmean(returns) / deviation * sqrt(periods_per_year)


def cagr(equity: Sequence[float], periods_per_year: int = TRADING_DAYS) -> float:
    if len(equity) < 2 or equity[0] <= 0 or equity[-1] <= 0:
        return 0.0
    years = (len(equity) - 1) / periods_per_year
    if years <= 0:
        return 0.0
    growth: float = (equity[-1] / equity[0]) ** (1 / years) - 1.0
    return growth


def drawdown_duration(equity: Sequence[float]) -> int:
    """Longest run of periods spent below a previous peak.

    Reported next to depth because a 20% drawdown recovered in a month and one that lasts three
    years are different products, and only one of them gets held through.
    """
    longest = 0
    current = 0
    peak = float("-inf")
    for value in equity:
        if value >= peak:
            peak = value
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def trade_stats(trades: Sequence[TradeResult]) -> dict[str, float]:
    if not trades:
        return {"win_rate": 0.0, "profit_factor": 0.0, "expectancy": 0.0, "average_r": 0.0}

    wins = [item.return_pct for item in trades if item.return_pct > 0]
    losses = [item.return_pct for item in trades if item.return_pct < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "win_rate": len(wins) / len(trades),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else 0.0,
        "expectancy": fmean([item.return_pct for item in trades]),
        "average_r": fmean([item.r_multiple for item in trades]),
    }


def evaluate(
    equity: Sequence[float],
    trades: Sequence[TradeResult] = (),
    *,
    periods_per_year: int = TRADING_DAYS,
    exposure: float = 0.0,
    turnover: float = 0.0,
) -> Performance:
    """Every headline number for one equity curve, computed the same way for every strategy."""
    if len(equity) < 2:
        return Performance(
            periods=len(equity),
            total_return=0.0,
            cagr=0.0,
            sharpe=0.0,
            sortino=0.0,
            calmar=0.0,
            max_drawdown=0.0,
            drawdown_days=0,
            volatility=0.0,
        )

    returns = simple_returns(equity)
    depth = max_drawdown(equity)
    growth = cagr(equity, periods_per_year)
    stats = trade_stats(trades)

    return Performance(
        periods=len(equity),
        total_return=equity[-1] / equity[0] - 1.0 if equity[0] > 0 else 0.0,
        cagr=growth,
        sharpe=sharpe(returns, periods_per_year),
        sortino=sortino(returns, periods_per_year),
        calmar=growth / depth if depth > 0 else 0.0,
        max_drawdown=depth,
        drawdown_days=drawdown_duration(equity),
        volatility=pstdev(returns) * sqrt(periods_per_year) if len(returns) > 1 else 0.0,
        trades=len(trades),
        win_rate=stats["win_rate"],
        profit_factor=stats["profit_factor"],
        expectancy=stats["expectancy"],
        average_r=stats["average_r"],
        exposure=exposure,
        turnover=turnover,
    )


def information_coefficient(scores: Sequence[float], forward: Sequence[float]) -> float:
    """Spearman rank correlation between a score and the return that followed it.

    Rank rather than Pearson because the question is whether the ordering was right, and one
    outlier return should not decide whether a signal is judged to work.
    """
    if len(scores) != len(forward) or len(scores) < 3:
        return 0.0

    ranked_scores = _ranks(scores)
    ranked_forward = _ranks(forward)

    mean_a = fmean(ranked_scores)
    mean_b = fmean(ranked_forward)
    covariance = sum(
        (a - mean_a) * (b - mean_b) for a, b in zip(ranked_scores, ranked_forward, strict=True)
    )
    spread_a = sqrt(sum((a - mean_a) ** 2 for a in ranked_scores))
    spread_b = sqrt(sum((b - mean_b) ** 2 for b in ranked_forward))

    if spread_a == 0 or spread_b == 0:
        return 0.0
    value = covariance / (spread_a * spread_b)
    return value if isfinite(value) else 0.0


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2 + 1
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks
