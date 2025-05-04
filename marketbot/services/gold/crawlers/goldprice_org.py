import logging

from ._base import Crawler, Result

_log = logging.getLogger(__name__)


class GoldPrice(Crawler):
    @property
    def name(self) -> str:
        return 'GoldPrice.org'

    @property
    def link(self) -> str:
        return 'https://goldprice.org/'

    def run(self) -> Result:
        resp = self._session.get(
            'https://data-asg.goldprice.org/dbXRates/AED',
            headers={
                'referer': 'https://goldprice.org/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',  # noqa: E501
            }
        )

        data = resp.json()
        item = data['items'][0]
        _log.debug(data)

        return Result(item['xauPrice'], item['chgXau'])
