from marketbot.crawlers.base import Crawler
from marketbot.dto.gold_price import GoldPriceResult


class GoldAPI(Crawler[GoldPriceResult]):
    @property
    def name(self) -> str:
        return 'GoldAPI.io'

    def run(self) -> GoldPriceResult:
        resp = self._session.get(
            'https://www.goldapi.io/api/XAU/AED',
            headers={
                'x-access-token': self._ctx.config.gold.goldapi_token,
            }
        )
        data = resp.json()
        return GoldPriceResult(
            name=self.name,
            link='https://www.goldapi.io/',
            price=float(data['price']),
            change=float(data['ch']),
        )
