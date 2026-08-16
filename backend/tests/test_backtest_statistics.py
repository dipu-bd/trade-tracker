import random
from math import isinf

import pytest

from tradebot.analytics.sizing import SizingConfig
from tradebot.backtest import ic
from tradebot.backtest.metrics import (
    TradeResult,
    cagr,
    drawdown_duration,
    evaluate,
    information_coefficient,
    sharpe,
    sortino,
)
from tradebot.backtest.statistics import (
    deflated_sharpe,
    expected_max_sharpe,
    minimum_track_record_length,
    normal_cdf,
    normal_ppf,
    probability_of_backtest_overfitting,
    purged_kfold,
    t_statistic,
    walk_forward,
)
from tradebot.backtest.trials import TrialLedger, fingerprint, parameter_count
from tradebot.engine.strategy import StrategyConfig


def test_normal_ppf_inverts_the_cdf() -> None:
    for probability in [0.01, 0.1, 0.5, 0.9, 0.99]:
        assert normal_cdf(normal_ppf(probability)) == pytest.approx(probability, abs=1e-6)


def test_normal_ppf_matches_known_quantiles() -> None:
    assert normal_ppf(0.975) == pytest.approx(1.959964, abs=1e-4)
    assert normal_ppf(0.5) == pytest.approx(0.0, abs=1e-9)


def test_a_flat_equity_curve_has_no_sharpe() -> None:
    assert sharpe([0.0] * 50) == 0.0


def test_sortino_ignores_upside_dispersion() -> None:
    """A series with no losing period has no downside deviation to divide by."""
    assert sortino([0.01] * 20 + [-0.01] * 5) > 0
    assert sortino([0.01] * 20) == 0.0


def test_cagr_annualises_a_known_doubling() -> None:
    equity = [100.0 * (2 ** (index / 252)) for index in range(253)]

    assert cagr(equity) == pytest.approx(1.0, rel=1e-6)


def test_drawdown_duration_counts_periods_under_water() -> None:
    assert drawdown_duration([100, 90, 80, 95, 110, 105]) == 3


def test_evaluate_reports_every_headline_number() -> None:
    equity = [100.0 * 1.001**index for index in range(200)]
    trades = [TradeResult("AAA", 10, 0.05, 1.2), TradeResult("BBB", 5, -0.02, -0.5)]

    performance = evaluate(equity, trades, exposure=0.6, turnover=1.5)
    body = performance.as_dict()

    assert body["trades"] == 2
    assert body["win_rate"] == 0.5
    assert body["profit_factor"] == pytest.approx(2.5)
    assert body["exposure"] == 0.6
    assert body["max_drawdown"] == 0.0
    assert set(body) >= {"cagr", "sharpe", "sortino", "calmar", "drawdown_days", "average_r"}


def test_evaluate_on_a_single_point_is_inert_rather_than_dividing_by_zero() -> None:
    assert evaluate([100.0]).sharpe == 0.0


def test_expected_max_sharpe_rises_with_the_number_of_attempts() -> None:
    """Try enough configurations and a flattering Sharpe is guaranteed — so the bar must rise."""
    assert expected_max_sharpe(1) == 0.0
    assert expected_max_sharpe(100) > expected_max_sharpe(10) > expected_max_sharpe(2)


def test_deflation_punishes_a_result_found_after_many_trials() -> None:
    random.seed(3)
    returns = [random.gauss(0.001, 0.01) for _ in range(500)]
    observed = sharpe(returns)

    honest = deflated_sharpe(returns, observed, trials=1)
    fished = deflated_sharpe(returns, observed, trials=500)

    assert fished.probability < honest.probability
    assert fished.expected_max > honest.expected_max


def test_a_deflated_sharpe_serialises_with_its_trial_count() -> None:
    returns = [0.001] * 100 + [-0.0005] * 100
    body = deflated_sharpe(returns, sharpe(returns), trials=7).as_dict()

    assert body["trials"] == 7
    assert "significant" in body
    assert "skew" in body and "kurtosis" in body


def test_minimum_track_record_length_is_infinite_without_an_edge() -> None:
    assert isinf(minimum_track_record_length(0.0, 0.0, 3.0))
    assert minimum_track_record_length(1.5, 0.0, 3.0) > 0


def test_pbo_is_near_one_half_when_selection_learns_nothing() -> None:
    """Random performance means the in-sample winner is a coin flip out of sample."""
    random.seed(11)
    performance = [[random.gauss(0, 1) for _ in range(8)] for _ in range(16)]

    value = probability_of_backtest_overfitting(performance)

    assert 0.2 <= value <= 0.8


def test_pbo_is_low_when_one_strategy_is_genuinely_better() -> None:
    random.seed(5)
    performance = [
        [random.gauss(0, 0.2) for _ in range(7)] + [random.gauss(3.0, 0.2)] for _ in range(16)
    ]

    assert probability_of_backtest_overfitting(performance) < 0.2


def test_pbo_on_too_little_data_reports_zero_rather_than_guessing() -> None:
    assert probability_of_backtest_overfitting([[1.0, 2.0]]) == 0.0


def test_purged_folds_never_touch_the_test_window() -> None:
    """Plain k-fold leaks: a training label overlapping the test window is its own answer."""
    for fold in purged_kfold(200, folds=5, embargo=0.02, horizon=5):
        assert not set(fold.train) & set(fold.test)
        for row in fold.train:
            assert row < min(fold.test) - 5 or row >= max(fold.test) + 5


