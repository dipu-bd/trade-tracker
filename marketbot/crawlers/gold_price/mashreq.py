from marketbot.crawlers.base import Crawler
from marketbot.crawlers.gold_price.goldprice_org import GoldPrice
from marketbot.dto.gold_price import GoldPriceResult


class Mashreq(GoldPrice, Crawler[GoldPriceResult]):
    @property
    def name(self) -> str:
        return 'Mashreq'

    def run(self) -> GoldPriceResult:
        result = super().run()
        result.price *= 1.0115  # 1.15% charge
        result.name = self.name
        result.link = (
            'https://www.mashreq.com/en/uae/gold/wealth-solutions'
            '/investment/gold-silver-edge-account/'
        )
        return result
