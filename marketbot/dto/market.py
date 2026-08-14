from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class Bar:
    """One daily OHLCV candle."""

    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Quote:
    symbol: str
    price: float
    previous_close: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    volume: float = 0.0
    avg_volume: float = 0.0
    year_high: float = 0.0
    name: str = ''
    exchange: str = ''

    @property
    def gap_pct(self) -> float:
        if self.previous_close <= 0:
            return 0.0
        return (self.price - self.previous_close) / self.previous_close * 100


@dataclass
class UniverseEntry:
    symbol: str
    asset_class: str
    name: str = ''
    exchange: str = ''
    sector: str = ''


@dataclass
class Candidate:
    """A scored instrument, ready for the entry/exit rules.

    The feature set and the `flags` idea come from the reference gap scanner;
    the extra trend/momentum columns are what a swing horizon needs.
    """

    symbol: str
    asset_class: str
    name: str = ''
    sector: str = ''

    price: float = 0.0
    prev_close: float = 0.0
    gap_pct: float = 0.0
    rvol: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    rsi: float = 0.0
    adx: float = 0.0
    ema20: float = 0.0
    ema50: float = 0.0
    mom_20: float = 0.0
    mom_60: float = 0.0
    mom_120: float = 0.0
    avg_dollar_volume: float = 0.0
    year_high: float = 0.0

    trend_ok: bool = False
    score: float = 0.0
    flags: List[str] = field(default_factory=list)

    instrument_id: Optional[int] = None

    @property
    def is_crypto(self) -> bool:
        return self.asset_class == 'CRYPTO'

    @property
    def is_etf(self) -> bool:
        return self.asset_class == 'ETF'

    def brief(self) -> dict:
        """Compact form handed to the LLM advisor — numbers only, no prose."""
        return {
            'symbol': self.symbol,
            'asset_class': self.asset_class,
            'sector': self.sector or None,
            'price': round(self.price, 4),
            'score': round(self.score, 1),
            'gap_pct': round(self.gap_pct, 2),
            'rvol': round(self.rvol, 2),
            'atr_pct': round(self.atr_pct, 2),
            'rsi': round(self.rsi, 1),
            'adx': round(self.adx, 1),
            'mom_20': round(self.mom_20, 2),
            'mom_60': round(self.mom_60, 2),
            'trend_ok': self.trend_ok,
            'avg_dollar_volume_m': round(self.avg_dollar_volume / 1e6, 1),
            'flags': list(self.flags),
        }
