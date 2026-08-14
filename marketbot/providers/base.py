import logging
from abc import ABCMeta, abstractmethod
from typing import Dict, Iterable, List, Sequence

from marketbot.dto.market import Bar, Quote, UniverseEntry

_log = logging.getLogger(__name__)


class RequestBudget:
    """A per-day cap on outbound API calls.

    Free-tier data plans are metered, so every provider call goes through
    `try_spend`. When the budget is exhausted the provider degrades to cached
    data rather than erroring — a scan on stale bars beats no scan at all.
    """

    def __init__(self, limit: int, used: int = 0):
        self.limit = max(limit, 0)
        self.used = max(used, 0)
        self._start = self.used

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def spent(self) -> int:
        return self.used - self._start

    def try_spend(self, count: int = 1) -> bool:
        if self.limit <= 0:
            return True  # unmetered
        if self.used + count > self.limit:
            return False
        self.used += count
        return True


class UnlimitedBudget(RequestBudget):
    def __init__(self):
        super().__init__(limit=0)


class MarketDataProvider(metaclass=ABCMeta):
    """Uniform read-only view of one asset class family."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def asset_classes(self) -> Sequence[str]:
        pass

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def list_universe(self) -> List[UniverseEntry]:
        pass

    @abstractmethod
    def get_quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        pass

    @abstractmethod
    def get_daily_bars(self, symbol: str, days: int = 260) -> List[Bar]:
        pass
