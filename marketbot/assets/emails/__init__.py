from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent


def _money(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return '—'
    # Crypto trades at prices where two decimals rounds away the whole move.
    decimals = 2 if abs(value) >= 1 else 4
    return f'${value:,.{decimals}f}'


def _signed_money(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return '—'
    return ('+' if value >= 0 else '-') + _money(abs(value))


def _signed(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return '—'
    return f'{value:+.2f}'


def _qty(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return '—'
    if value == int(value):
        return f'{int(value):,}'
    return f'{value:,.6f}'.rstrip('0').rstrip('.')


@lru_cache(1)
def get_jinja2_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(ROOT)),
        autoescape=select_autoescape(['html', 'xml']),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters['money'] = _money
    env.filters['signed_money'] = _signed_money
    env.filters['signed'] = _signed
    env.filters['qty'] = _qty
    return env


@lru_cache(1)
def portfolio_event_template():
    return get_jinja2_env().get_template('portfolio_event.html.j2')


@lru_cache(1)
def daily_digest_template():
    return get_jinja2_env().get_template('daily_digest.html.j2')


@lru_cache(1)
def risk_alert_template():
    return get_jinja2_env().get_template('risk_alert.html.j2')
