from marketbot.crawlers.base import Crawler
from marketbot.dto.gold_price import GoldPriceResult


class IGoldAE(Crawler[GoldPriceResult]):
    @property
    def name(self) -> str:
        return 'iGold.ae'

    def run(self) -> GoldPriceResult:
        resp = self._session.get(
            'https://igold.ae/prices/ajax',
            params={
                'url': 'get-graph-points',
                'metal': 'XAU',
                'currency': 'AED',
                'range': '10m',
            }
        )

        data = resp.json()
        last = float(data['last'][1])
        first = float(data['data'][0][1])

        return GoldPriceResult(
            name=self.name,
            link='https://igold.ae/gold-rate/24-carat/',
            price=last,
            change=last - first,
        )
