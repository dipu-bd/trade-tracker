from dataclasses import dataclass, field
from datetime import date

from tradebot.analytics.exits import ExitAction, ExitConfig, Holding, evaluate_exit, update_stop
from tradebot.analytics.features import Features
from tradebot.analytics.policy import (
    CostConfig,
    TurnoverBudget,
    TurnoverConfig,
    in_cooldown,
    needs_rebalance,
    passes_cost_gate,
)
from tradebot.analytics.screen import ScreenConfig, screen
from tradebot.analytics.series import BarSeries
from tradebot.analytics.signals import (
    Regime,
    RegimeConfig,
    RegimeState,
    TrendSignal,
    assess_regime,
    cross_sectional_rank,
    entry_score,
    is_leveraged,
    trend_signal,
)
from tradebot.analytics.sizing import Sizing, SizingConfig, fit_to_budget, size_position


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    exits: ExitConfig = field(default_factory=ExitConfig)
    turnover: TurnoverConfig = field(default_factory=TurnoverConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    benchmark: str = "SPY"
    require_trend: bool = True


@dataclass(frozen=True, slots=True)
class PortfolioState:
    equity: float
    cash: float
    holdings: dict[str, Holding] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    last_exit: dict[str, date] = field(default_factory=dict)
    turnover_used: float = 0.0

    @property
    def held(self) -> frozenset[str]:
        return frozenset(self.holdings)

    @property
    def invested_weight(self) -> float:
        return sum(self.weights.values())


@dataclass(frozen=True, slots=True)
class Entry:
    symbol: str
    target_weight: float
    current_weight: float
    notional: float
    score: float
    sizing: Sizing
    stop_price: float


@dataclass(frozen=True, slots=True)
class StopUpdate:
    symbol: str
    old_stop: float
    new_stop: float


@dataclass(frozen=True, slots=True)
class Decision:
    as_of: date
    regime: Regime
    entries: list[Entry] = field(default_factory=list)
    exits: list[ExitAction] = field(default_factory=list)
    stop_updates: list[StopUpdate] = field(default_factory=list)
    screened_out: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    candidates: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.entries and not self.exits


def decide(
    as_of: date,
    cohort: list[Features],
    benchmark_series: BarSeries,
    benchmark_features: Features,
    state: PortfolioState,
    config: StrategyConfig | None = None,
    names: dict[str, str] | None = None,
    confidence: dict[str, float] | None = None,
) -> Decision:
    """The whole rules layer, as a pure function.

    `confidence` is the meta-labeler's hook: the AI can scale or veto a bet the rules proposed,
    but the keys are the rules' candidates, so it can never originate a trade of its own.
    """
    config = config or StrategyConfig()
    names = names or {}
    confidence = confidence or {}

    regime = assess_regime(benchmark_series, benchmark_features, config.regime)
    by_symbol = {f.symbol: f for f in cohort}
    signals = {f.symbol: trend_signal(f) for f in cohort}

    exits, stop_updates = _manage_holdings(as_of, state, by_symbol, signals, regime, config)
    exiting = {action.symbol for action in exits if action.is_full}

    result = screen(cohort, config.screen, names, held=state.held)
    ranks = cross_sectional_rank(result.passed)

    entries, skipped = _pick_entries(
        as_of, result.passed, signals, ranks, regime, state, config, names, confidence, exiting
    )

    return Decision(
        as_of=as_of,
        regime=regime,
        entries=entries,
        exits=exits,
        stop_updates=stop_updates,
        screened_out=result.rejected,
        skipped=skipped,
        candidates=len(result.passed),
    )


def _manage_holdings(
    as_of: date,
    state: PortfolioState,
    by_symbol: dict[str, Features],
    signals: dict[str, TrendSignal],
    regime: Regime,
    config: StrategyConfig,
) -> tuple[list[ExitAction], list[StopUpdate]]:
    exits: list[ExitAction] = []
    stop_updates: list[StopUpdate] = []

    for symbol, holding in state.holdings.items():
        features = by_symbol.get(symbol)
        signal = signals.get(symbol)
        if features is None or signal is None:
            continue

        new_stop = update_stop(holding, features, config.exits)
        if new_stop > holding.stop_price:
            stop_updates.append(StopUpdate(symbol, holding.stop_price, new_stop))

        action = evaluate_exit(
            holding,
            features,
            signal,
            as_of,
            risk_off=regime.state is RegimeState.PANIC,
            config=config.exits,
        )
        if action is not None:
            exits.append(action)

    return exits, stop_updates


def _pick_entries(
    as_of: date,
    candidates: list[Features],
    signals: dict[str, TrendSignal],
    ranks: dict[str, float],
    regime: Regime,
    state: PortfolioState,
    config: StrategyConfig,
    names: dict[str, str],
    confidence: dict[str, float],
    exiting: set[str],
) -> tuple[list[Entry], dict[str, str]]:
    skipped: dict[str, str] = {}
    scored: list[tuple[float, Features, Sizing]] = []

    for features in candidates:
        symbol = features.symbol
        if symbol in exiting:
            skipped[symbol] = "exiting this cycle"
            continue

        if is_leveraged(symbol, names.get(symbol, "")):
            skipped[symbol] = "leveraged product, never sized"
            continue

        if in_cooldown(state.last_exit.get(symbol), as_of, config.turnover):
            skipped[symbol] = "in cooldown"
            continue

        signal = signals.get(symbol)
        if signal is None:
            skipped[symbol] = "no features"
            continue
        if config.require_trend and not signal.is_long:
            skipped[symbol] = "no long trend signal"
            continue

        score = entry_score(signal, ranks.get(symbol), features, regime, config.require_trend)
        if score <= 0:
            skipped[symbol] = "zero entry score"
            continue

        sizing = size_position(features, regime, confidence.get(symbol, 1.0), config.sizing)
        if not sizing.is_actionable:
            skipped[symbol] = f"sized to zero ({sizing.binding})"
            continue

        if not passes_cost_gate(sizing.stop_distance, config.costs, config.turnover):
            skipped[symbol] = "round-trip cost too large against stop distance"
            continue

        current = state.weights.get(symbol, 0.0)
        if not needs_rebalance(current, sizing.weight, config.turnover):
            skipped[symbol] = "inside the no-trade band"
            continue

        scored.append((score, features, sizing))

    scored.sort(key=lambda row: row[0], reverse=True)

    room = config.sizing.max_gross_exposure - state.invested_weight
    fitted = fit_to_budget([row[2] for row in scored], room, config.sizing)
    budget = TurnoverBudget(state.turnover_used, config.turnover.monthly_turnover_cap)

    entries: list[Entry] = []
    by_symbol_score = {features.symbol: score for score, features, _ in scored}
    features_by_symbol = {features.symbol: features for _, features, _ in scored}
    remaining = budget.remaining

    for sizing in fitted:
        symbol = sizing.symbol
        current = state.weights.get(symbol, 0.0)
        delta = abs(sizing.weight - current)
        if delta > remaining:
            skipped[symbol] = "monthly turnover budget exhausted"
            continue

        remaining -= delta
        features = features_by_symbol[symbol]
        entries.append(
            Entry(
                symbol=symbol,
                target_weight=sizing.weight,
                current_weight=current,
                notional=sizing.weight * state.equity,
                score=by_symbol_score[symbol],
                sizing=sizing,
                stop_price=features.close * (1.0 - sizing.stop_distance),
            )
        )

    for sizing in [row[2] for row in scored]:
        if sizing.symbol not in {e.symbol for e in entries} and sizing.symbol not in skipped:
            skipped[sizing.symbol] = "did not fit gross exposure or position count"

    return entries, skipped
