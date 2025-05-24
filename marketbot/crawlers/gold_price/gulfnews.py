from bs4 import BeautifulSoup

from marketbot.crawlers.base import Crawler
from marketbot.dto.gold_price import GoldPriceResult


class GulfNews(Crawler[GoldPriceResult]):
    @property
    def name(self) -> str:
        return 'Gulf News'

    def run(self) -> GoldPriceResult:
        resp = self._session.get(
            'https://gulfnews.com/gold-forex/historical-gold-rates'
        )

        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'lxml')
        tds = [td.text for td in soup.select('#container table tr td')]

        today = float(tds[1]) * self._ctx.config.gold.xau_gram
        yesterday = float(tds[6]) * self._ctx.config.gold.xau_gram

        return GoldPriceResult(
            name=self.name,
            link='https://gulfnews.com/gold-forex/historical-gold-rates',
            price=today,
            change=today - yesterday
        )
