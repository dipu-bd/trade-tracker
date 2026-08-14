from datetime import datetime, timedelta, timezone

import pytest

from marketbot.db import AssetClass, ExitReason, Instrument, Portfolio, Position, Regime
from marketbot.dto.market import Candidate
from marketbot.services import strategy
from tests.conftest import downtrend_bars, flat_bars, make_bars


def make_portfolio(**overrides) -> Portfolio:
    portfolio = Portfolio(
        name='p', initial_capital=100_000.0, cash=100_000.0,
        risk_pct_per_trade=1.0, max_positions=5, max_position_pct=25.0,
        max_per_sector=2, daily_loss_pct=6.0, atr_stop_mult=2.0,
        etf_atr_stop_mult=3.0, crypto_atr_stop_mult=2.5, take_profit_r=3.0,
        max_hold_days=15, etf_max_hold_days=30, crypto_max_hold_days=10,
        crypto_max_pct=30.0, entry_score=60.0, etf_entry_score=50.0,
        exit_score=35.0, rotation_edge=15.0, slippage_bps=10.0,
        commission_bps=0.0, crypto_commission_bps=10.0,
    )
    for key, value in overrides.items():
        setattr(portfolio, key, value)
    return portfolio


def make_position(
    symbol='AAA',
    asset_class=AssetClass.STOCK,
    avg_entry=100.0,
    stop=90.0,
    qty=10.0,
    entry_days_ago=1,
    sector='Technology',
    **overrides,
) -> Position:
    position = Position(
        portfolio_id=1, instrument_id=1, qty=qty, avg_entry=avg_entry,
        initial_stop=stop, stop_price=stop, high_water=avg_entry,
        r_value=avg_entry - stop, target_price=avg_entry * 1.3,
        max_hold_days=15, atr_at_entry=5.0,
        entry_at=datetime.now(timezone.utc) - timedelta(days=entry_days_ago),
    )
    position.instrument = Instrument(
        symbol=symbol, asset_class=asset_class, sector=sector
    )
    for key, value in overrides.items():
        setattr(position, key, value)
    return position


def make_candidate(**overrides) -> Candidate:
    candidate = Candidate(
        symbol='AAA', asset_class=AssetClass.STOCK, sector='Technology',
        price=110.0, atr=5.0, atr_pct=4.5, rsi=60.0, adx=30.0,
        ema20=105.0, ema50=100.0, mom_20=8.0, mom_60=20.0,
        avg_dollar_volume=50_000_000, trend_ok=True, score=75.0,
    )
    for key, value in overrides.items():
        setattr(candidate, key, value)
    return candidate


# --------------------------------------------------------------------------- #
# Regime
# --------------------------------------------------------------------------- #

def test_regime_is_bullish_when_price_leads_both_averages():
    assert strategy.detect_regime(make_bars(count=260)) == Regime.BULLISH


def test_regime_is_bearish_in_a_sustained_decline():
    assert strategy.detect_regime(downtrend_bars(count=260)) == Regime.BEARISH


def test_regime_defaults_to_neutral_without_enough_history():
    assert strategy.detect_regime(make_bars(count=50)) == Regime.NEUTRAL


def test_bearish_regime_unlocks_no_risk_budget():
    assert strategy.regime_multiplier(Regime.BEARISH) == 0.0
    assert strategy.regime_multiplier(Regime.NEUTRAL) == 0.5
    assert strategy.regime_multiplier(Regime.BULLISH) == 1.0


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #

def test_size_risks_exactly_the_configured_fraction_of_equity():
    portfolio = make_portfolio(risk_pct_per_trade=1.0)
    qty, r_value = strategy.size_position(
        portfolio, equity=100_000, available_cash=100_000,
        price=100.0, stop=95.0, regime_mult=1.0,
        sleeve_room=100_000, allow_fractional=True,
    )
    # 1% of 100k is 1,000 of risk, spread over 5.00 of risk per share.
    assert r_value == pytest.approx(5.0)
    assert qty == pytest.approx(200.0)
    assert qty * r_value == pytest.approx(1_000.0)


