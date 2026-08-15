from datetime import date, timedelta
from math import isclose

from tests.factories import choppy, falling, series, trending
from tradebot.analytics.exits import ExitConfig, ExitReason, Holding, evaluate_exit, update_stop
from tradebot.analytics.features import Features, extract
from tradebot.analytics.policy import (
    CostConfig,
    TurnoverBudget,
    TurnoverConfig,
    in_cooldown,
    needs_rebalance,
    passes_cost_gate,
    round_trip_cost,
    truncate_to_turnover,
)
from tradebot.analytics.screen import ScreenConfig, screen
from tradebot.analytics.signals import (
    Regime,
    RegimeConfig,
    RegimeState,
    assess_regime,
    cross_sectional_rank,
    entry_score,
    is_leveraged,
    trend_signal,
    vol_scalar,
)
from tradebot.analytics.sizing import (
    SizingConfig,
    fit_to_budget,
    kelly_fraction_from,
    size_position,
)

CALM = Regime(RegimeState.CALM, 1.0, 0.1, 0.2, 0.5, False)
PANIC = Regime(RegimeState.PANIC, 0.25, -0.2, 0.5, 0.9, True)


def test_features_describe_a_clean_uptrend() -> None:
    features = extract(trending("UP"))

    assert features.above_sma_200
    assert features.momentum_12_1 is not None and features.momentum_12_1 > 0
    assert features.adx_14 > 50
    assert features.drawdown_252 == 0.0
    assert features.warnings == ()


def test_features_flag_short_history_without_raising() -> None:
    features = extract(trending("SHORT", count=30))

    assert features.bar_count == 30
    assert not features.has_full_history
    assert features.momentum_12_1 is None
    assert any("short history" in warning for warning in features.warnings)


def test_features_on_an_empty_series_are_inert() -> None:
    features = extract(series("NONE", []))

    assert features.bar_count == 0
    assert not features.tradable
    assert features.warnings == ("no bars",)


def test_crypto_features_annualize_on_a_365_day_year() -> None:
    equity = extract(trending("EQ", daily=0.002))
    crypto = extract(trending("CR", daily=0.002, asset_class="crypto"))  # type: ignore[arg-type]

    assert crypto.vol_20 > equity.vol_20


def test_trend_signal_is_unanimous_in_a_persistent_uptrend() -> None:
    signal = trend_signal(extract(trending("UP")))

    assert signal.is_long
    assert signal.score == 1.0
    assert signal.agreement == 1.0


def test_trend_signal_is_negative_in_a_persistent_downtrend() -> None:
    signal = trend_signal(extract(falling("DOWN")))

    assert not signal.is_long
    assert signal.score == -1.0


def test_trend_signal_is_neutral_without_any_horizon() -> None:
    signal = trend_signal(extract(trending("NEW", count=5)))

    assert signal.score == 0.0
    assert not signal.is_long


def test_cross_sectional_rank_orders_the_cohort() -> None:
    cohort = [
        extract(trending("FAST", daily=0.003)),
        extract(trending("SLOW", daily=0.0005)),
        extract(falling("LOSER")),
    ]

    ranks = cross_sectional_rank(cohort)

    assert ranks["FAST"] > ranks["SLOW"] > ranks["LOSER"]
    assert ranks["LOSER"] < 0.5 < ranks["FAST"]


def test_cross_sectional_rank_excludes_names_without_a_year_of_history() -> None:
    """Ranking a new listing at zero would read as a strong sell rather than as unknown."""
    cohort = [extract(trending("OLD")), extract(trending("NEW", count=40))]

    ranks = cross_sectional_rank(cohort)

    assert "NEW" not in ranks
    assert "OLD" in ranks


def test_vol_scalar_is_inverse_to_realized_volatility() -> None:
    calm = Features("CALM", "stock", 300, 100.0, vol_20=0.10)
    wild = Features("WILD", "stock", 300, 100.0, vol_20=0.40)

    assert vol_scalar(calm, 0.20) == 1.5
    assert isclose(vol_scalar(wild, 0.20), 0.5)


def test_vol_scalar_is_zero_when_volatility_is_unknown() -> None:
    assert vol_scalar(Features("X", "stock", 300, 100.0, vol_20=0.0), 0.20) == 0.0


