from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tradebot.analytics.indicators import CRYPTO_DAYS, TRADING_DAYS


@dataclass(frozen=True, slots=True)
class Bar:
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class BarSeries:
    """An instrument's daily history, oldest first, as plain floats.

    Money stays `Decimal` at the broker boundary; indicator inputs do not need to, and a
    `Decimal` square root would buy precision that the underlying estimates do not have.
    """

    symbol: str
    asset_class: str
    bars: tuple[Bar, ...]

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def periods_per_year(self) -> int:
        return CRYPTO_DAYS if self.asset_class == "crypto" else TRADING_DAYS

    @property
    def closes(self) -> list[float]:
        return [bar.close for bar in self.bars]

    @property
    def highs(self) -> list[float]:
        return [bar.high for bar in self.bars]

    @property
    def lows(self) -> list[float]:
        return [bar.low for bar in self.bars]

    @property
    def volumes(self) -> list[float]:
        return [bar.volume for bar in self.bars]

    @property
    def last_close(self) -> float:
        return self.bars[-1].close if self.bars else 0.0

    @property
    def last_date(self) -> date | None:
        return self.bars[-1].bar_date if self.bars else None

    def tail(self, count: int) -> "BarSeries":
        return BarSeries(self.symbol, self.asset_class, self.bars[-count:])

    def through(self, cutoff: date) -> "BarSeries":
        """Only bars dated on or before `cutoff` — the series-level look-ahead guard."""
        return BarSeries(
            self.symbol, self.asset_class, tuple(b for b in self.bars if b.bar_date <= cutoff)
        )


def build_series(
    symbol: str,
    asset_class: str,
    rows: list[tuple[date, Decimal, Decimal, Decimal, Decimal, Decimal]],
) -> BarSeries:
    """Convert stored bar rows, oldest first, into a pure series."""
    return BarSeries(
        symbol=symbol,
        asset_class=asset_class,
        bars=tuple(
            Bar(
                bar_date=row[0],
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in sorted(rows, key=lambda row: row[0])
        ),
    )
