from dataclasses import dataclass
from enum import StrEnum

from tradebot.analytics import indicators as ind
from tradebot.analytics.features import YEAR, Features
from tradebot.analytics.series import BarSeries

HORIZONS = ("return_1m", "return_3m", "return_6m", "return_12m")


class RegimeState(StrEnum):
    CALM = "calm"
    HIGH_VOL = "high_vol"
    BEAR = "bear"
    PANIC = "panic"


@dataclass(frozen=True, slots=True)
class TrendSignal:
    """Time-series momentum, Moskowitz/Ooi/Pedersen (2012).

    The sign of past return at each horizon, averaged. Sign rather than magnitude is the
    paper's own specification and costs one fewer fitted parameter than a magnitude weighting.
    """

    symbol: str
    score: float
    horizons: dict[str, float]
    agreement: float

    @property
    def is_long(self) -> bool:
        return self.score > 0


@dataclass(frozen=True, slots=True)
class Regime:
    state: RegimeState
    exposure: float
    benchmark_return_252: float
    benchmark_vol: float
    vol_percentile: float
    below_trend: bool

    @property
    def is_risk_off(self) -> bool:
        return self.state in (RegimeState.BEAR, RegimeState.PANIC)


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    """Defaults are reasoned, not fitted. Every value here is a degree of freedom, so the set
    is deliberately small and each is counted in the backtest's trial budget."""

    vol_percentile_threshold: float = 0.75
    panic_exposure: float = 0.25
    bear_exposure: float = 0.50
    high_vol_exposure: float = 0.75
    calm_exposure: float = 1.00
    vol_history: int = YEAR * 2


def trend_signal(features: Features) -> TrendSignal:
    horizons: dict[str, float] = {}
    for name in HORIZONS:
        value = getattr(features, name)
        if value is not None:
            horizons[name] = value

    if not horizons:
        return TrendSignal(features.symbol, 0.0, {}, 0.0)

    signs = [1.0 if value > 0 else -1.0 for value in horizons.values()]
    score = sum(signs) / len(signs)
    agreement = abs(score)
    return TrendSignal(features.symbol, score, horizons, agreement)


def cross_sectional_rank(cohort: list[Features]) -> dict[str, float]:
    """Jegadeesh-Titman 12-1 percentile rank within a sleeve, in [0, 1].

    Instruments without 12 months of history are excluded rather than ranked at zero, which
    would otherwise read as a strong sell signal for anything newly listed.
    """
    scored = [(f.symbol, f.momentum_12_1) for f in cohort if f.momentum_12_1 is not None]
    if not scored:
        return {}

    values = [value for _, value in scored]
    return {symbol: ind.percentile_rank(values, value) for symbol, value in scored}


def vol_scalar(features: Features, target_vol: float, max_scalar: float = 1.5) -> float:
    """Moreira-Muir volatility targeting: exposure inversely proportional to realized vol.

    Replications suggest most of time-series momentum's measured benefit comes from this
    scaling rather than the signal, so it is a first-class component, not a refinement.
    """
    if features.vol_20 <= 0:
        return 0.0
    return min(target_vol / features.vol_20, max_scalar)


def assess_regime(
    benchmark: BarSeries, features: Features, config: RegimeConfig | None = None
) -> Regime:
    """Daniel-Moskowitz (2016) momentum-crash filter.

    Momentum collapses specifically in panic states — a bear market combined with high
    volatility — so exposure is cut there rather than uniformly.
    """
    config = config or RegimeConfig()
    closes = benchmark.closes

    if len(closes) < YEAR:
        return Regime(RegimeState.CALM, config.calm_exposure, 0.0, features.vol_20, 0.5, False)

    return_252 = ind.rolling_return(closes, YEAR) or 0.0
    below_trend = bool(features.sma_200 > 0 and features.close < features.sma_200)

    vol_series = ind.realized_vol(closes, 20, benchmark.periods_per_year)
    history = vol_series[-config.vol_history :]
    vol_percentile = ind.percentile_rank(history, features.vol_20) if history else 0.5

    bear = return_252 < 0 or below_trend
    high_vol = vol_percentile >= config.vol_percentile_threshold

    if bear and high_vol:
        state, exposure = RegimeState.PANIC, config.panic_exposure
    elif bear:
        state, exposure = RegimeState.BEAR, config.bear_exposure
    elif high_vol:
        state, exposure = RegimeState.HIGH_VOL, config.high_vol_exposure
    else:
        state, exposure = RegimeState.CALM, config.calm_exposure

    return Regime(
        state=state,
        exposure=exposure,
        benchmark_return_252=return_252,
        benchmark_vol=features.vol_20,
        vol_percentile=vol_percentile,
        below_trend=below_trend,
    )


def entry_score(
    trend: TrendSignal, rank: float | None, features: Features, regime: Regime
) -> float:
    """Blend the two momentum sources into one comparable number, in [0, 1].

    Used only to rank candidates against each other for the turnover budget — never as a
    probability, and never fed to the sizer directly.
    """
    if not trend.is_long:
        return 0.0

    components = [trend.agreement]
    if rank is not None:
        components.append(rank)
    if features.adx_14 > 0:
        components.append(min(features.adx_14 / 50.0, 1.0))

    base = sum(components) / len(components)
    return base * regime.exposure


def is_leveraged(symbol: str, name: str = "") -> bool:
    """Leveraged and inverse ETPs break the ATR sizing assumption, so they are excluded before
    sizing rather than screened on liquidity, which they would pass.
    """
    haystack = f"{symbol} {name}".upper()
    markers = (
        "2X",
        "3X",
        "-1X",
        "ULTRA",
        "ULTRASHORT",
        "LEVERAGED",
        "INVERSE",
        "BULL 2",
        "BEAR 2",
        "BULL 3",
        "BEAR 3",
        "DAILY 2",
        "DAILY 3",
    )
    if any(marker in haystack for marker in markers):
        return True

    return symbol.upper() in LEVERAGED_SYMBOLS


LEVERAGED_TICKERS = """
    TQQQ SQQQ SPXL SPXS SPXU UPRO SDOW UDOW TNA TZA SOXL SOXS LABU LABD FAS FAZ
    YINN YANG NUGT DUST JNUG JDST BOIL KOLD UCO SCO UVXY SVXY VIXY TMF TMV
    TSLL TSLQ NVDL NVDS AAPU AAPD MSFU MSFD AMZU AMZD GGLL GGLS METU METD CONL
    MSTU MSTX MSTZ QLD SSO QID SDS DXD DDM URTY SRTY ERX ERY
"""

LEVERAGED_SYMBOLS = frozenset(LEVERAGED_TICKERS.split())
