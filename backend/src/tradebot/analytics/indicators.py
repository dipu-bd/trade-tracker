from collections.abc import Sequence
from itertools import pairwise
from math import isclose, isfinite, log, sqrt

TRADING_DAYS = 252
CRYPTO_DAYS = 365


def _require_period(period: int) -> None:
    if period < 1:
        raise ValueError(f"period must be positive, got {period}")


def sma(values: Sequence[float], period: int) -> list[float]:
    _require_period(period)
    if len(values) < period:
        return []

    window = sum(values[:period])
    out = [window / period]
    for index in range(period, len(values)):
        window += values[index] - values[index - period]
        out.append(window / period)
    return out


def ema(values: Sequence[float], period: int) -> list[float]:
    _require_period(period)
    if len(values) < period:
        return []

    alpha = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    out = [current]
    for value in values[period:]:
        current += alpha * (value - current)
        out.append(current)
    return out


def wilder(values: Sequence[float], period: int) -> list[float]:
    """Wilder's smoothing — an EMA with alpha = 1/period, not 2/(period+1).

    RSI, ATR and ADX are all defined against this and give visibly different numbers under a
    conventional EMA, which is the usual source of disagreement with reference implementations.
    """
    _require_period(period)
    if len(values) < period:
        return []

    current = sum(values[:period]) / period
    out = [current]
    for value in values[period:]:
        current = (current * (period - 1) + value) / period
        out.append(current)
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float]:
    _require_period(period)
    if len(closes) < period + 1:
        return []

    gains: list[float] = []
    losses: list[float] = []
    for previous, current in pairwise(closes):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = wilder(gains, period)
    avg_loss = wilder(losses, period)

    out: list[float] = []
    for gain, loss in zip(avg_gain, avg_loss, strict=True):
        if loss == 0:
            out.append(100.0 if gain > 0 else 50.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + gain / loss))
    return out


def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    _require_same_length(highs, lows, closes)
    return [
        max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        for index in range(1, len(highs))
    ]


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> list[float]:
    return wilder(true_range(highs, lows, closes), period)


def adx(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> list[float]:
    _require_same_length(highs, lows, closes)
    _require_period(period)
    if len(highs) < period * 2 + 1:
        return []

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(highs)):
        up = highs[index] - highs[index - 1]
        down = lows[index - 1] - lows[index]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    smoothed_tr = wilder(true_range(highs, lows, closes), period)
    smoothed_plus = wilder(plus_dm, period)
    smoothed_minus = wilder(minus_dm, period)

    dx: list[float] = []
    for tr_value, plus, minus in zip(smoothed_tr, smoothed_plus, smoothed_minus, strict=True):
        if tr_value == 0:
            dx.append(0.0)
            continue
        plus_di = 100.0 * plus / tr_value
        minus_di = 100.0 * minus / tr_value
        total = plus_di + minus_di
        dx.append(0.0 if total == 0 else 100.0 * abs(plus_di - minus_di) / total)

    return wilder(dx, period)


def directional_index(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> tuple[float, float]:
    """The latest (+DI, -DI). Returns (0, 0) when history is too short to define them."""
    _require_same_length(highs, lows, closes)
    _require_period(period)
    if len(highs) < period + 1:
        return (0.0, 0.0)

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(highs)):
        up = highs[index] - highs[index - 1]
        down = lows[index - 1] - lows[index]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    tr_series = wilder(true_range(highs, lows, closes), period)
    plus_series = wilder(plus_dm, period)
    minus_series = wilder(minus_dm, period)
    if not tr_series or tr_series[-1] == 0:
        return (0.0, 0.0)

    return (100.0 * plus_series[-1] / tr_series[-1], 100.0 * minus_series[-1] / tr_series[-1])


def log_returns(closes: Sequence[float]) -> list[float]:
    out: list[float] = []
    for previous, current in pairwise(closes):
        if previous <= 0 or current <= 0:
            out.append(0.0)
        else:
            out.append(log(current / previous))
    return out


