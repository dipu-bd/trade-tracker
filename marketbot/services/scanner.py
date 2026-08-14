"""Feature extraction and scoring.

The scoring is a heuristic, deliberately readable and deliberately simple —
every candidate it produces is written to the `signals` table so the weights
can be tuned against realised results rather than taken on faith.
"""

import logging
from typing import List, Optional, Sequence

from marketbot.db import AssetClass, Instrument
from marketbot.dto.market import Bar, Candidate, Quote
from marketbot.services import indicators as ta

_log = logging.getLogger(__name__)

MIN_BARS = 60

# Liquidity floors for individual names and ETFs.
MIN_PRICE = 3.0
MIN_AVG_DOLLAR_VOLUME = 5_000_000

# A position has to be able to pay for itself, without being unhinged.
MIN_ATR_PCT = 1.0
MAX_ATR_PCT = 15.0
MIN_CRYPTO_ATR_PCT = 1.5


def _scaled(value: float, low: float, high: float) -> float:
    """Map `value` onto 0..1 across the [low, high] range, clamped."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def build_candidate(
    instrument: Instrument,
    bars: Sequence[Bar],
    quote: Optional[Quote] = None,
) -> Optional[Candidate]:
    """Turn cached bars plus an optional live quote into a scored candidate."""
    if len(bars) < MIN_BARS:
        return None

    close_series = ta.closes(bars)
    last_close = close_series[-1]
    price = quote.price if quote and quote.price > 0 else last_close
    prev_close = bars[-2].close if len(bars) > 1 else last_close
    if quote and quote.previous_close > 0:
        prev_close = quote.previous_close

    atr_value = ta.atr(bars)
    candidate = Candidate(
        symbol=instrument.symbol,
        asset_class=instrument.asset_class,
        name=instrument.name or instrument.symbol,
        sector=instrument.sector,
        instrument_id=instrument.id,
        price=price,
        prev_close=prev_close,
        gap_pct=((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0,
        rvol=ta.relative_volume(bars),
        atr=atr_value,
        atr_pct=(atr_value / price * 100) if price > 0 else 0.0,
        rsi=ta.rsi(close_series),
        adx=ta.adx(bars),
        ema20=ta.ema(close_series, 20),
        ema50=ta.ema(close_series, 50),
        mom_20=ta.pct_return(close_series, 20),
        mom_60=ta.pct_return(close_series, 60),
        mom_120=ta.pct_return(close_series, 120),
        avg_dollar_volume=ta.average_dollar_volume(bars),
        year_high=ta.highest_close(bars),
    )

    candidate.trend_ok = (
        candidate.price > candidate.ema20 > candidate.ema50 > 0
    )
    candidate.score = score(candidate)
    return candidate


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def score(c: Candidate) -> float:
    if c.asset_class == AssetClass.ETF:
        raw = _score_etf(c)
    elif c.asset_class == AssetClass.CRYPTO:
        raw = _score_crypto(c)
    else:
        raw = _score_stock(c)

    raw += _guards(c)
    return round(max(0.0, min(100.0, raw)), 1)


def _trend_component(c: Candidate, weight: float) -> float:
    """EMA stack plus ADX — how much of a trend is actually there."""
    stack = 0.0
    if c.price > c.ema20 > c.ema50 > 0:
        stack = 1.0
    elif c.price > c.ema20 > 0:
        stack = 0.5
    elif c.price > c.ema50 > 0:
        stack = 0.3
    strength = _scaled(c.adx, 15, 40)
    return weight * (0.6 * stack + 0.4 * strength)


def _gap_component(c: Candidate, weight: float) -> float:
    """From the reference scanner: reward a real gap, punish a chase."""
    g = c.gap_pct
    if g <= 0:
        return 0.0
    if g <= 2:
        return weight * 0.3 * (g / 2)
    if g <= 15:
        return weight * (0.3 + 0.7 * _scaled(g, 2, 15))
    if g <= 25:
        return weight * (1.0 - 0.5 * _scaled(g, 15, 25))
    c.flags.append('EXTENDED')
    return weight * max(0.0, 0.5 - 0.5 * _scaled(g, 25, 45))


def _rsi_component(c: Candidate, weight: float) -> float:
    """Sweet spot is 45-70: trending but not yet exhausted."""
    if 45 <= c.rsi <= 70:
        return weight
    if 35 <= c.rsi < 45:
        return weight * _scaled(c.rsi, 35, 45)
    if 70 < c.rsi <= 80:
        return weight * (1 - _scaled(c.rsi, 70, 80))
    return 0.0


def _score_stock(c: Candidate) -> float:
    s = 0.0
    s += 12 * _scaled(c.mom_20, 0, 15)
    s += 13 * _scaled(c.mom_60, 0, 30)
    s += _trend_component(c, 25)
    s += 15 * _scaled(c.rvol, 1.0, 3.0)
    s += _gap_component(c, 15)
    s += _rsi_component(c, 10)
    s += 10 * _scaled(c.atr_pct, MIN_ATR_PCT, 6.0)
    return s


def _score_etf(c: Candidate) -> float:
    """Trend-following only — an ETF rarely gaps, and rarely needs to."""
    s = 0.0
    s += 15 * _scaled(c.mom_60, 0, 20)
    s += 15 * _scaled(c.mom_120, 0, 35)
    s += _trend_component(c, 40)
    s += _rsi_component(c, 15)
    s += 10 * _scaled(c.atr_pct, 0.5, 3.0)
    return s


def _score_crypto(c: Candidate) -> float:
    """No gap logic — a 24/7 market does not gap the way an equity does."""
    s = 0.0
    s += 18 * _scaled(c.mom_20, 0, 25)
    s += 17 * _scaled(c.mom_60, 0, 50)
    s += _trend_component(c, 30)
    s += 15 * _scaled(c.rvol, 1.0, 3.0)
    s += _rsi_component(c, 10)
    s += 10 * _scaled(c.atr_pct, MIN_CRYPTO_ATR_PCT, 8.0)
    return s


def _guards(c: Candidate) -> float:
    """Hard quality checks. These subtract, and they flag the reason."""
    penalty = 0.0
    min_atr = MIN_CRYPTO_ATR_PCT if c.is_crypto else MIN_ATR_PCT

    if not c.is_crypto:
        if c.price < MIN_PRICE:
            penalty -= 30
            c.flags.append('LOW-PRICE')
        if c.avg_dollar_volume and c.avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
            penalty -= 25
            c.flags.append('THIN')

    if c.atr_pct < min_atr:
        penalty -= 20
        c.flags.append('NO-RANGE')
    elif c.atr_pct > MAX_ATR_PCT:
        penalty -= 10
        c.flags.append('WILD')

    if c.rsi > 80:
        penalty -= 10
        c.flags.append('OVERBOUGHT')
    if c.year_high and c.price >= c.year_high * 0.98:
        c.flags.append('52W-HIGH')
    if not c.trend_ok:
        c.flags.append('NO-TREND')

    return penalty


def rank(candidates: List[Candidate]) -> List[Candidate]:
    return sorted(candidates, key=lambda c: c.score, reverse=True)
