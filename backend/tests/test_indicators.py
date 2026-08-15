from math import isclose, log, sqrt

import pytest

from tradebot.analytics import indicators as ind

WILDER_CLOSES = [
    44.34,
    44.09,
    44.15,
    43.61,
    44.33,
    44.83,
    45.10,
    45.42,
    45.84,
    46.08,
    45.89,
    46.03,
    45.61,
    46.28,
    46.28,
    46.00,
    46.03,
    46.41,
    46.22,
    45.64,
    46.21,
    46.25,
    45.71,
    46.45,
    45.78,
    45.35,
    44.03,
    44.18,
    44.22,
    44.57,
    43.42,
    42.66,
    43.13,
]


def test_sma_is_a_plain_moving_mean() -> None:
    assert ind.sma([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]


def test_sma_rolls_without_accumulating_error() -> None:
    values = [float(index % 7) for index in range(500)]
    rolled = ind.sma(values, 20)
    for offset, value in enumerate(rolled):
        window = values[offset : offset + 20]
        assert isclose(value, sum(window) / 20, abs_tol=1e-9)


def test_ema_seeds_on_the_sma_and_uses_two_over_n_plus_one() -> None:
    series = ind.ema([1.0, 2.0, 3.0, 10.0], 3)

    assert series[0] == 2.0
    assert isclose(series[1], 2.0 + 0.5 * (10.0 - 2.0))


def test_wilder_smoothing_is_hand_computable() -> None:
    """Wilder's alpha is 1/n, not 2/(n+1) — the usual source of indicator disagreement."""
    assert ind.wilder([1.0, 2.0, 3.0, 4.0], 2) == [1.5, 2.25, 3.125]


def test_rsi_matches_a_hand_computation_of_wilders_series() -> None:
    """Gains sum to 3.34 and losses to 1.40 over the first 14 changes, so RS is 2.385714.

    Published tables round this to 70.53; 70.46 is what the quoted closes actually give.
    """
    series = ind.rsi(WILDER_CLOSES, 14)

    assert isclose(series[0], 100.0 - 100.0 / (1.0 + (3.34 / 14) / (1.40 / 14)), abs_tol=1e-9)
    assert isclose(series[0], 70.4641, abs_tol=1e-4)


def test_rsi_is_bounded_and_saturates() -> None:
    rising = [float(index) for index in range(1, 40)]
    falling = list(reversed(rising))

    assert ind.rsi(rising, 14)[-1] == 100.0
    assert ind.rsi(falling, 14)[-1] == 0.0


def test_rsi_of_a_flat_series_is_neutral() -> None:
    assert ind.rsi([50.0] * 30, 14)[-1] == 50.0


def test_true_range_takes_the_largest_of_the_three_spans() -> None:
    highs = [10.0, 11.0, 12.0]
    lows = [9.0, 10.0, 10.0]
    closes = [9.5, 10.5, 11.5]

    assert ind.true_range(highs, lows, closes) == [1.5, 2.0]


def test_atr_averages_the_true_range() -> None:
    highs = [10.0, 11.0, 12.0]
    lows = [9.0, 10.0, 10.0]
    closes = [9.5, 10.5, 11.5]

    assert ind.atr(highs, lows, closes, 2) == [1.75]


def test_adx_is_zero_on_a_flat_market() -> None:
    flat = [100.0] * 60

    assert ind.adx(flat, flat, flat, 14)[-1] == 0.0


def test_adx_is_high_on_a_clean_one_way_trend() -> None:
    closes = [100.0 + index for index in range(60)]
    highs = [value + 0.5 for value in closes]
    lows = [value - 0.5 for value in closes]

    assert ind.adx(highs, lows, closes, 14)[-1] > 90.0


def test_directional_index_favours_the_direction_of_travel() -> None:
    closes = [100.0 + index for index in range(40)]
    highs = [value + 0.5 for value in closes]
    lows = [value - 0.5 for value in closes]

    plus_di, minus_di = ind.directional_index(highs, lows, closes, 14)

    assert plus_di > minus_di
    assert minus_di == 0.0


def test_log_returns_are_additive_across_a_round_trip() -> None:
    returns = ind.log_returns([100.0, 110.0, 100.0])

    assert isclose(sum(returns), 0.0, abs_tol=1e-12)


def test_realized_vol_annualizes_a_constant_growth_series() -> None:
    """A constant 1%/day series has zero *dispersion* but non-zero realized vol.

    That is the zero-mean estimator behaving as designed: it measures squared returns, not
    deviation from a fitted drift, and over 20 bars that drift is not worth estimating.
    """
    closes = [100.0 * 1.01**index for index in range(40)]

    assert isclose(ind.realized_vol(closes, 20)[-1], log(1.01) * sqrt(252), rel_tol=1e-9)


def test_realized_vol_of_a_flat_series_is_zero() -> None:
    assert ind.realized_vol([100.0] * 40, 20)[-1] == 0.0


def test_realized_vol_uses_the_crypto_year_when_asked() -> None:
    closes = [100.0 * 1.01**index for index in range(40)]

    equity = ind.realized_vol(closes, 20, ind.TRADING_DAYS)[-1]
    crypto = ind.realized_vol(closes, 20, ind.CRYPTO_DAYS)[-1]

    assert isclose(crypto / equity, sqrt(365 / 252), rel_tol=1e-9)


def test_relative_volume_compares_the_last_bar_to_its_trailing_mean() -> None:
    volumes = [100.0] * 20 + [250.0]

    assert isclose(ind.relative_volume(volumes, 20), 2.5)


def test_relative_volume_is_neutral_without_enough_history() -> None:
    assert ind.relative_volume([100.0, 200.0], 20) == 1.0


def test_rolling_return_measures_the_window() -> None:
    assert isclose(ind.rolling_return([100.0, 110.0, 120.0], 2) or 0.0, 0.2)


def test_rolling_return_skip_excludes_the_recent_window() -> None:
    """12-1 momentum skips the last month, so the final bar must not affect the result."""
    closes = [100.0, 110.0, 120.0, 999.0]

    assert isclose(ind.rolling_return(closes, 2, skip=1) or 0.0, 0.2)


def test_rolling_return_is_none_without_enough_history() -> None:
    assert ind.rolling_return([100.0, 110.0], 5) is None


def test_max_drawdown_finds_the_worst_peak_to_trough() -> None:
    assert isclose(ind.max_drawdown([100.0, 120.0, 60.0, 130.0]), 0.5)


def test_max_drawdown_of_a_monotonic_series_is_zero() -> None:
    assert ind.max_drawdown([1.0, 2.0, 3.0]) == 0.0


def test_drawdown_from_peak_uses_the_trailing_window_only() -> None:
    closes = [200.0] + [100.0] * 5 + [90.0]

    assert isclose(ind.drawdown_from_peak(closes, 3), 0.1)


def test_slope_recovers_a_linear_trend() -> None:
    assert isclose(ind.slope([1.0, 3.0, 5.0, 7.0]), 2.0)


def test_slope_of_a_flat_series_is_zero() -> None:
    assert ind.slope([5.0] * 10) == 0.0


def test_percentile_rank_places_a_value_in_its_cohort() -> None:
    assert ind.percentile_rank([1.0, 2.0, 3.0, 4.0], 3.0) == 0.625
    assert ind.percentile_rank([1.0, 2.0, 3.0, 4.0], 0.0) == 0.0
    assert ind.percentile_rank([1.0, 2.0, 3.0, 4.0], 5.0) == 1.0


def test_percentile_rank_splits_ties_at_the_midpoint() -> None:
    """A flat history must read as mid-range, not as an extreme."""
    assert ind.percentile_rank([5.0] * 10, 5.0) == 0.5


def test_short_history_returns_an_empty_series_rather_than_raising() -> None:
    assert ind.sma([1.0], 5) == []
    assert ind.rsi([1.0, 2.0], 14) == []
    assert ind.realized_vol([1.0, 2.0], 20) == []
    assert ind.adx([1.0], [1.0], [1.0], 14) == []


@pytest.mark.parametrize("period", [0, -1])
def test_a_non_positive_period_is_a_programming_error(period: int) -> None:
    """Short history is a data condition; a bad period is a bug, so they fail differently."""
    with pytest.raises(ValueError, match="period must be positive"):
        ind.sma([1.0, 2.0, 3.0], period)


def test_mismatched_series_lengths_raise() -> None:
    with pytest.raises(ValueError, match="series lengths differ"):
        ind.true_range([1.0, 2.0], [1.0], [1.0, 2.0])
