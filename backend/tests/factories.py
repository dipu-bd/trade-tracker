from datetime import date, timedelta

from tradebot.analytics.series import Bar, BarSeries

START = date(2024, 1, 1)


def series(
    symbol: str,
    closes: list[float],
    asset_class: str = "stock",
    volume: float = 2_000_000.0,
    spread: float = 0.01,
    start: date = START,
) -> BarSeries:
    bars = [
        Bar(
            bar_date=start + timedelta(days=index),
            open=close,
            high=close * (1 + spread),
            low=close * (1 - spread),
            close=close,
            volume=volume,
        )
        for index, close in enumerate(closes)
    ]
    return BarSeries(symbol=symbol, asset_class=asset_class, bars=tuple(bars))


def trending(symbol: str, count: int = 320, daily: float = 0.001, **kwargs: float) -> BarSeries:
    return series(symbol, [100.0 * (1 + daily) ** index for index in range(count)], **kwargs)  # type: ignore[arg-type]


def falling(symbol: str, count: int = 320, daily: float = 0.001, **kwargs: float) -> BarSeries:
    return trending(symbol, count, -daily, **kwargs)


def choppy(symbol: str, count: int = 320, amplitude: float = 0.02, **kwargs: float) -> BarSeries:
    closes = [100.0 * (1 + amplitude * (1 if index % 2 else -1)) for index in range(count)]
    return series(symbol, closes, **kwargs)  # type: ignore[arg-type]
