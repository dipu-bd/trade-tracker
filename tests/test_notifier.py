"""Template rendering and delivery routing.

These exercise the real Jinja templates and filters, so a broken template or a
missing variable fails here rather than silently producing an empty email.
"""

from datetime import datetime, timezone

import pytest

from marketbot.db import ScanRun, Sleeve


def make_run(portfolio_id: int) -> ScanRun:
    return ScanRun(
        portfolio_id=portfolio_id,
        sleeve=Sleeve.ALL,
        started_at=datetime.now(timezone.utc),
        regime='BULLISH',
    )


def base_summary() -> dict:
    return {
        'regime': 'BULLISH',
        'equity': 101_500.0,
        'cash': 90_000.0,
        'total_return': 1.5,
        'open_positions': 2,
        'opened': [],
        'closed': [],
        'stop_moves': [],
        'advice': [],
        'advisor_mode': 'off',
        'advisor_model': '',
    }


def opened_item() -> dict:
    return {
        'symbol': 'AAA', 'asset_class': 'STOCK', 'qty': 100.0, 'price': 50.25,
        'stop_price': 46.0, 'target_price': 63.0, 'score': 72.5,
        'flags': ['52W-HIGH'], 'advisor_note': '',
    }


def closed_item() -> dict:
    return {
        'symbol': 'BBB', 'asset_class': 'STOCK', 'qty': 50.0,
        'avg_entry': 30.0, 'price': 33.0, 'realized_pnl': 150.0,
        'r_multiple': 1.5, 'reason': 'take_profit',
    }


def test_nothing_changed_means_no_email(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        assert ctx.notifier.notify_run(book, make_run(portfolio), base_summary()) \
            is False
    assert ctx.mail.sent == []


def test_an_add_renders_the_position_detail(ctx, portfolio):
    summary = base_summary()
    summary['opened'] = [opened_item()]

    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        assert ctx.notifier.notify_run(book, make_run(portfolio), summary) is True

    body = ctx.mail.sent[0]['body']
    assert 'AAA' in body
    assert '$50.25' in body
    assert '52W-HIGH' in body
    assert 'Added' in body


def test_a_removal_renders_the_realised_result(ctx, portfolio):
    summary = base_summary()
    summary['closed'] = [closed_item()]

    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        ctx.notifier.notify_run(book, make_run(portfolio), summary)

    body = ctx.mail.sent[0]['body']
    assert 'BBB' in body
    assert 'take profit' in body
    assert '+$150.00' in body


def test_the_subject_names_what_changed(ctx, portfolio):
    summary = base_summary()
    summary['opened'] = [opened_item()]
    summary['closed'] = [closed_item()]

    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        ctx.notifier.notify_run(book, make_run(portfolio), summary)

    subject = ctx.mail.sent[0]['subject']
    assert '+AAA' in subject and '-BBB' in subject
    assert 'Test Book' in subject


def test_per_event_mode_sends_one_email_per_change(ctx, portfolio, monkeypatch):
    monkeypatch.setenv('NOTIFY_MODE', 'per_event')
    ctx.config.mail.__dict__.pop('notify_mode', None)

    summary = base_summary()
    summary['opened'] = [opened_item()]
    summary['closed'] = [closed_item()]

    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        ctx.notifier.notify_run(book, make_run(portfolio), summary)

    assert len(ctx.mail.sent) == 2
    assert 'Added AAA' in ctx.mail.sent[0]['subject']
    assert 'Removed BBB' in ctx.mail.sent[1]['subject']


def test_advisor_notes_appear_in_the_email(ctx, portfolio):
    summary = base_summary()
    summary['opened'] = [opened_item()]
    summary['advisor_mode'] = 'veto'
    summary['advisor_model'] = 'claude-opus-5'
    summary['advice'] = [{
        'symbol': 'CCC', 'proposed_action': 'BUY', 'verdict': 'reject',
        'reason': 'parabolic on thin volume', 'confidence': 0.8, 'applied': True,
    }]

    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        ctx.notifier.notify_run(book, make_run(portfolio), summary)

    body = ctx.mail.sent[0]['body']
    assert 'parabolic on thin volume' in body
    assert 'claude-opus-5' in body
    assert '(applied)' in body


def test_the_digest_renders_the_book(ctx, portfolio):
    summary = {
        'as_of': '01 Jan 2026 21:30 UTC',
        'regime': 'NEUTRAL',
        'equity': 104_000.0, 'cash': 60_000.0,
        'total_return': 4.0, 'day_return': -0.5,
        'realized_pnl': 2_000.0, 'unrealized_pnl': 2_000.0,
        'drawdown_pct': 1.2,
        'positions': [{
            'symbol': 'AAA', 'qty': 100.0, 'avg_entry': 50.0, 'price': 55.0,
            'stop_price': 48.0, 'unrealized': 500.0, 'r_multiple': 2.5,
            'held_days': 6,
        }],
    }

    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        assert ctx.notifier.notify_digest(book, summary) is True

    body = ctx.mail.sent[0]['body']
    assert 'AAA' in body
    assert '+4.00%' in body
    assert '6d' in body


def test_the_digest_handles_an_empty_book(ctx, portfolio):
    summary = {
        'as_of': '01 Jan 2026 21:30 UTC', 'regime': 'BEARISH',
        'equity': 100_000.0, 'cash': 100_000.0, 'total_return': 0.0,
        'day_return': 0.0, 'realized_pnl': 0.0, 'unrealized_pnl': 0.0,
        'drawdown_pct': 0.0, 'positions': [],
    }

    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        ctx.notifier.notify_digest(book, summary)

    assert 'No open positions' in ctx.mail.sent[0]['body']


def test_a_risk_alert_renders(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        assert ctx.notifier.notify_risk(
            book, 'Trading halted', 'Daily loss limit of 6.0% reached',
            {'equity': 94_000.0, 'cash': 94_000.0, 'total_return': -6.0},
        ) is True

    message = ctx.mail.sent[0]
    assert 'Trading halted' in message['subject']
    assert 'Daily loss limit' in message['body']
    assert '-6.00%' in message['body']


def test_crypto_prices_keep_their_precision():
    from marketbot.assets.emails import get_jinja2_env

    money = get_jinja2_env().filters['money']
    assert money(0.0421) == '$0.0421'
    assert money(1234.5) == '$1,234.50'


def test_quantity_formatting_drops_trailing_zeros():
    from marketbot.assets.emails import get_jinja2_env

    qty = get_jinja2_env().filters['qty']
    assert qty(100.0) == '100'
    assert qty(0.5) == '0.5'
    assert qty(0.123456) == '0.123456'