def test_regime_is_calm_in_a_rising_low_volatility_market() -> None:
    benchmark = trending("SPY", count=700)
    regime = assess_regime(benchmark, extract(benchmark))

    assert regime.state is RegimeState.CALM
    assert regime.exposure == 1.0
    assert not regime.is_risk_off


def test_regime_is_bear_when_the_benchmark_is_below_trend() -> None:
    benchmark = falling("SPY", count=700)
    regime = assess_regime(benchmark, extract(benchmark))

    assert regime.is_risk_off
    assert regime.exposure < 1.0
    assert regime.below_trend


def test_regime_cuts_exposure_hardest_in_a_panic() -> None:
    """Daniel-Moskowitz: momentum crashes in bear-plus-high-volatility, not in either alone."""
    quiet = falling("SPY", count=700)
    erupting = series(
        "SPY",
        [
            100.0 * (0.999**index) * ((1.06 if index % 2 else 0.94) if index > 640 else 1.0)
            for index in range(700)
        ],
    )

    bear = assess_regime(quiet, extract(quiet))
    panic = assess_regime(erupting, extract(erupting))

    assert panic.state is RegimeState.PANIC
    assert panic.exposure < bear.exposure


def test_regime_defaults_to_calm_without_a_year_of_benchmark_history() -> None:
    short = trending("SPY", count=100)

    assert assess_regime(short, extract(short)).state is RegimeState.CALM


def test_regime_thresholds_are_configurable() -> None:
    benchmark = falling("SPY", count=700)
    config = RegimeConfig(bear_exposure=0.1)

    assert assess_regime(benchmark, extract(benchmark), config).exposure == 0.1


def test_entry_score_is_zero_without_a_long_signal() -> None:
    features = extract(falling("DOWN"))

    assert entry_score(trend_signal(features), 0.9, features, CALM) == 0.0


def test_entry_score_is_scaled_down_by_the_regime() -> None:
    features = extract(trending("UP"))
    signal = trend_signal(features)

    assert entry_score(signal, 0.9, features, PANIC) < entry_score(signal, 0.9, features, CALM)


def test_leveraged_products_are_recognised_by_ticker_and_by_name() -> None:
    assert is_leveraged("TQQQ")
    assert is_leveraged("SOXL")
    assert is_leveraged("TSLL")
    assert is_leveraged("ABCD", "Direxion Daily 3X Bull Shares")
    assert is_leveraged("XYZ", "ProShares UltraShort Something")
    assert not is_leveraged("AAPL")
    assert not is_leveraged("SPY", "SPDR S&P 500 ETF Trust")


def test_screen_rejects_illiquid_cheap_and_short_history_names() -> None:
    cohort = [
        extract(trending("GOOD")),
        extract(trending("PENNY", count=320)),
        extract(trending("THIN", volume=1.0)),
        extract(trending("YOUNG", count=40)),
    ]
    cohort[1] = Features("PENNY", "stock", 320, 1.5, dollar_volume=1e9, atr_pct=0.01)

    result = screen(cohort)

    assert result.symbols == ["GOOD"]
    assert "below" in result.rejected["PENNY"]
    assert "dollar volume" in result.rejected["THIN"]
    assert "bars" in result.rejected["YOUNG"]


def test_screen_excludes_leveraged_products() -> None:
    cohort = [extract(trending("TQQQ")), extract(trending("AAPL"))]

    result = screen(cohort)

    assert result.symbols == ["AAPL"]
    assert result.rejected["TQQQ"] == "leveraged or inverse product"


def test_a_held_position_always_survives_the_screen() -> None:
    """A name that drops out of the filter would otherwise have no path to an exit."""
    cohort = [extract(trending("THIN", volume=1.0))]

    result = screen(cohort, held=frozenset({"THIN"}))

    assert result.symbols == ["THIN"]


def test_the_never_list_beats_the_always_list() -> None:
    cohort = [extract(trending("NOPE"))]
    config = ScreenConfig(never=frozenset({"NOPE"}), always=frozenset({"NOPE"}))

    assert screen(cohort, config).rejected["NOPE"] == "on the never list"


def test_sizing_is_capped_by_the_atr_risk_budget() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=0.10, atr_pct=0.02)
    config = SizingConfig(risk_per_trade=0.01, atr_stop_multiple=3.0, max_position_weight=0.5)

    sizing = size_position(features, CALM, config=config)

    assert isclose(sizing.atr_cap, 0.01 / 0.06)
    assert isclose(sizing.weight, 0.01 / 0.06)
    assert sizing.binding == "atr_risk"


