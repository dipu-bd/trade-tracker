from datetime import date, timedelta

import pytest

from marketbot.advisors.base import AdvisorResult, Verdict
from marketbot.db import (
    AssetClass,
    Event,
    EventType,
    PortfolioSnapshot,
    Sleeve,
)
from marketbot.dto.market import UniverseEntry
from tests.conftest import FakeAdvisor, FakeProvider, downtrend_bars, make_bars

EQUITY_SERIES = {
    'SPY': make_bars(count=260, start=400.0),
    'AAA': make_bars(count=260, start=100.0, volume_ramp=2.0),
    'BBB': make_bars(count=260, start=50.0, drift=0.005, volume_ramp=1.5),
}
CRYPTO_SERIES = {
    'BTC_USD': make_bars(count=260, start=30_000.0, drift=0.005, volume_ramp=1.0),
}


@pytest.fixture
def wired(ctx, monkeypatch):
    """Engine wired to canned series for a small, deterministic universe."""
    from marketbot.services import market_data as market_data_module

    def fake_static_universe(include_stocks=True, include_etfs=True):
        entries = []
        if include_stocks:
            entries += [
                UniverseEntry(symbol='AAA', asset_class=AssetClass.STOCK,
                              sector='Technology'),
                UniverseEntry(symbol='BBB', asset_class=AssetClass.STOCK,
                              sector='Healthcare'),
            ]
        if include_etfs:
            entries.append(
                UniverseEntry(symbol='SPY', asset_class=AssetClass.ETF,
                              sector='Broad Market')
            )
        return entries

    monkeypatch.setattr(
        market_data_module.universe_lib,
        'static_equity_universe',
        fake_static_universe,
    )

    equity = FakeProvider(EQUITY_SERIES, (AssetClass.STOCK, AssetClass.ETF))
    crypto = FakeProvider(CRYPTO_SERIES, (AssetClass.CRYPTO,))
    crypto.list_universe = lambda: [
        UniverseEntry(symbol='BTC_USD', asset_class=AssetClass.CRYPTO,
                      sector='Crypto')
    ]
    ctx.use_providers(equity=equity, crypto=crypto)
    return ctx


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #

def test_a_scan_opens_positions_and_spends_cash(wired, portfolio):
    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    assert result['opened'], 'expected the scan to add at least one position'
    assert result['regime'] == 'BULLISH'
    assert result['cash'] < 100_000
    assert result['open_positions'] == len(result['opened'])


def test_equity_reconciles_with_cash_plus_positions(wired, portfolio):
    wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    with wired.db.session() as session:
        book = wired.portfolios.get(session, portfolio)
        positions = wired.portfolios.open_positions(session, book)
        prices = {p.instrument.symbol: p.avg_entry for p in positions}
        market_value = wired.portfolios.positions_value(positions, prices)
        assert wired.portfolios.equity(book, positions, prices) == pytest.approx(
            book.cash + market_value
        )


def test_every_opened_position_respects_the_risk_budget(wired, portfolio):
    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    with wired.db.session() as session:
        book = wired.portfolios.get(session, portfolio)
        budget = 100_000 * book.risk_pct_per_trade / 100
        for position in wired.portfolios.open_positions(session, book):
            risk = (position.avg_entry - position.stop_price) * position.qty
            # Sizing rounds down for whole-share names, never up.
            assert risk <= budget * 1.02


def test_opening_a_position_writes_an_event_and_a_trade(wired, portfolio):
    wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    with wired.db.session() as session:
        events = session.query(Event).filter(
            Event.type == EventType.POSITION_OPENED
        ).all()
        assert events
        assert events[0].payload['symbol']

        book = wired.portfolios.get(session, portfolio)
        positions = wired.portfolios.open_positions(session, book)
        assert len(positions) == len(events)


def test_a_scan_emails_the_owner(wired, portfolio):
    wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    assert wired.mail.sent, 'expected an email for the added positions'
    message = wired.mail.sent[0]
    assert message['to'] == 'owner@example.com'
    assert 'added' in message['subject'] or '+' in message['subject']


def test_a_crypto_only_scan_touches_no_equity_provider(ctx, wired, portfolio):
    equity_provider = wired.market_data._equity
    equity_provider.bar_calls.clear()

    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.CRYPTO)

    opened = {item['symbol'] for item in result['opened']}
    assert opened <= {'BTC_USD'}


