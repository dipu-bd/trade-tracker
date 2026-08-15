from datetime import date

from tests.factories import falling, trending
from tradebot.analytics.exits import ExitConfig, ExitReason, Holding
from tradebot.analytics.features import extract
from tradebot.analytics.policy import TurnoverConfig
from tradebot.analytics.screen import ScreenConfig
from tradebot.analytics.signals import RegimeState
from tradebot.analytics.sizing import SizingConfig
from tradebot.engine.strategy import PortfolioState, StrategyConfig, decide

AS_OF = date(2024, 12, 2)
BENCH = trending("SPY", count=700)
BENCH_FEATURES = extract(BENCH)
BEAR_BENCH = falling("SPY", count=700)
BEAR_BENCH_FEATURES = extract(BEAR_BENCH)


def empty_state(equity: float = 100_000.0) -> PortfolioState:
    return PortfolioState(equity=equity, cash=equity)


def run(cohort, state=None, config=None, names=None, confidence=None):  # type: ignore[no-untyped-def]
    return decide(
        as_of=AS_OF,
        cohort=cohort,
        benchmark_series=BENCH,
        benchmark_features=BENCH_FEATURES,
        state=state or empty_state(),
        config=config,
        names=names,
        confidence=confidence,
    )


def test_a_trending_market_produces_entries() -> None:
    decision = run([extract(trending("AAA")), extract(trending("BBB", daily=0.002))])

    assert decision.regime.state is RegimeState.CALM
    assert {entry.symbol for entry in decision.entries} == {"AAA", "BBB"}
    assert all(entry.target_weight > 0 for entry in decision.entries)


def test_entries_are_ranked_by_score_and_the_stronger_name_leads() -> None:
    decision = run(
        [extract(trending("WEAK", daily=0.0002)), extract(trending("STRONG", daily=0.004))]
    )

    assert decision.entries[0].symbol == "STRONG"
    assert decision.entries[0].score >= decision.entries[-1].score


def test_a_falling_market_produces_no_entries() -> None:
    decision = run([extract(falling("DOWN"))])

    assert decision.entries == []
    assert decision.skipped["DOWN"] == "no long trend signal"


def test_a_leveraged_product_is_excluded_before_it_is_ever_sized() -> None:
    """Carried forward from M2: 2x/3x names break the ATR sizing assumption.

    The screen already drops them, so this asserts the second, independent guard — the one that
    still applies when a name reaches sizing by another route, such as being held.
    """
    cohort = [extract(trending("TQQQ")), extract(trending("AAPL"))]

    decision = run(cohort)

    assert [entry.symbol for entry in decision.entries] == ["AAPL"]
    assert "TQQQ" in decision.screened_out


def test_a_held_leveraged_product_can_be_exited_but_never_added_to() -> None:
    """Held names bypass the screen so they remain manageable — sizing must still refuse them."""
    cohort = [extract(trending("TQQQ"))]
    state = PortfolioState(
        equity=100_000.0,
        cash=50_000.0,
        holdings={
            "TQQQ": Holding("TQQQ", 100.0, 100.0, date(2024, 1, 1), 100.0, 50.0),
        },
        weights={"TQQQ": 0.2},
    )

    decision = run(cohort, state=state)

    assert decision.entries == []
    assert decision.skipped["TQQQ"] == "leveraged product, never sized"


def test_names_that_fail_the_screen_are_reported_with_a_reason() -> None:
    cohort = [extract(trending("THIN", volume=1.0)), extract(trending("GOOD"))]

    decision = run(cohort)

    assert "dollar volume" in decision.screened_out["THIN"]
    assert decision.candidates == 1


def test_a_bear_regime_shrinks_every_target_weight() -> None:
    cohort = [extract(trending("AAA"))]

    calm = run(cohort)
    bear = decide(
        as_of=AS_OF,
        cohort=cohort,
        benchmark_series=BEAR_BENCH,
        benchmark_features=BEAR_BENCH_FEATURES,
        state=empty_state(),
    )

    assert bear.regime.is_risk_off
    assert bear.entries[0].target_weight < calm.entries[0].target_weight


def test_gross_exposure_is_never_exceeded() -> None:
    cohort = [extract(trending(f"S{index:02d}")) for index in range(30)]
    config = StrategyConfig(sizing=SizingConfig(max_gross_exposure=0.5, max_positions=30))

    decision = run(cohort, config=config)

    assert sum(entry.target_weight for entry in decision.entries) <= 0.5 + 1e-9


def test_the_position_count_cap_is_respected() -> None:
    cohort = [extract(trending(f"S{index:02d}")) for index in range(30)]
    config = StrategyConfig(sizing=SizingConfig(max_positions=5))

    assert len(run(cohort, config=config).entries) <= 5


