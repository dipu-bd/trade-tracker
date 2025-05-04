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
        self._executor.shutdown(False, True)

    def get_latest_gold_prices(self) -> Generator[dict, None, None]:
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

    def build_slack_message(self, result: dict) -> dict:
        lines = []
        for item in result:
            try:
                text = f":coin: 1 XAU = *{item['price']}*  "
                text += f"_<{item['link']}|{item['name']}>_  "
                if item['change']:
                    text += '⏷' if item['change'][0] == '-' else '⏶'
                    text += f" *{item['change'][1:]}*"
                lines.append(text.strip())
            except BaseException as e:
                _log.error(f"[{item['name']}] Failed to get data.", e)

        message = '\n'.join(lines)
        return {
            "type": "mrkdwn",
            "text": message
        }

    def send_slack_alert(self, data: dict[str, str]) -> None:
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
