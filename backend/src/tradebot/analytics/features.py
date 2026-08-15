from dataclasses import dataclass, field
from statistics import median

from tradebot.analytics import indicators as ind
from tradebot.analytics.series import BarSeries

MONTH = 21
QUARTER = 63
HALF_YEAR = 126
YEAR = 252

MIN_BARS = 260


@dataclass(frozen=True, slots=True)
class Features:
    """Everything the rules and the AI brief read. Computed once per instrument per cycle."""

    symbol: str
    asset_class: str
    bar_count: int
    close: float

    dollar_volume: float = 0.0
    relative_volume: float = 1.0

    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    return_12m: float | None = None
    momentum_12_1: float | None = None

    vol_20: float = 0.0
    vol_60: float = 0.0
    vol_ratio: float = 1.0

    atr_14: float = 0.0
    atr_pct: float = 0.0

    adx_14: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    rsi_14: float = 50.0

    sma_50: float = 0.0
    sma_200: float = 0.0
    above_sma_200: bool = False
    sma_slope_50: float = 0.0

    drawdown_252: float = 0.0
    high_252: float = 0.0

    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_full_history(self) -> bool:
        return self.bar_count >= MIN_BARS

    @property
    def tradable(self) -> bool:
        return self.close > 0 and self.bar_count >= MONTH + 1


def extract(series: BarSeries) -> Features:
    """Compute the full feature vector. Never raises on short history — it reports it."""
    closes = series.closes
    highs = series.highs
    lows = series.lows
    volumes = series.volumes
    count = len(closes)

    if count == 0:
        return Features(
            symbol=series.symbol,
            asset_class=series.asset_class,
            bar_count=0,
            close=0.0,
            warnings=("no bars",),
        )

    warnings: list[str] = []
    if count < MIN_BARS:
        warnings.append(f"short history: {count} bars, {MIN_BARS} wanted")

    close = closes[-1]
    per_year = series.periods_per_year

    atr_series = ind.atr(highs, lows, closes, 14)
    atr_14 = atr_series[-1] if atr_series else 0.0
    adx_series = ind.adx(highs, lows, closes, 14)
    rsi_series = ind.rsi(closes, 14)
    vol_20_series = ind.realized_vol(closes, 20, per_year)
    vol_60_series = ind.realized_vol(closes, 60, per_year)
    sma_50_series = ind.sma(closes, 50)
    sma_200_series = ind.sma(closes, 200)

    vol_20 = vol_20_series[-1] if vol_20_series else 0.0
    vol_60 = vol_60_series[-1] if vol_60_series else 0.0
    sma_50 = sma_50_series[-1] if sma_50_series else 0.0
    sma_200 = sma_200_series[-1] if sma_200_series else 0.0

    window = min(MONTH, count)
    dollar_volume = median(
        [c * v for c, v in zip(closes[-window:], volumes[-window:], strict=True)]
    )

    plus_di, minus_di = ind.directional_index(highs, lows, closes, 14)
    high_252 = max(closes[-YEAR:])

    return Features(
        symbol=series.symbol,
        asset_class=series.asset_class,
        bar_count=count,
        close=close,
        dollar_volume=dollar_volume,
        relative_volume=ind.relative_volume(volumes, 20),
        return_1m=ind.rolling_return(closes, MONTH),
        return_3m=ind.rolling_return(closes, QUARTER),
        return_6m=ind.rolling_return(closes, HALF_YEAR),
        return_12m=ind.rolling_return(closes, YEAR),
        momentum_12_1=ind.rolling_return(closes, YEAR - MONTH, skip=MONTH),
        vol_20=vol_20,
        vol_60=vol_60,
        vol_ratio=vol_20 / vol_60 if vol_60 > 0 else 1.0,
        atr_14=atr_14,
        atr_pct=atr_14 / close if close > 0 else 0.0,
        adx_14=adx_series[-1] if adx_series else 0.0,
        plus_di=plus_di,
        minus_di=minus_di,
        rsi_14=rsi_series[-1] if rsi_series else 50.0,
        sma_50=sma_50,
        sma_200=sma_200,
        above_sma_200=close > sma_200 > 0,
        sma_slope_50=ind.slope(sma_50_series[-MONTH:]) / close
        if close > 0 and sma_50_series
        else 0,
        drawdown_252=ind.drawdown_from_peak(closes, YEAR),
        high_252=high_252,
        warnings=tuple(warnings),
    )
