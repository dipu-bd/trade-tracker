import logging

from ._base import Crawler, Result

_log = logging.getLogger(__name__)


class GoldAPI(Crawler):
    @property
    def name(self) -> str:
        return 'GoldAPI.io'

    @property
    def link(self) -> str:
        return 'https://www.goldapi.io/'

    def run(self) -> Result:
        resp = self._session.get(
            'https://www.goldapi.io/api/XAU/AED',
            headers={
                'x-access-token': self._ctx.config.gold.goldapi_token,
            }
        )

        data = resp.json()
        _log.debug(data)

        return Result(data['price'], data['ch'])
