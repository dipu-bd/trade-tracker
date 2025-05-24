from bs4 import BeautifulSoup

from marketbot.crawlers.base import Crawler
from marketbot.dto.gold_price import GoldPriceResult


class DubaiCityOfGold(Crawler[GoldPriceResult]):
    @property
    def name(self) -> str:
        return 'Dubai City Of Gold'

    def run(self) -> GoldPriceResult:
        resp = self._session.get(
            'https://dubaicityofgold.com/',
        )

        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'lxml')
        vendor_key = [
            x.get('vendor-key')
            for x in soup.select('#dcog-canvas')
        ][0]

        resp = self._session.post(
            "https://dubaicityofgold.com/gold-rate-app/dcoggoldrate",
            data=f"vendor_key={vendor_key}",
            headers={
                "accept": "application/json",
                "accept-language": "en,bn;q=0.9",
                "content-type": "application/x-www-form-urlencoded",
            },
        )

        resp.raise_for_status()
        data = resp.json()
        today = float(data['gold_rate_24k']) * self._ctx.config.gold.xau_gram

        return GoldPriceResult(
            name=self.name,
            link='https://dubaicityofgold.com/',
            price=today,
        )
