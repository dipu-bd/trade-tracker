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
    def gold_price(self):
        from .services import GoldPriceService
        return GoldPriceService(self)

    @cached_property
    def slack(self):
        from .services import SlackMessagingService
        return SlackMessagingService(self)
