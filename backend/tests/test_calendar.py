from datetime import UTC, date, datetime

from tradebot.marketdata import calendar
from tradebot.providers.base import AssetClass

# The day after Thanksgiving 2025: a real session, but the bell is at 13:00 in New York.
HALF_DAY = date(2025, 11, 28)
FULL_DAY = date(2025, 11, 26)
HOLIDAY = date(2025, 11, 27)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def test_a_full_session_runs_to_the_regular_close() -> None:
    bounds = calendar.session_bounds(FULL_DAY)
    assert bounds is not None
    assert bounds[0] == at(FULL_DAY, 14, 30)
    assert bounds[1] == at(FULL_DAY, 21)


def test_an_early_close_is_not_stretched_to_a_full_session() -> None:
    bounds = calendar.session_bounds(HALF_DAY)
    assert bounds is not None
    assert bounds[1] == at(HALF_DAY, 18)


def test_the_exchange_is_shut_after_an_early_close() -> None:
    assert calendar.is_open(at(HALF_DAY, 17))
    assert not calendar.is_open(at(HALF_DAY, 19))


def test_a_holiday_has_no_session() -> None:
    assert calendar.session_bounds(HOLIDAY) is None
    assert not calendar.is_open(at(HOLIDAY, 16))


def test_the_last_close_on_a_half_day_is_its_early_bell() -> None:
    assert calendar.last_close(at(HALF_DAY, 19)) == at(HALF_DAY, 18)


def test_crypto_never_closes() -> None:
    assert calendar.is_open(at(HOLIDAY, 3), AssetClass.CRYPTO)
    assert calendar.is_open(at(HALF_DAY, 23), AssetClass.CRYPTO)
