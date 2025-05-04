from typing import Dict, MutableMapping, TypeVar

from cachetools import TTLCache

from .base import Crawler

T = TypeVar('T')

_fail_tolerance = 10
_fail_count: Dict[str, int] = {}
_cache: MutableMapping = TTLCache(100, 10)
_disabled: MutableMapping = TTLCache(100, 3600)


def run_crawler(crawler: Crawler[T]) -> T:
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