def test_the_embargo_drops_the_window_after_the_test_fold() -> None:
    folds = list(purged_kfold(100, folds=4, embargo=0.1, horizon=1))
    first = folds[0]
    gap = min(row for row in first.train if row > max(first.test)) - max(first.test)

    assert gap > 1


def test_walk_forward_is_anchored_and_moves_forward() -> None:
    folds = list(walk_forward(300, folds=4, minimum=100))

    assert len(folds) >= 2
    for fold in folds:
        assert fold.train[0] == 0
        assert min(fold.test) > max(fold.train)
    assert folds[1].train[-1] > folds[0].train[-1]


def test_walk_forward_declines_a_window_too_short_to_split() -> None:
    assert list(walk_forward(50, minimum=60)) == []


def test_the_information_coefficient_is_one_when_the_ordering_is_perfect() -> None:
    scores = [1.0, 2.0, 3.0, 4.0, 5.0]

    assert information_coefficient(scores, scores) == pytest.approx(1.0)
    assert information_coefficient(scores, list(reversed(scores))) == pytest.approx(-1.0)


def test_the_information_coefficient_of_noise_is_near_zero() -> None:
    random.seed(2)
    scores = [random.random() for _ in range(200)]
    forward = [random.random() for _ in range(200)]

    assert abs(information_coefficient(scores, forward)) < 0.2


def test_t_statistic_separates_a_real_mean_from_noise() -> None:
    assert t_statistic([0.1] * 30) > 2.0
    assert abs(t_statistic([0.1, -0.1] * 15)) < 2.0


def test_a_signal_with_no_edge_is_deweighted_to_zero() -> None:
    """The feature that most distinguishes this from the prior art: it admits failure."""
    random.seed(4)
    scores = [random.random() for _ in range(200)]
    forward = [random.random() for _ in range(200)]

    quality = ic.assess("llm_confidence", scores, forward)

    assert quality.weight < 1.0
    assert "IC" in quality.verdict()


def test_a_signal_that_orders_returns_correctly_keeps_its_influence() -> None:
    scores = [float(index % 20) for index in range(200)]
    forward = [value * 0.01 for value in scores]

    quality = ic.assess("rules_score", scores, forward)

    assert quality.mean_ic > 0.9
    assert quality.is_reliable
    assert quality.weight == 1.0


def test_too_little_history_leaves_influence_unchanged() -> None:
    """Absence of evidence is not evidence of failure.

    Withholding influence during warm-up is self-defeating: zero confidence opens no positions,
    no positions generate no evidence, so the signal could never earn back what it never had.
    """
    quality = ic.assess("llm_confidence", [1.0, 2.0, 3.0], [0.1, 0.2, 0.3])

    assert quality.is_warming_up
    assert quality.weight == 1.0
    assert "too few" in quality.verdict()


def test_a_measured_negative_ic_decays_influence_to_zero_not_merely_downward() -> None:
    """Item 13 must actually bite: a failing AI ends with no influence, not a little less."""
    quality = ic.SignalQuality("llm", observations=200, mean_ic=-0.05, t_stat=-2.4, windows=90)

    assert not quality.is_warming_up
    assert quality.weight == 0.0
    assert ic.apply_deweighting({"AAA": 1.0}, quality) == {"AAA": 0.0}
    assert "decayed to zero" in quality.verdict()


def test_deweighting_scales_confidence_toward_rules_only() -> None:
    quality = ic.SignalQuality("llm", observations=200, mean_ic=-0.1, t_stat=-3.0, windows=100)

    assert ic.apply_deweighting({"AAA": 0.9, "BBB": 0.5}, quality) == {"AAA": 0.0, "BBB": 0.0}


def test_rolling_ic_reports_a_series_not_a_single_number() -> None:
    scores = [float(index) for index in range(100)]
    forward = [float(index) for index in range(100)]

    series = ic.rolling_ic(scores, forward, window=20)

    assert len(series) == 81
    assert all(value == pytest.approx(1.0) for value in series)


def test_the_trial_ledger_counts_distinct_configurations() -> None:
    """A DSR computed from a hand-entered trial count is worthless, so the machine counts."""
    ledger = TrialLedger()
    base = StrategyConfig()
    tweaked = StrategyConfig(sizing=SizingConfig(risk_per_trade=0.02))

    ledger.record(base)
    ledger.record(base)
    ledger.record(tweaked)

    assert ledger.trials == 2
    assert ledger.runs == 3
    assert ledger.as_dict() == {"distinct_configurations": 2, "total_runs": 3}


def test_the_same_configuration_fingerprints_identically() -> None:
    assert fingerprint(StrategyConfig()) == fingerprint(StrategyConfig())


def test_changing_one_threshold_is_a_new_trial() -> None:
    a = fingerprint(StrategyConfig())
    b = fingerprint(StrategyConfig(sizing=SizingConfig(max_positions=7)))

    assert a != b


def test_every_knob_is_counted_as_a_degree_of_freedom() -> None:
    assert parameter_count(StrategyConfig()) > 20


def test_an_empty_ledger_still_reports_one_trial() -> None:
    """Deflating by zero trials would divide the honesty out of the number."""
    assert TrialLedger().trials == 1
