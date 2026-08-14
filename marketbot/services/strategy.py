"""The decision rules: regime, sizing, entries, exits, rotation.

Pure functions over plain data — no database and no network — so every rule
here is directly testable against synthetic bars.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from marketbot.db import AssetClass, ExitReason, Portfolio, Position, Regime
from marketbot.dto.market import Bar, Candidate
from marketbot.services import indicators as ta

_log = logging.getLogger(__name__)

REGIME_BENCHMARK = 'SPY'

# How much of the risk budget each regime unlocks.
REGIME_MULTIPLIER = {
    Regime.BULLISH: 1.0,
    Regime.NEUTRAL: 0.5,
    Regime.BEARISH: 0.0,
}

BREAKEVEN_AT_R = 1.0
TRAIL_AT_R = 2.0
TIME_STOP_MIN_R = 0.5


@dataclass
class ProposedEntry:
    candidate: Candidate
    qty: float
    entry_price: float
    stop_price: float
    target_price: float
    r_value: float
    atr: float
    max_hold_days: int
    advisor_note: str = ''

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price

    def brief(self) -> dict:
        data = self.candidate.brief()
        data.update({
            'action': 'BUY',
            'qty': round(self.qty, 6),
            'entry_price': round(self.entry_price, 4),
            'stop_price': round(self.stop_price, 4),
            'target_price': round(self.target_price, 4),
            'notional': round(self.notional, 2),
        })
        return data


@dataclass
class ProposedExit:
    position: Position
    price: float
    reason: str
    score: float = 0.0
    advisor_note: str = ''

    def brief(self) -> dict:
        return {
            'action': 'SELL',
            'symbol': self.position.instrument.symbol,
            'asset_class': self.position.instrument.asset_class,
            'reason': self.reason,
            'price': round(self.price, 4),
            'entry_price': round(self.position.avg_entry, 4),
            'r_multiple': round(self.position.r_multiple(self.price), 2),
            'current_score': round(self.score, 1),
        }


@dataclass
class StopMove:
    position: Position
    old_stop: float
    new_stop: float


@dataclass
class ActionPlan:
    regime: str = Regime.NEUTRAL
    exits: List[ProposedExit] = field(default_factory=list)
    entries: List[ProposedEntry] = field(default_factory=list)
    stop_moves: List[StopMove] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ''

    @property
    def is_empty(self) -> bool:
        return not (self.exits or self.entries or self.stop_moves)


# --------------------------------------------------------------------------- #
# Regime
# --------------------------------------------------------------------------- #

def detect_regime(benchmark_bars: Sequence[Bar]) -> str:
    """Risk-on/risk-off from the benchmark's own moving averages.

    A simple, well-worn filter: above both averages is risk-on, below the
    long average is risk-off, in between is half size and best ideas only.
    """
    closes = ta.closes(benchmark_bars)
    if len(closes) < 210:
        return Regime.NEUTRAL

    price = closes[-1]
    ema50 = ta.ema(closes, 50)
    ema200 = ta.ema(closes, 200)

    if price > ema50 > ema200:
        return Regime.BULLISH
    if price < ema200:
        return Regime.BEARISH
    return Regime.NEUTRAL


def regime_multiplier(regime: str) -> float:
    return REGIME_MULTIPLIER.get(regime, 0.5)


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #

def atr_multiple(portfolio: Portfolio, asset_class: str) -> float:
    if asset_class == AssetClass.ETF:
        return portfolio.etf_atr_stop_mult
    if asset_class == AssetClass.CRYPTO:
        return portfolio.crypto_atr_stop_mult
    return portfolio.atr_stop_mult


def max_hold_days(portfolio: Portfolio, asset_class: str) -> int:
    if asset_class == AssetClass.ETF:
        return portfolio.etf_max_hold_days
    if asset_class == AssetClass.CRYPTO:
        return portfolio.crypto_max_hold_days
    return portfolio.max_hold_days


def entry_threshold(portfolio: Portfolio, asset_class: str) -> float:
    if asset_class == AssetClass.ETF:
        return portfolio.etf_entry_score
    return portfolio.entry_score


def stop_for(portfolio: Portfolio, candidate: Candidate, price: float) -> float:
    distance = atr_multiple(portfolio, candidate.asset_class) * candidate.atr
    if distance <= 0:
        # No usable ATR — fall back to a fixed percentage so sizing stays sane.
        distance = price * 0.08
    return max(price - distance, 0.01)


def size_position(
    portfolio: Portfolio,
    equity: float,
    available_cash: float,
    price: float,
    stop: float,
    regime_mult: float,
    sleeve_room: float,
    allow_fractional: bool,
) -> Tuple[float, float]:
    """Risk-based sizing, clamped by concentration, cash, and sleeve budget.

    Returns `(qty, per_share_risk)`; a qty of 0 means the trade cannot be
    taken within the portfolio's own limits.
    """
    per_share_risk = price - stop
    if per_share_risk <= 0 or price <= 0:
        return 0.0, 0.0

    risk_budget = equity * (portfolio.risk_pct_per_trade / 100) * regime_mult
    if risk_budget <= 0:
        return 0.0, per_share_risk

    qty = risk_budget / per_share_risk

    # Concentration cap, cash on hand, and the sleeve's remaining budget.
    max_notional = min(
        equity * (portfolio.max_position_pct / 100),
        available_cash,
        sleeve_room,
    )
    if max_notional <= 0:
        return 0.0, per_share_risk
    qty = min(qty, max_notional / price)

    if not allow_fractional:
        qty = float(int(qty))

    return (qty if qty > 0 else 0.0), per_share_risk


# --------------------------------------------------------------------------- #
# Exits
# --------------------------------------------------------------------------- #

def evaluate_exit(
    portfolio: Portfolio,
    position: Position,
    price: float,
    candidate: Optional[Candidate],
    regime: str,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """First matching rule wins; order encodes priority."""
    now = now or datetime.now(timezone.utc)
    r = position.r_multiple(price)

    # 1. Hard stop.
    if price <= position.stop_price:
        reason = (
            ExitReason.TRAILING_STOP
            if position.stop_price > position.initial_stop
            else ExitReason.STOP_LOSS
        )
        return reason

    # 2. Take profit.
    if portfolio.take_profit_r > 0 and r >= portfolio.take_profit_r:
        return ExitReason.TAKE_PROFIT

    # 3. Time stop — dead money is still money.
    held_days = _held_days(position, now)
    if held_days >= position.max_hold_days and r < TIME_STOP_MIN_R:
        return ExitReason.TIME_STOP

    # 4. Regime flip: let winners run, cut everything else.
    is_equity = position.instrument.asset_class in AssetClass.EQUITY_CLASSES
    if regime == Regime.BEARISH and is_equity and r < BREAKEVEN_AT_R:
        return ExitReason.REGIME_EXIT

    # 5. Signal decay.
    if candidate is not None:
        if candidate.score < portfolio.exit_score:
            return ExitReason.SIGNAL_EXIT
        if _trend_broken(position, candidate):
            return ExitReason.SIGNAL_EXIT

    return None


def _trend_broken(position: Position, candidate: Candidate) -> bool:
    if position.instrument.asset_class == AssetClass.ETF:
        return candidate.ema50 > 0 and candidate.price < candidate.ema50
    return candidate.ema20 > 0 and candidate.price < candidate.ema20


def _held_days(position: Position, now: datetime) -> int:
    entered = position.entry_at
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=timezone.utc)
    return max((now - entered).days, 0)


def next_stop(
    portfolio: Portfolio,
    position: Position,
    price: float,
    atr: float,
    regime: str,
) -> float:
    """Ratchet the stop upward. It never moves down."""
    stop = position.stop_price
    r = position.r_multiple(price)
    high_water = max(position.high_water, price)

    if r >= TRAIL_AT_R and atr > 0:
        mult = atr_multiple(portfolio, position.instrument.asset_class)
        if regime == Regime.BEARISH:
            mult = min(mult, 1.0)
        stop = max(stop, high_water - mult * atr)
    elif r >= BREAKEVEN_AT_R:
        stop = max(stop, position.avg_entry)
    elif regime == Regime.BEARISH and atr > 0:
        stop = max(stop, price - atr)

    return round(stop, 6)


# --------------------------------------------------------------------------- #
# Entry eligibility
# --------------------------------------------------------------------------- #

def is_entry_eligible(
    portfolio: Portfolio,
    candidate: Candidate,
    regime: str,
    held_symbols: Sequence[str],
    sector_counts: Dict[str, int],
    open_count: int,
) -> Tuple[bool, str]:
    if candidate.symbol in held_symbols:
        return False, 'already held'
    if open_count >= portfolio.max_positions:
        return False, 'position limit reached'
    if candidate.score < entry_threshold(portfolio, candidate.asset_class):
        return False, 'score below threshold'
    if not candidate.trend_ok:
        return False, 'no trend'

    is_equity = candidate.asset_class in AssetClass.EQUITY_CLASSES
    if is_equity and regime == Regime.BEARISH:
        return False, 'risk-off regime'
    if regime == Regime.NEUTRAL and candidate.score < entry_threshold(
        portfolio, candidate.asset_class
    ) + 10:
        return False, 'neutral regime needs a stronger signal'

    sector = candidate.sector or 'Unknown'
    if sector != 'Unknown' and sector_counts.get(sector, 0) >= portfolio.max_per_sector:
        return False, f'sector cap reached ({sector})'

    return True, ''


def find_rotation(
    portfolio: Portfolio,
    candidate: Candidate,
    open_positions: Sequence[Position],
    scores: Dict[str, float],
    prices: Dict[str, float],
) -> Optional[Position]:
    """The weakest holding a new candidate is clearly better than.

    Positions already trailing in profit are left alone — the point of a
    trailing stop is to let a winner run, not to swap it out for a fresh idea.
    """
    weakest: Optional[Position] = None
    weakest_score = float('inf')

    for position in open_positions:
        symbol = position.instrument.symbol
        price = prices.get(symbol)
        if price is not None and position.r_multiple(price) >= BREAKEVEN_AT_R:
            continue
        held_score = scores.get(symbol, 0.0)
        if held_score < weakest_score:
            weakest, weakest_score = position, held_score

    if weakest is None:
        return None
    if candidate.score < weakest_score + portfolio.rotation_edge:
        return None
    return weakest