def test_size_is_capped_by_the_concentration_limit():
    portfolio = make_portfolio(risk_pct_per_trade=5.0, max_position_pct=10.0)
    qty, _ = strategy.size_position(
        portfolio, equity=100_000, available_cash=100_000,
        price=100.0, stop=99.0, regime_mult=1.0,
        sleeve_room=100_000, allow_fractional=True,
    )
    assert qty * 100.0 == pytest.approx(10_000.0)


def test_size_is_capped_by_the_sleeve_budget():
    portfolio = make_portfolio()
    qty, _ = strategy.size_position(
        portfolio, equity=100_000, available_cash=100_000,
        price=100.0, stop=95.0, regime_mult=1.0,
        sleeve_room=5_000, allow_fractional=True,
    )
    assert qty * 100.0 == pytest.approx(5_000.0)


def test_size_is_capped_by_available_cash():
    portfolio = make_portfolio()
    qty, _ = strategy.size_position(
        portfolio, equity=100_000, available_cash=1_000,
        price=100.0, stop=95.0, regime_mult=1.0,
        sleeve_room=100_000, allow_fractional=True,
    )
    assert qty * 100.0 <= 1_000.0


def test_whole_share_instruments_round_down():
    portfolio = make_portfolio()
    qty, _ = strategy.size_position(
        portfolio, equity=1_000, available_cash=1_000,
        price=100.0, stop=95.0, regime_mult=1.0,
        sleeve_room=1_000, allow_fractional=False,
    )
    assert qty == int(qty)


def test_a_bearish_multiplier_produces_no_position():
    portfolio = make_portfolio()
    qty, _ = strategy.size_position(
        portfolio, equity=100_000, available_cash=100_000,
        price=100.0, stop=95.0, regime_mult=0.0,
        sleeve_room=100_000, allow_fractional=True,
    )
    assert qty == 0.0


def test_stop_uses_the_asset_class_atr_multiple():
    portfolio = make_portfolio()
    stock = make_candidate(asset_class=AssetClass.STOCK, atr=5.0)
    etf = make_candidate(asset_class=AssetClass.ETF, atr=5.0)
    crypto = make_candidate(asset_class=AssetClass.CRYPTO, atr=5.0)

    assert strategy.stop_for(portfolio, stock, 100.0) == pytest.approx(90.0)
    assert strategy.stop_for(portfolio, etf, 100.0) == pytest.approx(85.0)
    assert strategy.stop_for(portfolio, crypto, 100.0) == pytest.approx(87.5)


def test_stop_falls_back_to_a_percentage_when_atr_is_unusable():
    portfolio = make_portfolio()
    candidate = make_candidate(atr=0.0)
    assert strategy.stop_for(portfolio, candidate, 100.0) == pytest.approx(92.0)


# --------------------------------------------------------------------------- #
# Exits
# --------------------------------------------------------------------------- #

def test_hard_stop_fires_when_price_touches_the_initial_stop():
    portfolio = make_portfolio()
    position = make_position()
    reason = strategy.evaluate_exit(
        portfolio, position, price=89.0, candidate=None, regime=Regime.BULLISH
    )
    assert reason == ExitReason.STOP_LOSS


def test_a_raised_stop_reports_as_a_trailing_stop():
    portfolio = make_portfolio()
    position = make_position()
    position.stop_price = 105.0  # ratcheted above the initial 90
    reason = strategy.evaluate_exit(
        portfolio, position, price=104.0, candidate=None, regime=Regime.BULLISH
    )
    assert reason == ExitReason.TRAILING_STOP