def test_sizing_is_capped_by_the_per_position_limit() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=0.10, atr_pct=0.001)
    config = SizingConfig(max_position_weight=0.10)

    sizing = size_position(features, CALM, config=config)

    assert sizing.weight == 0.10
    assert sizing.binding == "position_cap"


def test_sizing_scales_with_confidence() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=1.0, atr_pct=0.005)

    full = size_position(features, CALM, confidence=1.0)
    half = size_position(features, CALM, confidence=0.5)

    assert isclose(half.weight, full.weight / 2)


def test_a_zero_confidence_veto_sizes_to_nothing() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=0.20, atr_pct=0.01)

    assert size_position(features, CALM, confidence=0.0).weight == 0.0


def test_sizing_is_cut_by_the_regime() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=0.20, atr_pct=0.01)

    calm = size_position(features, CALM)
    panic = size_position(features, PANIC)

    assert isclose(panic.weight, calm.weight * PANIC.exposure)
    assert panic.binding == "regime"


def test_sizing_without_an_atr_is_zero_rather_than_unbounded() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=0.20, atr_pct=0.0)

    assert size_position(features, CALM).weight == 0.0


def test_a_dust_sized_position_is_dropped() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=30.0, atr_pct=0.01)

    assert size_position(features, CALM).weight == 0.0


def test_kelly_fraction_is_zero_without_an_edge() -> None:
    assert kelly_fraction_from(0.5, 1.0) == 0.0
    assert kelly_fraction_from(0.4, 1.0) == 0.0
    assert kelly_fraction_from(0.6, 1.0) > 0.0


def test_fit_to_budget_drops_the_tail_rather_than_shrinking_everything() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=0.20, atr_pct=0.01)
    sizings = [size_position(features, CALM) for _ in range(10)]

    kept = fit_to_budget(sizings, 0.30)

    assert sum(item.weight for item in kept) <= 0.30 + 1e-12
    assert len(kept) < len(sizings)


def test_fit_to_budget_respects_the_position_count_cap() -> None:
    features = Features("X", "stock", 300, 100.0, vol_20=0.20, atr_pct=0.05)
    sizings = [size_position(features, CALM) for _ in range(30)]

    kept = fit_to_budget(sizings, 1.0, SizingConfig(max_positions=4))

    assert len(kept) == 4


def test_the_no_trade_band_ignores_small_drift() -> None:
    assert not needs_rebalance(0.10, 0.105)
    assert needs_rebalance(0.10, 0.20)


def test_the_no_trade_band_has_a_relative_arm() -> None:
    """A 3% drift on a 30% position is inside the band; the same drift on nothing is not."""
    assert not needs_rebalance(0.30, 0.33)
    assert needs_rebalance(0.0, 0.03)


def test_cooldown_blocks_re_entry_for_the_configured_days() -> None:
    exited = date(2024, 5, 1)
    config = TurnoverConfig(cooldown_days=5)

    assert in_cooldown(exited, exited + timedelta(days=2), config)
    assert not in_cooldown(exited, exited + timedelta(days=5), config)
    assert not in_cooldown(None, exited, config)


def test_round_trip_cost_includes_a_square_root_impact_term() -> None:
    config = CostConfig(slippage_bps=10, commission_bps=5)

    assert isclose(round_trip_cost(config), 0.003)
    assert round_trip_cost(config, participation=0.04) > round_trip_cost(config)


def test_the_cost_gate_rejects_a_trade_whose_frictions_eat_its_risk_budget() -> None:
    costs = CostConfig(slippage_bps=50)
    turnover = TurnoverConfig(max_cost_share_of_risk=0.15)

    assert passes_cost_gate(0.15, costs, turnover)
    assert not passes_cost_gate(0.01, costs, turnover)
    assert not passes_cost_gate(0.0, costs, turnover)


def test_the_turnover_budget_truncates_ranked_proposals() -> None:
    budget = TurnoverBudget(used=1.8, cap=2.0)

    kept, dropped = truncate_to_turnover([("A", 0.15), ("B", 0.10), ("C", 0.10)], budget)

    assert [symbol for symbol, _ in kept] == ["A"]
    assert dropped == ["B", "C"]


def test_an_exhausted_turnover_budget_admits_nothing() -> None:
    budget = TurnoverBudget(used=2.0, cap=2.0)

    assert budget.is_exhausted
    assert not budget.admits(0.01)