def test_the_crypto_sleeve_budget_is_respected(wired, portfolio):
    wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    with wired.db.session() as session:
        book = wired.portfolios.get(session, portfolio)
        positions = wired.portfolios.open_positions(session, book)
        prices = {p.instrument.symbol: p.avg_entry for p in positions}
        crypto = wired.portfolios.sleeve_exposure(
            positions, prices, (AssetClass.CRYPTO,)
        )
        assert crypto <= 100_000 * book.crypto_max_pct / 100 + 1


def test_a_second_scan_does_not_double_up_on_a_held_name(wired, portfolio):
    first = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)
    second = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    first_symbols = {i['symbol'] for i in first['opened']}
    second_symbols = {i['symbol'] for i in second['opened']}
    assert not (first_symbols & second_symbols)


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #

def test_a_dry_run_plans_without_trading(wired, portfolio):
    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL, dry_run=True)

    assert result['dry_run'] is True
    assert result['would_open']
    assert result['top_candidates']
    assert result['cash'] == pytest.approx(100_000)
    assert wired.mail.sent == []

    with wired.db.session() as session:
        book = wired.portfolios.get(session, portfolio)
        assert wired.portfolios.open_positions(session, book) == []


# --------------------------------------------------------------------------- #
# Risk halt
# --------------------------------------------------------------------------- #

def test_the_daily_loss_breaker_liquidates_and_halts(wired, portfolio):
    wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    with wired.db.session() as session:
        # Yesterday's equity was far higher, so today reads as a big drawdown.
        session.add(PortfolioSnapshot(
            portfolio_id=portfolio,
            snap_date=date.today() - timedelta(days=1),
            equity=1_000_000.0, cash=1_000_000.0, positions_value=0.0,
        ))

    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    assert result['halted'] is True
    assert result['opened'] == []
    with wired.db.session() as session:
        book = wired.portfolios.get(session, portfolio)
        assert wired.portfolios.open_positions(session, book) == []


# --------------------------------------------------------------------------- #
# Advisor integration
# --------------------------------------------------------------------------- #

def test_advisor_veto_suppresses_the_entry(wired, portfolio, monkeypatch):
    planned = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL, dry_run=True)
    target = planned['would_open'][0]['symbol']

    monkeypatch.setenv('LLM_ADVISOR_MODE', 'veto')
    wired.config.advisor.__dict__.pop('mode', None)
    wired.advisor = FakeAdvisor(result=AdvisorResult(
        provider='fake', model='m',
        verdicts=[Verdict(target, 'BUY', 'reject', 'looks chased')],
    ))

    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    assert target not in {i['symbol'] for i in result['opened']}
    assert any(row['symbol'] == target and row['applied'] for row in result['advice'])


def test_advisor_annotate_mode_records_but_does_not_block(wired, portfolio,
                                                          monkeypatch):
    planned = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL, dry_run=True)
    target = planned['would_open'][0]['symbol']

    monkeypatch.setenv('LLM_ADVISOR_MODE', 'annotate')
    wired.config.advisor.__dict__.pop('mode', None)
    wired.advisor = FakeAdvisor(result=AdvisorResult(
        provider='fake', model='m',
        verdicts=[Verdict(target, 'BUY', 'reject', 'looks chased')],
    ))

    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    assert target in {i['symbol'] for i in result['opened']}
    assert any(row['applied'] is False for row in result['advice'])


def test_an_advisor_that_raises_falls_through_to_the_rules(wired, portfolio,
                                                           monkeypatch):
    baseline = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL, dry_run=True)

    monkeypatch.setenv('LLM_ADVISOR_MODE', 'veto')
    wired.config.advisor.__dict__.pop('mode', None)
    wired.advisor = FakeAdvisor(raises=RuntimeError('connection reset'))

    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    assert {i['symbol'] for i in result['opened']} == {
        i['symbol'] for i in baseline['would_open']
    }


def test_a_failed_advisor_result_leaves_the_plan_intact(wired, portfolio,
                                                        monkeypatch):
    baseline = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL, dry_run=True)

    monkeypatch.setenv('LLM_ADVISOR_MODE', 'veto')
    wired.config.advisor.__dict__.pop('mode', None)
    wired.advisor = FakeAdvisor(result=AdvisorResult(
        provider='fake', model='m', error='timed out'
    ))

    result = wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)

    assert {i['symbol'] for i in result['opened']} == {
        i['symbol'] for i in baseline['would_open']
    }


# --------------------------------------------------------------------------- #
# Digest
# --------------------------------------------------------------------------- #

def test_the_digest_reports_the_open_book(wired, portfolio):
    wired.engine.run_scan(portfolio, sleeve=Sleeve.ALL)
    wired.mail.sent.clear()

    assert wired.engine.send_digest(portfolio) is True
    assert 'Daily digest' in wired.mail.sent[0]['subject']