def test_take_profit_fires_at_the_configured_r_multiple():
    portfolio = make_portfolio(take_profit_r=3.0)
    position = make_position(avg_entry=100.0, stop=90.0)
    reason = strategy.evaluate_exit(
        portfolio, position, price=130.0, candidate=None, regime=Regime.BULLISH
    )
    assert reason == ExitReason.TAKE_PROFIT


def test_time_stop_recycles_dead_money():
    portfolio = make_portfolio()
    position = make_position(entry_days_ago=40)
    reason = strategy.evaluate_exit(
        portfolio, position, price=101.0, candidate=None, regime=Regime.BULLISH
    )
    assert reason == ExitReason.TIME_STOP


def test_time_stop_leaves_a_winner_alone():
    portfolio = make_portfolio()
    position = make_position(entry_days_ago=40)
    reason = strategy.evaluate_exit(
        portfolio, position, price=120.0, candidate=None, regime=Regime.BULLISH
    )
    assert reason != ExitReason.TIME_STOP


def test_risk_off_regime_cuts_an_equity_position_that_is_not_yet_winning():
    portfolio = make_portfolio()
    position = make_position()
    reason = strategy.evaluate_exit(
        portfolio, position, price=101.0, candidate=None, regime=Regime.BEARISH
    )
    assert reason == ExitReason.REGIME_EXIT


def test_risk_off_regime_leaves_crypto_and_winners_alone():
    portfolio = make_portfolio()
    crypto = make_position(asset_class=AssetClass.CRYPTO)
    assert strategy.evaluate_exit(
        portfolio, crypto, price=101.0, candidate=None, regime=Regime.BEARISH
    ) is None

    winner = make_position()
    assert strategy.evaluate_exit(
        portfolio, winner, price=115.0, candidate=None, regime=Regime.BEARISH
    ) is None


def test_signal_decay_exits_when_the_score_collapses():
    portfolio = make_portfolio(exit_score=35.0)
    position = make_position()
    candidate = make_candidate(score=10.0)
    reason = strategy.evaluate_exit(
        portfolio, position, price=105.0, candidate=candidate, regime=Regime.BULLISH
    )
    assert reason == ExitReason.SIGNAL_EXIT


def test_signal_decay_exits_when_the_trend_breaks():
    portfolio = make_portfolio()
    position = make_position()
    candidate = make_candidate(score=80.0, price=104.0, ema20=110.0)
    reason = strategy.evaluate_exit(
        portfolio, position, price=104.0, candidate=candidate, regime=Regime.BULLISH
    )
    assert reason == ExitReason.SIGNAL_EXIT


def test_a_healthy_position_is_not_exited():
    portfolio = make_portfolio()
    position = make_position()
    candidate = make_candidate(score=80.0, price=110.0, ema20=105.0)
    reason = strategy.evaluate_exit(
        portfolio, position, price=110.0, candidate=candidate, regime=Regime.BULLISH
    )
    assert reason is None


# --------------------------------------------------------------------------- #
# Stop ratchet
# --------------------------------------------------------------------------- #

def test_stop_moves_to_breakeven_at_one_r():
    portfolio = make_portfolio()
    position = make_position(avg_entry=100.0, stop=90.0)
    assert strategy.next_stop(
        portfolio, position, price=110.0, atr=5.0, regime=Regime.BULLISH
    ) == pytest.approx(100.0)


def test_stop_trails_the_high_water_mark_past_two_r():
    portfolio = make_portfolio(atr_stop_mult=2.0)
    position = make_position(avg_entry=100.0, stop=90.0)
    position.high_water = 125.0
    stop = strategy.next_stop(
        portfolio, position, price=125.0, atr=5.0, regime=Regime.BULLISH
    )
    assert stop == pytest.approx(115.0)


def test_stop_never_moves_down():
    portfolio = make_portfolio()
    position = make_position(avg_entry=100.0, stop=90.0)
    position.stop_price = 108.0
    position.high_water = 125.0
    stop = strategy.next_stop(
        portfolio, position, price=112.0, atr=20.0, regime=Regime.BULLISH
    )
    assert stop >= 108.0


