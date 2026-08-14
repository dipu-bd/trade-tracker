from functools import cached_property
from typing import Optional

from .utils.decorators import autoclose

_cache: Optional['ServerContext'] = None


class ServerContext:
    def __new__(cls):
        global _cache
        if _cache is None:
            _cache = super().__new__(cls)
        return _cache

    @cached_property
    def config(self):
        from .config import Config
        return Config()

    @cached_property
    @autoclose
    def gold(self):
        from .services import GoldPriceService
        return GoldPriceService(self)

    @cached_property
    @autoclose
    def db(self):
        from .db import Database
        return Database(self.config.database.url, self.config.database.echo)

    @cached_property
    def market_data(self):
        from .services import MarketDataService
        return MarketDataService(self)

    @cached_property
    def portfolios(self):
        from .services import PortfolioService
        return PortfolioService(self)

    @cached_property
    def engine(self):
        from .services import EngineService
        return EngineService(self)

    @cached_property
    @autoclose
    def mail(self):
        from .services import MailService
        return MailService(self)

    @cached_property
    def notifier(self):
        from .services import NotifierService
        return NotifierService(self)

    @cached_property
    @autoclose
    def scheduler(self):
        from .services import SchedulerService
        return SchedulerService(self)

    @cached_property
    def advisor(self):
        """The LLM reviewer, or None when it is switched off or unconfigured."""
        from .advisors import build_advisor
        return build_advisor(self.config.advisor)
