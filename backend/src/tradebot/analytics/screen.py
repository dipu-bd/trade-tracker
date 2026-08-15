from dataclasses import dataclass, field

from tradebot.analytics.features import MIN_BARS, Features
from tradebot.analytics.signals import is_leveraged


@dataclass(frozen=True, slots=True)
class ScreenConfig:
    min_dollar_volume: float = 5_000_000.0
    min_price: float = 5.0
    max_price: float = 100_000.0
    min_bars: int = MIN_BARS
    max_atr_pct: float = 0.15
    exclude_leveraged: bool = True
    never: frozenset[str] = frozenset()
    always: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ScreenResult:
    passed: list[Features] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)

    @property
    def symbols(self) -> list[str]:
        return [item.symbol for item in self.passed]


def screen(
    cohort: list[Features],
    config: ScreenConfig | None = None,
    names: dict[str, str] | None = None,
    held: frozenset[str] = frozenset(),
) -> ScreenResult:
    """Quality and tradability filter. Not a timing signal — it never ranks, only excludes.

    Held positions always survive: a name that drops out of the filter would otherwise become
    unmanageable, with no path to exit it.
    """
    config = config or ScreenConfig()
    names = names or {}

    passed: list[Features] = []
    rejected: dict[str, str] = {}

    for features in cohort:
        symbol = features.symbol
        if symbol in held:
            passed.append(features)
            continue

        reason = _reject_reason(features, config, names.get(symbol, ""))
        if reason is None:
            passed.append(features)
        else:
            rejected[symbol] = reason

    return ScreenResult(passed=passed, rejected=rejected)


def _reject_reason(features: Features, config: ScreenConfig, name: str) -> str | None:
    symbol = features.symbol

    if symbol in config.never:
        return "on the never list"
    if config.exclude_leveraged and is_leveraged(symbol, name):
        return "leveraged or inverse product"
    if symbol in config.always:
        return None

    if features.close <= 0:
        return "no price"
    if features.close < config.min_price:
        return f"price {features.close:.2f} below {config.min_price:.2f}"
    if features.close > config.max_price:
        return f"price {features.close:.2f} above {config.max_price:.2f}"
    if features.bar_count < config.min_bars:
        return f"{features.bar_count} bars, {config.min_bars} required"
    if features.dollar_volume < config.min_dollar_volume:
        return f"dollar volume {features.dollar_volume:,.0f} below {config.min_dollar_volume:,.0f}"
    if features.atr_pct > config.max_atr_pct:
        return f"daily ATR {features.atr_pct:.1%} above {config.max_atr_pct:.1%}"

    return None
