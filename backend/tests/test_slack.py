import httpx
import respx
from httpx import AsyncClient

from tests.test_ai_routes import PORTFOLIO
from tradebot.obs.bus import BusEvent, EventBus
from tradebot.obs.slack import NOTIFIED, SlackNotifier, format_event

HOOK = "https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxxxxxxxxxxxxxx"


class StubLookup:
    def __init__(self, url: str = HOOK) -> None:
        self.url = url

    async def for_portfolio(self, portfolio_id: int) -> str:
        return self.url


def test_a_fill_renders_with_its_numbers() -> None:
    text = format_event(
        BusEvent(
            domain="broker",
            kind="order_filled",
            portfolio_id=1,
            message="BUY 10 AAA @ 100",
            payload={"qty": "10", "price": "100", "brief": "ignored"},
        )
    )

    assert "order filled" in text
    assert "BUY 10 AAA @ 100" in text
    assert "`qty=10`" in text
    assert "ignored" not in text, "only whitelisted payload keys reach Slack"


def test_the_notified_set_excludes_high_volume_noise() -> None:
    assert "order_filled" in NOTIFIED
    assert "cycle_finished" in NOTIFIED
    assert "kill_switch" in NOTIFIED
    assert "tick_received" not in NOTIFIED
    assert "provider_request" not in NOTIFIED


@respx.mock
async def test_a_notified_event_posts_to_the_webhook() -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200))
    notifier = SlackNotifier(EventBus(), StubLookup())
    try:
        await notifier.deliver(
            BusEvent(domain="broker", kind="order_filled", portfolio_id=1, message="BUY 1 AAA")
        )
    finally:
        await notifier.aclose()

    assert route.called
    assert "BUY 1 AAA" in route.calls[0].request.content.decode()


@respx.mock
async def test_a_portfolio_without_a_webhook_posts_nothing() -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200))
    notifier = SlackNotifier(EventBus(), StubLookup(""))
    try:
        await notifier.deliver(BusEvent(domain="broker", kind="order_filled", portfolio_id=1))
    finally:
        await notifier.aclose()

    assert not route.called


@respx.mock
async def test_a_slack_outage_never_escapes_into_the_trading_path() -> None:
    """The bus loop swallows delivery failures: a dead webhook must not fail a cycle."""
    respx.post(HOOK).mock(side_effect=httpx.ConnectError("slack is down"))
    bus = EventBus()
    notifier = SlackNotifier(bus, StubLookup())
    notifier.start()
    try:
        await bus.publish(BusEvent(domain="broker", kind="order_filled", portfolio_id=1))
        await bus.publish(BusEvent(domain="broker", kind="order_filled", portfolio_id=1))
    finally:
        await notifier.aclose()


async def test_the_webhook_url_is_never_returned_by_the_api(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    pid = created.json()["id"]

    stored = await client.put(
        f"/api/portfolios/{pid}/notifications",
        json={"webhook_url": HOOK},
        headers=registered,
    )
    read = await client.get(f"/api/portfolios/{pid}/notifications", headers=registered)

    assert stored.status_code == 200, stored.text
    assert read.json()["configured"] is True
    assert HOOK not in stored.text
    assert HOOK not in read.text


async def test_a_url_that_is_not_a_slack_webhook_is_rejected(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    pid = created.json()["id"]

    response = await client.put(
        f"/api/portfolios/{pid}/notifications",
        json={"webhook_url": "https://evil.example.com/collect"},
        headers=registered,
    )

    assert response.status_code == 422


async def test_clearing_the_webhook_turns_notifications_off(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    pid = created.json()["id"]
    await client.put(
        f"/api/portfolios/{pid}/notifications", json={"webhook_url": HOOK}, headers=registered
    )

    await client.delete(f"/api/portfolios/{pid}/notifications", headers=registered)
    read = await client.get(f"/api/portfolios/{pid}/notifications", headers=registered)

    assert read.json()["configured"] is False
