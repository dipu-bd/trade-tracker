from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Preset:
    """A starting configuration, not a recommendation.

    Every value here is a degree of freedom the backtester must count, so the presets differ
    along few axes rather than being separately tuned.
    """

    key: str
    name: str
    summary: str
    benchmark: str
    cadence: str
    strategy: dict[str, Any] = field(default_factory=dict)
    universe: dict[str, Any] = field(default_factory=dict)
    quality: str = "balanced"
    deliberation: str = "firm_debate"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="conservative_index",
        name="Conservative Index",
        summary="Broad ETFs only, small positions, quick to cut in a bear market.",
        benchmark="SPY",
        cadence="daily",
        strategy={
            "sizing": {
                "target_vol": 0.12,
                "risk_per_trade": 0.005,
                "max_position_weight": 0.20,
                "max_positions": 6,
                "max_gross_exposure": 0.80,
            },
            "regime": {"bear_exposure": 0.30, "panic_exposure": 0.0},
            "turnover": {"monthly_turnover_cap": 0.75, "cooldown_days": 10},
            "screen": {"min_dollar_volume": 25_000_000.0},
        },
        universe={"asset_classes": ["etf"], "max_symbols": 40},
        quality="economy",
        deliberation="single_call",
    ),
    Preset(
        key="balanced_growth",
        name="Balanced Growth",
        summary="Large-cap equities and ETFs with the default risk budget.",
        benchmark="SPY",
        cadence="daily",
        strategy={},
        universe={"asset_classes": ["stock", "etf"], "max_symbols": 120},
    ),
    Preset(
        key="momentum_swing",
        name="Momentum Swing",
        summary="Concentrated trend following, wider stops, higher turnover.",
        benchmark="QQQ",
        cadence="twice_daily",
        strategy={
            "sizing": {
                "target_vol": 0.28,
                "risk_per_trade": 0.015,
                "max_position_weight": 0.20,
                "max_positions": 8,
            },
            "exits": {"atr_stop_multiple": 4.0, "trail_multiple": 4.0, "time_stop_days": 60},
            "turnover": {"monthly_turnover_cap": 3.0, "cooldown_days": 3},
        },
        universe={"asset_classes": ["stock", "etf"], "max_symbols": 150},
        quality="thorough",
        deliberation="multi_round_debate",
    ),
    Preset(
        key="crypto_aggressive",
        name="Crypto Aggressive",
        summary="24/7 crypto sleeve, volatility-scaled, benchmarked against BTC.",
        benchmark="BTC-USD",
        cadence="crypto_daily",
        strategy={
            "sizing": {
                "target_vol": 0.45,
                "risk_per_trade": 0.02,
                "max_position_weight": 0.25,
                "max_positions": 6,
            },
            "screen": {"min_dollar_volume": 2_000_000.0, "max_atr_pct": 0.30, "min_price": 0.01},
            "exits": {"atr_stop_multiple": 3.5, "min_hold_days": 1},
        },
        universe={"asset_classes": ["crypto"], "max_symbols": 60},
    ),
)

BY_KEY = {preset.key: preset for preset in PRESETS}
