from httpx import AsyncClient

from tradebot.obs.bus import BusEvent, EventBus


async def test_registering_records_an_event(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/events", headers=registered)
    assert response.status_code == 200
    assert {row["kind"] for row in response.json()} >= {"user_registered", "login"}


async def test_events_can_be_filtered_by_kind(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/events?kind=login", headers=registered)
    assert [row["kind"] for row in response.json()] == ["login"]


async def test_events_are_scoped_to_the_requesting_user(client: AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "password-long-enough", "display_name": "A"},
    )
    await client.post(
        "/api/auth/register",
        json={"email": "b@example.com", "password": "password-long-enough", "display_name": "B"},
    )
    login = await client.post(
        "/api/auth/login", json={"email": "b@example.com", "password": "password-long-enough"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.get("/api/events", headers=headers)
    assert response.json()
    assert all(row["kind"] in {"user_registered", "login"} for row in response.json())


async def test_events_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/events")).status_code == 401


async def test_bus_delivers_to_every_subscriber() -> None:
    bus = EventBus()
    async with bus.subscribe() as first, bus.subscribe() as second:
        await bus.publish(BusEvent(domain="engine", kind="cycle_started"))

        assert (await first.get(timeout=1)).kind == "cycle_started"
        assert (await second.get(timeout=1)).kind == "cycle_started"


async def test_events_published_before_first_read_are_not_lost() -> None:
    bus = EventBus()
    async with bus.subscribe() as subscription:
        await bus.publish(BusEvent(domain="broker", kind="order_filled"))
        assert (await subscription.get(timeout=1)).kind == "order_filled"


async def test_closing_a_subscription_detaches_it() -> None:
    bus = EventBus()
    async with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0


async def test_bus_drops_subscribers_that_stop_draining() -> None:
    bus = EventBus(queue_size=1)
    async with bus.subscribe():
        for _ in range(5):
            await bus.publish(BusEvent(domain="market", kind="tick_received"))
        assert bus.subscriber_count == 0


async def test_a_stalled_subscriber_does_not_block_a_healthy_one() -> None:
    bus = EventBus()
    async with bus.subscribe(queue_size=1) as stalled, bus.subscribe() as healthy:
        for index in range(4):
            await bus.publish(BusEvent(domain="market", kind=f"tick_{index}"))

        assert stalled not in bus._subscribers
        assert healthy.queue.qsize() == 4


async def test_get_returns_none_on_timeout() -> None:
    bus = EventBus()
    async with bus.subscribe() as subscription:
        assert await subscription.get(timeout=0.01) is None


async def test_bus_event_serializes_created_at_as_iso() -> None:
    payload = BusEvent(domain="ai", kind="call_completed").as_dict()
    assert payload["domain"] == "ai"
    assert "T" in payload["created_at"]
