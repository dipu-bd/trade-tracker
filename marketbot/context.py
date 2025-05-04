from functools import cached_property
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

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


def verify_token(
    token: str = Depends(APIKeyHeader(name='x-access-token')),
) -> None:
    ctx = ServerContext()
    api_token = ctx.config.server.api_token
    if token != api_token:
        raise HTTPException(401, 'Invalid token')