def realized_vol(
    closes: Sequence[float], period: int = 20, periods_per_year: int = TRADING_DAYS
) -> list[float]:
    """Annualized volatility from the zero-mean realized-variance estimator.

    Zero-mean rather than demeaned: over a 20-day window the sample mean is almost entirely
    noise, and subtracting it adds more estimation error than the drift it removes.
    """
    _require_period(period)
    returns = log_returns(closes)
    if len(returns) < period:
        return []

    scale = sqrt(periods_per_year)
    squares = [value * value for value in returns]
    window = sum(squares[:period])
    out = [sqrt(window / period) * scale]
    for index in range(period, len(squares)):
        window += squares[index] - squares[index - period]
        out.append(sqrt(max(window, 0.0) / period) * scale)
    return out


def relative_volume(volumes: Sequence[float], period: int = 20) -> float:
    """Latest volume against its trailing average. 1.0 when the average is undefined or zero."""
    _require_period(period)
    if len(volumes) < period + 1:
        return 1.0

    average = sum(volumes[-period - 1 : -1]) / period
    return volumes[-1] / average if average > 0 else 1.0


def rolling_return(closes: Sequence[float], lookback: int, skip: int = 0) -> float | None:
    """Return over `lookback` bars ending `skip` bars ago.

    `skip` is what makes 12-1 momentum expressible: skipping the most recent month avoids the
    short-term reversal effect that would otherwise cancel the momentum being measured.
    """
    if lookback < 1 or skip < 0:
        raise ValueError("lookback must be positive and skip non-negative")
    if len(closes) < lookback + skip + 1:
        return None

    end = len(closes) - 1 - skip
    start = end - lookback
    if closes[start] <= 0:
        return None

    return closes[end] / closes[start] - 1.0


def max_drawdown(equity: Sequence[float]) -> float:
    """Worst peak-to-trough decline as a positive fraction."""
    peak = float("-inf")
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def drawdown_from_peak(closes: Sequence[float], lookback: int) -> float:
    """Current decline from the highest close in the trailing window, as a positive fraction."""
    _require_period(lookback)
    if not closes:
        return 0.0

    window = closes[-lookback:]
    peak = max(window)
    return 0.0 if peak <= 0 else max(0.0, (peak - window[-1]) / peak)


def slope(values: Sequence[float]) -> float:
    """Ordinary least-squares slope against an evenly spaced index."""
    count = len(values)
    if count < 2:
        return 0.0

    mean_x = (count - 1) / 2
    mean_y = sum(values) / count
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    return numerator / denominator if denominator else 0.0


def percentile_rank(values: Sequence[float], target: float) -> float:
    """Position of `target` within `values`, in [0, 1], splitting near-ties at the midpoint.

    Counting ties as "at or below" would rank a perfectly flat volatility history at 1.0 and
    trip the high-volatility regime filter on a market that has not moved at all. Ties are
    matched within a tolerance because the inputs are rolling estimates: a halted instrument
    and a sliding-window sum both produce values that are equal in meaning but not in bits.
    """
    if not values:
        return 0.5

    equal = sum(1 for value in values if isclose(value, target, rel_tol=1e-9, abs_tol=1e-12))
    below = sum(1 for value in values if value < target) - _below_but_close(values, target)
    return (below + equal / 2) / len(values)


def _below_but_close(values: Sequence[float], target: float) -> int:
    return sum(
        1
        for value in values
        if value < target and isclose(value, target, rel_tol=1e-9, abs_tol=1e-12)
    )


def _require_same_length(*series: Sequence[float]) -> None:
    lengths = {len(item) for item in series}
    if len(lengths) > 1:
        raise ValueError(f"series lengths differ: {sorted(lengths)}")


def clean(values: Sequence[float]) -> list[float]:
    return [value for value in values if isfinite(value)]