def test_an_existing_position_inside_the_band_is_left_alone() -> None:
    cohort = [extract(trending("AAA"))]
    target = run(cohort).entries[0].target_weight

    state = PortfolioState(
        equity=100_000.0,
        cash=90_000.0,
        holdings={"AAA": Holding("AAA", 10.0, 100.0, date(2024, 1, 1), 100.0, 90.0)},
        weights={"AAA": target},
    )

    decision = run(cohort, state=state)

    assert decision.entries == []
    assert decision.skipped["AAA"] == "inside the no-trade band"


def test_a_recently_exited_name_is_blocked_by_the_cooldown() -> None:
    cohort = [extract(trending("AAA"))]
    state = PortfolioState(equity=100_000.0, cash=100_000.0, last_exit={"AAA": date(2024, 12, 1)})

    decision = run(cohort, state=state)

    assert decision.entries == []
    assert decision.skipped["AAA"] == "in cooldown"


def test_an_exhausted_turnover_budget_stops_new_entries() -> None:
    cohort = [extract(trending("AAA"))]
    state = PortfolioState(equity=100_000.0, cash=100_000.0, turnover_used=2.0)
    config = StrategyConfig(turnover=TurnoverConfig(monthly_turnover_cap=2.0))

    decision = run(cohort, state=state, config=config)

    assert decision.entries == []
    assert decision.skipped["AAA"] == "monthly turnover budget exhausted"


def test_a_zero_confidence_from_the_meta_layer_vetoes_the_trade() -> None:
    """The AI can refuse a bet the rules proposed. It cannot propose one of its own."""
    cohort = [extract(trending("AAA")), extract(trending("BBB"))]

    decision = run(cohort, confidence={"AAA": 0.0, "NOTINUNIVERSE": 1.0})

    assert [entry.symbol for entry in decision.entries] == ["BBB"]
    assert "sized to zero" in decision.skipped["AAA"]


def test_lower_confidence_produces_a_smaller_position() -> None:
    cohort = [extract(trending("AAA"))]
    config = StrategyConfig(sizing=SizingConfig(max_position_weight=1.0))

    full = run(cohort, config=config).entries[0].target_weight
    timid = run(cohort, config=config, confidence={"AAA": 0.4}).entries[0].target_weight

    assert timid < full


def test_a_broken_trend_closes_the_position() -> None:
    cohort = [extract(falling("AAA"))]
    state = PortfolioState(
        equity=100_000.0,
        cash=80_000.0,
        holdings={"AAA": Holding("AAA", 100.0, 100.0, date(2024, 1, 1), 100.0, 1.0)},
        weights={"AAA": 0.2},
    )

    decision = run(cohort, state=state)

    assert [action.reason for action in decision.exits] == [ExitReason.SIGNAL_LOST]


def test_a_stop_is_ratcheted_up_as_the_position_runs() -> None:
    cohort = [extract(trending("AAA"))]
    state = PortfolioState(
        equity=100_000.0,
        cash=80_000.0,
        holdings={"AAA": Holding("AAA", 100.0, 100.0, date(2024, 1, 1), 100.0, 1.0)},
        weights={"AAA": 0.2},
    )

    decision = run(cohort, state=state)

    assert len(decision.stop_updates) == 1
    assert decision.stop_updates[0].new_stop > decision.stop_updates[0].old_stop


def test_a_name_exiting_this_cycle_is_not_re_entered_in_the_same_cycle() -> None:
    cohort = [extract(trending("AAA"))]
    state = PortfolioState(
        equity=100_000.0,
        cash=80_000.0,
        holdings={"AAA": Holding("AAA", 100.0, 100.0, date(2024, 1, 1), 100.0, 1e9)},
        weights={"AAA": 0.2},
    )

    decision = run(cohort, state=state)

    assert decision.exits and decision.exits[0].is_full
    assert decision.skipped["AAA"] == "exiting this cycle"


def test_a_panic_regime_liquidates_holdings() -> None:
    cohort = [extract(trending("AAA"))]
    erupting = falling("SPY", count=700)
    state = PortfolioState(
        equity=100_000.0,
        cash=80_000.0,
        holdings={"AAA": Holding("AAA", 100.0, 100.0, date(2024, 1, 1), 100.0, 1.0)},
        weights={"AAA": 0.2},
    )

    decision = decide(
        as_of=AS_OF,
        cohort=cohort,
        benchmark_series=erupting,
        benchmark_features=extract(erupting),
        state=state,
        config=StrategyConfig(regime=type(StrategyConfig().regime)(vol_percentile_threshold=0.0)),
    )

    assert decision.regime.state is RegimeState.PANIC
    assert [action.reason for action in decision.exits] == [ExitReason.REGIME]


def test_the_decision_is_empty_on_an_empty_universe() -> None:
    decision = run([])

    assert decision.is_empty
    assert decision.candidates == 0


def test_config_sections_are_honoured_end_to_end() -> None:
    cohort = [extract(trending("AAA"))]
    config = StrategyConfig(
        screen=ScreenConfig(min_price=1e9),
        exits=ExitConfig(min_hold_days=0),
    )

    decision = run(cohort, config=config)

    assert decision.entries == []
    assert "below" in decision.screened_out["AAA"]
