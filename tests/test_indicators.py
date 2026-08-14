from datetime import date, timedelta

import pytest

from marketbot.dto.market import Bar
from marketbot.services import indicators as ta
from tests.conftest import flat_bars, make_bars


def bars_from_closes(closes, volume=1000.0):
    start = date.today() - timedelta(days=len(closes))
    return [
        Bar(
            bar_date=start + timedelta(days=i),
            open=c, high=c + 1, low=c - 1, close=c, volume=volume,
        )
        for i, c in enumerate(closes)
    ]


def test_sma_is_the_mean_of_the_window():
    assert ta.sma([1, 2, 3, 4, 5], 5) == 3
    assert ta.sma([1, 2, 3, 4, 5], 2) == 4.5


def test_sma_returns_zero_when_history_is_short():
    assert ta.sma([1, 2], 5) == 0.0


def test_ema_of_a_constant_series_is_that_constant():
    assert ta.ema([7.0] * 40, 10) == pytest.approx(7.0)


def test_ema_tracks_below_price_in_an_uptrend():
    closes = [float(i) for i in range(1, 60)]
    assert ta.ema(closes, 20) < closes[-1]


def test_rsi_pins_high_on_an_unbroken_advance():
    closes = [100 + i for i in range(40)]
    assert ta.rsi(closes) == pytest.approx(100.0)


def test_rsi_pins_low_on_an_unbroken_decline():
    closes = [100 - i for i in range(40)]
    assert ta.rsi(closes) == pytest.approx(0.0, abs=1e-6)


def test_rsi_is_neutral_without_enough_history():
    assert ta.rsi([1, 2, 3]) == 50.0


def test_atr_matches_a_hand_computed_constant_range():
    # Each bar spans exactly 2.0 and closes at its midpoint, so every true
    # range is 2.0 and the smoothed average must be 2.0 as well.
    closes = [100.0] * 30
    bars = bars_from_closes(closes)
    assert ta.atr(bars, 14) == pytest.approx(2.0)


def test_atr_pct_is_atr_over_close():
    bars = bars_from_closes([100.0] * 30)
    assert ta.atr_pct(bars, 14) == pytest.approx(2.0)


def test_adx_is_high_for_a_clean_trend_and_low_when_flat():
    trending = make_bars(count=120, amplitude=0.0)
    choppy = flat_bars(count=120)
    assert ta.adx(trending) > ta.adx(choppy)


def test_pct_return_over_lookback():
    closes = [100.0] * 10 + [110.0]
    assert ta.pct_return(closes, 1) == pytest.approx(10.0)
    assert ta.pct_return(closes, 10) == pytest.approx(10.0)


def test_pct_return_is_zero_without_enough_history():
    assert ta.pct_return([100.0], 20) == 0.0


def test_relative_volume_compares_today_to_the_average():
    bars = bars_from_closes([100.0] * 25, volume=1000.0)
    bars[-1].volume = 3000.0
    assert ta.relative_volume(bars, 20) == pytest.approx(3.0)


def test_average_dollar_volume_multiplies_price_by_size():
    bars = bars_from_closes([10.0] * 25, volume=1000.0)
    assert ta.average_dollar_volume(bars, 20) == pytest.approx(10_000.0)


def test_highest_close_looks_back_over_the_window():
    bars = bars_from_closes([1.0, 5.0, 3.0])
    assert ta.highest_close(bars) == 5.0
