from marketbot.crawlers.base import Crawler
from marketbot.dto.gold_price import GoldPriceResult


class MetalpriceAPI(Crawler[GoldPriceResult]):
    @property
    def name(self) -> str:
        return 'MetalpriceAPI'

    def run(self) -> GoldPriceResult:
        resp = self._session.get(
            'https://api.metalpriceapi.com/v1/latest',
            params={
                'base': 'AED',
                'currencies': 'XAU',
                'api_key': self._ctx.config.gold.metalprice_token,
            }
        )

        resp.raise_for_status()
        data = resp.json()

        return GoldPriceResult(
            name=self.name,
            link='https://metalpriceapi.com/',
            price=float(data['rates']['AEDXAU']),
        )