def holding(**kwargs: object) -> Holding:
    base = {
        "symbol": "X",
        "qty": 100.0,
        "entry_price": 100.0,
        "entry_date": date(2024, 1, 1),
        "highest_close": 100.0,
        "stop_price": 90.0,
    }
    return Holding(**{**base, **kwargs})  # type: ignore[arg-type]


UPTREND = trend_signal(extract(trending("X")))
DOWNTREND = trend_signal(extract(falling("X")))


def test_a_close_through_the_stop_exits_the_whole_position() -> None:
    features = Features("X", "stock", 300, 89.0, atr_14=2.0, atr_pct=0.02)

    action = evaluate_exit(holding(), features, UPTREND, date(2024, 3, 1))

    assert action is not None
    assert action.reason is ExitReason.STOP_LOSS
    assert action.is_full


def test_a_stop_hit_after_a_profitable_run_is_reported_as_a_trailing_stop() -> None:
    features = Features("X", "stock", 300, 89.0, atr_14=2.0, atr_pct=0.02)

    action = evaluate_exit(holding(highest_close=140.0), features, UPTREND, date(2024, 3, 1))

    assert action is not None
    assert action.reason is ExitReason.TRAILING_STOP


def test_the_stop_ratchets_up_but_never_down() -> None:
    features = Features("X", "stock", 300, 150.0, atr_14=2.0)
    config = ExitConfig(trail_multiple=3.0)

    raised = update_stop(holding(), features, config)
    assert raised == 144.0

    assert update_stop(holding(stop_price=148.0), features, config) == 148.0


def test_a_protective_exit_ignores_the_minimum_hold() -> None:
    """A stop that waits three days is not a stop."""
    features = Features("X", "stock", 300, 80.0, atr_14=2.0, atr_pct=0.02)

    action = evaluate_exit(holding(), features, UPTREND, date(2024, 1, 2))

    assert action is not None
    assert action.reason is ExitReason.STOP_LOSS


def test_the_minimum_hold_suppresses_a_discretionary_exit() -> None:
    features = Features("X", "stock", 300, 101.0, atr_14=2.0, atr_pct=0.02)

    assert evaluate_exit(holding(), features, DOWNTREND, date(2024, 1, 2)) is None


def test_a_lost_trend_signal_exits_the_position() -> None:
    features = Features("X", "stock", 300, 101.0, atr_14=2.0, atr_pct=0.02)

    action = evaluate_exit(holding(), features, DOWNTREND, date(2024, 3, 1))

    assert action is not None
    assert action.reason is ExitReason.SIGNAL_LOST


def test_a_panic_regime_exits_everything() -> None:
    features = Features("X", "stock", 300, 101.0, atr_14=2.0, atr_pct=0.02)

    action = evaluate_exit(holding(), features, UPTREND, date(2024, 3, 1), risk_off=True)

    assert action is not None
    assert action.reason is ExitReason.REGIME


def test_the_profit_ladder_trims_rather_than_exits() -> None:
    features = Features("X", "stock", 300, 115.0, atr_14=2.0, atr_pct=0.02)

    action = evaluate_exit(holding(), features, UPTREND, date(2024, 3, 1))

    assert action is not None
    assert action.reason is ExitReason.PROFIT_LADDER
    assert not action.is_full
    assert 0 < action.fraction < 1


def test_the_ladder_fires_only_once() -> None:
    features = Features("X", "stock", 300, 115.0, atr_14=2.0, atr_pct=0.02)

    assert evaluate_exit(holding(laddered=True), features, UPTREND, date(2024, 3, 1)) is None


def test_a_position_going_nowhere_is_closed_by_the_time_stop() -> None:
    features = Features("X", "stock", 300, 101.0, atr_14=2.0, atr_pct=0.02)

    action = evaluate_exit(holding(), features, UPTREND, date(2024, 6, 1))

    assert action is not None
    assert action.reason is ExitReason.TIME_STOP


def test_a_working_position_survives_the_time_stop() -> None:
    features = Features("X", "stock", 300, 108.0, atr_14=2.0, atr_pct=0.02)

    assert evaluate_exit(holding(laddered=True), features, UPTREND, date(2024, 6, 1)) is None


def test_a_choppy_market_produces_no_trend_signal() -> None:
    assert not trend_signal(extract(choppy("CHOP"))).is_long