def test_risk_off_tightens_the_stop_on_a_position_still_below_one_r():
    portfolio = make_portfolio()
    position = make_position(avg_entry=100.0, stop=90.0)
    stop = strategy.next_stop(
        portfolio, position, price=104.0, atr=5.0, regime=Regime.BEARISH
    )
    assert stop == pytest.approx(99.0)


# --------------------------------------------------------------------------- #
# Entry eligibility and rotation
# --------------------------------------------------------------------------- #

def test_entry_is_rejected_for_a_name_already_held():
    portfolio = make_portfolio()
    ok, why = strategy.is_entry_eligible(
        portfolio, make_candidate(), Regime.BULLISH, ['AAA'], {}, 0
    )
    assert not ok and 'already held' in why


def test_entry_is_rejected_once_the_book_is_full():
    portfolio = make_portfolio(max_positions=2)
    ok, why = strategy.is_entry_eligible(
        portfolio, make_candidate(), Regime.BULLISH, [], {}, 2
    )
    assert not ok and 'position limit' in why


def test_entry_is_rejected_below_the_score_threshold():
    portfolio = make_portfolio(entry_score=80.0)
    ok, why = strategy.is_entry_eligible(
        portfolio, make_candidate(score=70.0), Regime.BULLISH, [], {}, 0
    )
    assert not ok and 'score' in why


def test_entry_is_rejected_without_a_trend():
    portfolio = make_portfolio()
    ok, why = strategy.is_entry_eligible(
        portfolio, make_candidate(trend_ok=False), Regime.BULLISH, [], {}, 0
    )
    assert not ok and 'trend' in why


def test_equity_entry_is_rejected_in_a_risk_off_regime():
    portfolio = make_portfolio()
    ok, why = strategy.is_entry_eligible(
        portfolio, make_candidate(), Regime.BEARISH, [], {}, 0
    )
    assert not ok and 'risk-off' in why


def test_neutral_regime_demands_a_stronger_signal():
    portfolio = make_portfolio(entry_score=60.0)
    marginal = make_candidate(score=62.0)
    strong = make_candidate(score=75.0)

    ok, _ = strategy.is_entry_eligible(
        portfolio, marginal, Regime.NEUTRAL, [], {}, 0
    )
    assert not ok
    ok, _ = strategy.is_entry_eligible(portfolio, strong, Regime.NEUTRAL, [], {}, 0)
    assert ok


def test_sector_cap_blocks_a_third_name_in_one_sector():
    portfolio = make_portfolio(max_per_sector=2)
    ok, why = strategy.is_entry_eligible(
        portfolio, make_candidate(), Regime.BULLISH, [], {'Technology': 2}, 0
    )
    assert not ok and 'sector cap' in why


def test_rotation_replaces_a_clearly_weaker_holding():
    portfolio = make_portfolio(rotation_edge=15.0)
    weak = make_position(symbol='WEAK')
    displaced = strategy.find_rotation(
        portfolio, make_candidate(score=80.0), [weak],
        scores={'WEAK': 40.0}, prices={'WEAK': 101.0},
    )
    assert displaced is weak


def test_rotation_declines_when_the_edge_is_too_small():
    portfolio = make_portfolio(rotation_edge=15.0)
    weak = make_position(symbol='WEAK')
    displaced = strategy.find_rotation(
        portfolio, make_candidate(score=50.0), [weak],
        scores={'WEAK': 45.0}, prices={'WEAK': 101.0},
    )
    assert displaced is None


def test_rotation_never_displaces_a_position_already_running():
    portfolio = make_portfolio(rotation_edge=15.0)
    winner = make_position(symbol='WIN', avg_entry=100.0, stop=90.0)
    displaced = strategy.find_rotation(
        portfolio, make_candidate(score=95.0), [winner],
        scores={'WIN': 20.0}, prices={'WIN': 115.0},
    )
    assert displaced is None
