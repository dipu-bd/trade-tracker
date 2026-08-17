from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from tradebot.providers.base import EQUITY_CLASSES, AssetClass

NYSE_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


@lru_cache(maxsize=1)
def _nyse():  # type: ignore[no-untyped-def]
    import exchange_calendars

    return exchange_calendars.get_calendar("XNYS")


def is_24x7(asset_class: AssetClass) -> bool:
    return asset_class not in EQUITY_CLASSES


def is_trading_day(day: date, asset_class: AssetClass = AssetClass.STOCK) -> bool:
    if is_24x7(asset_class):
        return True
    return bool(_nyse().is_session(day))


def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    """Regular-hours open and close in UTC, or None when the exchange is shut."""
    if not _nyse().is_session(day):
        return None
    open_at = datetime.combine(day, REGULAR_OPEN, tzinfo=NYSE_TZ)
    close_at = datetime.combine(day, REGULAR_CLOSE, tzinfo=NYSE_TZ)
    return open_at.astimezone(UTC), close_at.astimezone(UTC)


def is_open(moment: datetime, asset_class: AssetClass = AssetClass.STOCK) -> bool:
    if is_24x7(asset_class):
        return True
    bounds = session_bounds(moment.astimezone(NYSE_TZ).date())
    if bounds is None:
        return False
    open_at, close_at = bounds
    return open_at <= moment.astimezone(UTC) < close_at


def last_close(moment: datetime) -> datetime:
    """End of the most recent completed regular session, at or before `moment`."""
    today = moment.astimezone(NYSE_TZ).date()
    bounds = session_bounds(today)
    if bounds is not None and moment.astimezone(UTC) >= bounds[1]:
        return bounds[1]
    cursor = previous_trading_day(today)
    while True:
        bounds = session_bounds(cursor)
        if bounds is not None:
            return bounds[1]
        cursor = previous_trading_day(cursor)


def previous_trading_day(day: date, asset_class: AssetClass = AssetClass.STOCK) -> date:
    if is_24x7(asset_class):
        return day - timedelta(days=1)
    cursor = day - timedelta(days=1)
    while not is_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def next_trading_day(day: date, asset_class: AssetClass = AssetClass.STOCK) -> date:
    if is_24x7(asset_class):
        return day + timedelta(days=1)
    cursor = day + timedelta(days=1)
    while not is_trading_day(cursor):
        cursor += timedelta(days=1)
    return cursor


def trading_days_between(
    start: date, end: date, asset_class: AssetClass = AssetClass.STOCK
) -> list[date]:
    """Inclusive on both ends."""
    if is_24x7(asset_class):
        span = (end - start).days
        return [start + timedelta(days=offset) for offset in range(span + 1)]
    sessions = _nyse().sessions_in_range(start, end)
    return [session.date() if hasattr(session, "date") else session for session in sessions]


def expected_session_count(start: date, end: date, asset_class: AssetClass) -> int:
    return len(trading_days_between(start, end, asset_class))
