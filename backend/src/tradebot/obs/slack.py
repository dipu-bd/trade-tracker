import asyncio
import contextlib

import httpx

from tradebot.core.logging import get_logger
from tradebot.obs.bus import BusEvent, EventBus

_log = get_logger(__name__)

WEBHOOK_PROVIDER = "slack"
WEBHOOK_FIELD = "webhook_url"

# Everything else stays in the event feed. A channel that reports every tick gets muted, and a
# muted channel reports nothing at all.
NOTIFIED = frozenset(
    {
        "order_filled",
        "order_rejected",
        "cycle_finished",
        "daily_loss_breached",
        "drawdown_alert",
        "kill_switch",
        "reconciliation_failed",
    }
)

ICONS = {
    "order_filled": ":white_check_mark:",
    "order_rejected": ":x:",
    "cycle_finished": ":repeat:",
}


def format_event(event: BusEvent) -> str:
    icon = ICONS.get(event.kind, ":rotating_light:" if event.severity != "info" else ":bell:")
    parts = [f"{icon} *{event.kind.replace('_', ' ')}*"]
    if event.message:
        parts.append(f"— {event.message}")

    detail = {
        key: value
        for key, value in (event.payload or {}).items()
        if key in {"qty", "price", "fee", "regime", "exposure", "entries", "exits", "status"}
    }
    if detail:
        parts.append("\n" + "  ".join(f"`{k}={v}`" for k, v in detail.items()))
    return " ".join(parts)


class SlackNotifier:
    """Forwards a few event kinds to a per-portfolio webhook.

    Subscribed to the bus rather than called from the broker or the engine, so adding a
    notification never means editing trading code — and a Slack outage cannot fail a cycle.
    """

    def __init__(
        self,
        bus: EventBus,
        webhooks: "WebhookLookup",
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bus = bus
        self._webhooks = webhooks
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._client.aclose()

    async def _run(self) -> None:
        async with self._bus.subscribe() as subscription:
            async for event in subscription:
                if event.kind not in NOTIFIED or event.portfolio_id is None:
                    continue
                try:
                    await self.deliver(event)
                except Exception as error:
                    # Never let a notification failure escape into the trading path.
                    _log.warning("slack_delivery_failed", kind=event.kind, error=str(error))

    async def deliver(self, event: BusEvent) -> None:
        if event.portfolio_id is None:
            return
        url = await self._webhooks.for_portfolio(event.portfolio_id)
        if not url:
            return
        await self._client.post(url, json={"text": format_event(event)})


class WebhookLookup:
    """Resolves a portfolio's webhook from the credential vault.

    The URL is a secret — anyone holding it can post into the channel — so it lives encrypted
    beside the API keys rather than in a plain column.
    """

    def __init__(self, context: object) -> None:
        self._context = context

    async def for_portfolio(self, portfolio_id: int) -> str:
        from sqlalchemy import select

        from tradebot.db.models import Credential, Portfolio

        context = self._context
        async with context.db.session() as session:  # type: ignore[attr-defined]
            portfolio = await session.get(Portfolio, portfolio_id)
            if portfolio is None:
                return ""
            record = await session.scalar(
                select(Credential).where(
                    Credential.user_id == portfolio.user_id,
                    Credential.provider_key == WEBHOOK_PROVIDER,
                    Credential.field == WEBHOOK_FIELD,
                    Credential.label == str(portfolio_id),
                )
            )
            if record is None:
                return ""
            secret: str = await context.vault.reveal(  # type: ignore[attr-defined]
                session, credential_id=record.id, user_id=portfolio.user_id
            )
            return secret
