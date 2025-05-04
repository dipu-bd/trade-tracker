import json
import logging
from typing import Any, Iterable

import requests
from fastapi import HTTPException

from marketbot.context import ServerContext
from marketbot.dto.gold_price import GoldPriceResult

_log = logging.getLogger(__name__)


class SlackMessagingService:
    def __init__(self, ctx: ServerContext):
        self._ctx = ctx

    def send_slack_alert(self, data: dict[str, Any]) -> None:
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
            raise HTTPException(500, 'Failed to send slack alerts.')

    def build_message_for_gold_prices(
        self,
        results: Iterable[GoldPriceResult]
    ) -> dict[str, str]:
        lines = []
        for item in results:
            try:
                text = f":coin: 1 XAU = *AED {item.price:,.2f}*  "
                if item.change < 0:
                    text += f":arrow_down_small: *{item.change:,.2f}*"
                elif item.change > 0:
                    text += f":arrow_up_small: *+{item.change:,.2f}*"
                text += f"  _<{item.link}|{item.name}>_"
                lines.append(text.strip())
            except BaseException as e:
                _log.error(f"[{item.name}] Failed to get data.", e)
        return {
            "type": "mrkdwn",
            "text": '\n'.join(lines)
        }
