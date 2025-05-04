import logging
from bs4 import BeautifulSoup
from ._base import Crawler, Result

_log = logging.getLogger(__name__)


class GulfNews(Crawler):
    @property
    def name(self) -> str:
        return 'Gulf News'

    @property
    def link(self) -> str:
        return 'https://gulfnews.com/gold-forex/historical-gold-rates'

    def run(self) -> Result:
        resp = self._session.get(
            'https://gulfnews.com/gold-forex/historical-gold-rates'
        )

        soup = BeautifulSoup(resp.content, 'lxml')
        tds = [td.text for td in soup.select('#container table tr td')]
        _log.debug(tds)

        today = float(tds[1]) * self._ctx.config.gold.xau_gram
        yesterday = float(tds[6]) * self._ctx.config.gold.xau_gram

        return Result(today, today - yesterday)
