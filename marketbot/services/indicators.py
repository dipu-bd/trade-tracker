"""Technical indicators over a list of daily bars.

Hand-rolled in pure Python on purpose: the series here are a few hundred bars
long, so numpy/pandas would add tens of megabytes to an Alpine image to save
microseconds. Every function takes bars in chronological order (oldest first)
and returns 0.0 rather than raising when there is not enough history.
"""

from typing import List, Sequence

from marketbot.dto.market import Bar


def closes(bars: Sequence[Bar]) -> List[float]:
    return [b.close for b in bars]


def sma(values: Sequence[float], period: int) -> float:
    if len(values) < period or period <= 0:
        return 0.0
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> float:
    if len(values) < period or period <= 0:
        return 0.0
    k = 2 / (period + 1)
    # Seed with the SMA of the first `period` values, then walk forward.
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * k + result * (1 - k)
    return result


def ema_series(values: Sequence[float], period: int) -> List[float]:
    if len(values) < period or period <= 0:
        return []
    k = 2 / (period + 1)
    result = sum(values[:period]) / period
    series = [result]
    for value in values[period:]:
        result = value * k + result * (1 - k)
        series.append(result)
    return series


def rsi(values: Sequence[float], period: int = 14) -> float:
    """Wilder's RSI. Returns 50 (neutral) when history is too short."""
    if len(values) < period + 1:
        return 50.0

    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain, avg_loss = gains / period, losses / period

    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def true_ranges(bars: Sequence[Bar]) -> List[float]:
    if len(bars) < 2:
        return []
    ranges = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        cur = bars[i]
        ranges.append(max(
            cur.high - cur.low,
            abs(cur.high - prev_close),
            abs(cur.low - prev_close),
        ))
    return ranges


def atr(bars: Sequence[Bar], period: int = 14) -> float:
    """Wilder-smoothed Average True Range in price units."""
    ranges = true_ranges(bars)
    if len(ranges) < period:
        return 0.0
    value = sum(ranges[:period]) / period
    for tr in ranges[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def atr_pct(bars: Sequence[Bar], period: int = 14) -> float:
    if not bars or bars[-1].close <= 0:
        return 0.0
    return atr(bars, period) / bars[-1].close * 100


def adx(bars: Sequence[Bar], period: int = 14) -> float:
    """Wilder's ADX — trend strength, direction-agnostic. 0 when too short."""
    if len(bars) < period * 2 + 1:
        return 0.0

    plus_dm: List[float] = []
    minus_dm: List[float] = []
    for i in range(1, len(bars)):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    ranges = true_ranges(bars)
    if len(ranges) < period:
        return 0.0

    def wilder(seq: Sequence[float]) -> List[float]:
        smoothed = [sum(seq[:period])]
        for value in seq[period:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / period + value)
        return smoothed

    tr_s, plus_s, minus_s = wilder(ranges), wilder(plus_dm), wilder(minus_dm)

    dx_values: List[float] = []
    for tr_v, plus_v, minus_v in zip(tr_s, plus_s, minus_s):
        if tr_v == 0:
            continue
        plus_di = 100 * plus_v / tr_v
        minus_di = 100 * minus_v / tr_v
        denom = plus_di + minus_di
        if denom == 0:
            continue
        dx_values.append(100 * abs(plus_di - minus_di) / denom)

    if len(dx_values) < period:
        return sum(dx_values) / len(dx_values) if dx_values else 0.0

    value = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        value = (value * (period - 1) + dx) / period
    return value


def pct_return(values: Sequence[float], lookback: int) -> float:
    """Percentage change over `lookback` bars."""
    if len(values) < lookback + 1:
        return 0.0
    past = values[-(lookback + 1)]
    if past <= 0:
        return 0.0
    return (values[-1] - past) / past * 100


def average_volume(bars: Sequence[Bar], period: int = 20) -> float:
    if len(bars) < period or period <= 0:
        return 0.0
    return sum(b.volume for b in bars[-period:]) / period


def average_dollar_volume(bars: Sequence[Bar], period: int = 20) -> float:
    if len(bars) < period or period <= 0:
        return 0.0
    window = bars[-period:]
    return sum(b.close * b.volume for b in window) / period


def relative_volume(bars: Sequence[Bar], period: int = 20) -> float:
    """Latest session's volume against its own 20-day average."""
    if len(bars) < period + 1:
        return 0.0
    baseline = average_volume(bars[:-1], period)
    if baseline <= 0:
        return 0.0
    return bars[-1].volume / baseline


def highest_close(bars: Sequence[Bar], lookback: int = 252) -> float:
    if not bars:
        return 0.0
    return max(b.close for b in bars[-lookback:])
