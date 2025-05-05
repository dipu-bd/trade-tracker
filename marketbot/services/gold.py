import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List

import requests
from fastapi import HTTPException

from marketbot.context import ServerContext
from marketbot.crawlers import run_crawler
from marketbot.crawlers.gold_price import get_crawlers
from marketbot.dto.gold_price import GoldPriceResult

_log = logging.getLogger(__name__)


class GoldPriceService:
    def __init__(self, ctx: ServerContext):
        self._ctx = ctx
        self._executor = ThreadPoolExecutor(10)

    def close(self):
        self._executor.shutdown(False, cancel_futures=True)

    def get_latest_gold_prices(self) -> List[GoldPriceResult]:
        futures = {
            crawler: self._executor.submit(run_crawler, crawler)
            for crawler in get_crawlers(self._ctx)
        }
        results = []
        for crawler, future in futures.items():
            try:
                results.append(future.result())
            except BaseException as e:
                _log.error(f'<!> {crawler.name} {e}')
        return results

    def build_slack_message(self, results: List[GoldPriceResult]):
        lines = []
        for item in results:
            text = f":coin: 1 XAU = *AED {item.price:,.2f}*  "
            if item.change < 0:
                text += f":arrow_down_small: *{item.change:,.2f}*"
            elif item.change > 0:
                text += f":arrow_up_small: *+{item.change:,.2f}*"
            text += f"  _<{item.link}|{item.name}>_"
            lines.append(text.strip())
        return {
            "type": "mrkdwn",
            "text": '\n'.join(lines)
        }

    def send_slack_alert(self, data: dict):
        resp = requests.post(
            self._ctx.config.gold.slack_webhook_url,
            data=json.dumps(data),
            headers={
                'Content-type': 'application/json',
            }
        )
        if resp.status_code >= 400:
            raise HTTPException(500, 'Failed to send slack alerts.')
        return resp.content
