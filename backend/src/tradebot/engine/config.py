from dataclasses import fields
from typing import Any

from tradebot.analytics.exits import ExitConfig
from tradebot.analytics.policy import CostConfig, TurnoverConfig
from tradebot.analytics.screen import ScreenConfig
from tradebot.analytics.signals import RegimeConfig
from tradebot.analytics.sizing import SizingConfig
from tradebot.db.models import Portfolio
from tradebot.engine.strategy import StrategyConfig

SECTIONS = {
    "screen": ScreenConfig,
    "sizing": SizingConfig,
    "regime": RegimeConfig,
    "exits": ExitConfig,
    "turnover": TurnoverConfig,
}

FROZEN_SETS = {"never", "always"}


def _build[T](kind: type[T], overrides: dict[str, Any]) -> T:
    known = {field.name for field in fields(kind)}  # type: ignore[arg-type]
    accepted: dict[str, Any] = {}
    for key, value in overrides.items():
        if key not in known:
            continue
        accepted[key] = frozenset(value) if key in FROZEN_SETS else value
    return kind(**accepted)


def strategy_config(portfolio: Portfolio) -> StrategyConfig:
    """Portfolio settings into a strategy config.

    Costs come from the portfolio's own slippage and commission rather than the strategy JSON,
    so the cost gate is measured against what the broker will actually charge.
    """
    stored = dict(portfolio.strategy or {})
    sections: dict[str, Any] = {
        name: _build(kind, stored.get(name, {})) for name, kind in SECTIONS.items()
    }

    return StrategyConfig(
        costs=CostConfig(
            slippage_bps=float(portfolio.slippage_bps),
            commission_bps=float(portfolio.commission_bps),
            min_commission=float(portfolio.min_commission),
            impact_coefficient=float(stored.get("impact_coefficient", 0.1)),
        ),
        benchmark=portfolio.benchmark,
        require_trend=bool(stored.get("require_trend", True)),
        **sections,
    )


def parameter_count(config: StrategyConfig) -> int:
    """Every knob is a degree of freedom, and the backtester deflates Sharpe by how many."""
    total = 0
    for name in SECTIONS:
        section = getattr(config, name)
        total += len([f for f in fields(section) if f.name not in FROZEN_SETS])
    return total
