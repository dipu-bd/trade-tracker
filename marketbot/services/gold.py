import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Generator, List

from marketbot.context import ServerContext
from marketbot.crawlers import run_crawler
from marketbot.crawlers.gold import crawlers
from marketbot.dto.gold_price import GoldPriceResult

_log = logging.getLogger(__name__)


class GoldPriceService:
    def __init__(self, ctx: ServerContext):
        self._ctx = ctx
        self._executor = ThreadPoolExecutor(10)

    def close(self):
        self._executor.shutdown(False, True)

    def get_latest_gold_prices(self) -> Generator[GoldPriceResult, None, None]:
        futures: List[Future[GoldPriceResult]] = []
        for create_crawler in crawlers:
            crawler = create_crawler(self._ctx)
            futures.append(self._executor.submit(run_crawler, crawler))
        for future in futures:
            try:
                yield future.result()
            except BaseException as e:
                _log.error(f'[{crawler.name}] Failed to get data.', e)
