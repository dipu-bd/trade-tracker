import logging

from ._base import Crawler, Result

_log = logging.getLogger(__name__)


class MetalpriceAPI(Crawler):
    @property
    def name(self) -> str:
        return 'MetalpriceAPI'

    @property
    def link(self) -> str:
        return 'https://metalpriceapi.com/'

    def run(self) -> Result:
        resp = self._session.get(
            'https://api.metalpriceapi.com/v1/latest',
            params={
                'base': 'AED',
                'currencies': 'XAU',
                'api_key': self._ctx.config.gold.metalprice_token,
            }
        )

        data = resp.json()
        _log.debug(data)

        return Result(data['rates']['AEDXAU'], None)
