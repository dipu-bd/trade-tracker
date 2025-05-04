from typing import Dict, List, MutableMapping, Type

from cachetools import TTLCache

from ._base import Crawler, Result
from .goldapi_io import GoldAPI
from .goldprice_org import GoldPrice
from .gulfnews import GulfNews
from .igold import IGoldAE
from .metalpriceapi import MetalpriceAPI

crawlers: List[Type[Crawler]] = [
    GoldAPI,
    GoldPrice,
    GulfNews,
    IGoldAE,
    MetalpriceAPI,
]


_fail_tolerance = 10
_fail_count: Dict[str, int] = {}
_cache: MutableMapping = TTLCache(100, 10)
_disabled: MutableMapping = TTLCache(100, 3600)


def run_crawler(crawler: Crawler) -> Result:
    k = crawler.name
    if k in _cache:
        return _cache[k]
    if k in _disabled:
        raise Exception('Disabled')

    fc = _fail_count.setdefault(k, 0)
    try:
        result = crawler.run()
        _fail_count[k] = 0
        _cache[k] = result
        return result
    except Exception:
        if fc > _fail_tolerance:
            _disabled[k] = True
        else:
            _fail_count[k] = fc + 1
        raise
