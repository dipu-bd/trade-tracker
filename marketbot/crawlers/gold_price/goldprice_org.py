from marketbot.crawlers.base import Crawler
from marketbot.dto.gold_price import GoldPriceResult


class GoldPrice(Crawler[GoldPriceResult]):
    @property
    def name(self) -> str:
        return 'GoldPrice.org'

    @property
    def link(self) -> str:
        return 'https://goldprice.org/'

    def run(self) -> GoldPriceResult:
        resp = self._session.get(
            'https://data-asg.goldprice.org/dbXRates/AED',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',  # noqa: E501
            }
        )

        data = resp.json()
        item = data['items'][0]

        return GoldPriceResult(
            name=self.name,
            link='https://goldprice.org/',
            price=float(item['xauPrice']),
            change=float(item['chgXau']),
        )
