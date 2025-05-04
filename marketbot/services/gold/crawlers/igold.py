import logging

from ._base import Crawler, Result

_log = logging.getLogger(__name__)


class IGoldAE(Crawler):
    @property
    def name(self) -> str:
        return 'iGold.ae'

    @property
    def link(self) -> str:
        return 'https://igold.ae/gold-rate/24-carat/'

    def run(self) -> Result:
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
        _log.debug(data)

        last = data['last'][1]
        first = data['data'][0][1]
        return Result(last, last - first)
