import json
import logging
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Generator, Dict

import requests

from marketbot.context import ServerContext

from .crawlers import crawlers, run_crawler, Crawler, Result

_log = logging.getLogger(__name__)


class GoldPriceService:
    def __init__(self, ctx: ServerContext):
        self._ctx = ctx
        self._executor = ThreadPoolExecutor(10)

    def close(self):
        self._executor.shutdown(True, True)

    def _send_slack_alert(self, data) -> None:
        try:
            requests.post(
                self._ctx.config.gold.slack_webhook_url,
                data=json.dumps(data),
                headers={
                    'Content-type': 'application/json',
                }
            )
        except BaseException as e:
            _log.error('Failed to send slack alerts.', e)

    def get_latest_prices(self) -> Generator[dict[str, str], None, None]:
        futures: Dict[Future[Result], Crawler] = {}
        for create_crawler in crawlers:
            crawler = create_crawler(self._ctx)
            futures[self._executor.submit(run_crawler, crawler)] = crawler

        for future, crawler in futures.items():
            try:
                result = future.result()
                yield {
                    'name': crawler.name,
                    'link': crawler.link,
                    'price': result.price,
                    'change': result.change,
                }
            except BaseException as e:
                _log.error(f'[{crawler.name}] Failed to get data.', e)

    def send_latest_prices(self) -> str:
        lines = []
        for item in self.get_latest_prices():
            try:
                text = f":coin: 1 XAU = *{item['price']}* AED  "

                if item['change']:
                    change_sign = (
                        ':arrow_down_small:'
                        if item['change'].startswith('-')
                        else ':arrow_up_small:'
                    )
                    text += f"{change_sign} *{item['change']}*  "

                text += f"_<{item['link']}|{item['name']}>_"
                lines.append(text)
            except BaseException as e:
                _log.error(f"[{item['name']}] Failed to get data.", e)

        message = '\n'.join(lines)
        self._send_slack_alert({
            "type": "mrkdwn",
            "text": message
        })

        return message
