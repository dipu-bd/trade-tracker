import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from tests.fakes import FakeProvider
from tradebot.core.clock import FrozenClock
from tradebot.marketdata.stream import QuoteStreamWorker
from tradebot.obs.bus import EventBus
from tradebot.providers.base import (
    AssetClass,
    Capability,
    ProviderConfig,
    ProviderError,
    Quote,
)
from tradebot.providers.router import ProviderRouter

NOW = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)


class StreamingProvider(FakeProvider):
    key = "streamer"
    label = "Streamer"
    capabilities = frozenset({Capability.QUOTES, Capability.STREAM})
    default_priority = 1

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)
        self.ticks: list[Quote] = []
        self.fail_after: int | None = None
        self.stream_calls = 0
        self.hold = True

    async def stream_quotes(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        self.stream_calls += 1
        for index, tick in enumerate(self.ticks):
            if self.fail_after is not None and index >= self.fail_after:
                raise ProviderError(self.key, "socket closed")
            yield tick
            await asyncio.sleep(0)
        if self.hold:
            # A real socket stays open between ticks rather than ending; without this the
            # worker spins through reconnects and floods the bus.
            await asyncio.Event().wait()


def tick(symbol: str, price: str) -> Quote:
    return Quote(symbol=symbol, price=Decimal(price), at=NOW)


def make_worker(provider: StreamingProvider, bus: EventBus, **kwargs: object) -> QuoteStreamWorker:
    async def instant(_seconds: float) -> None:
        await asyncio.sleep(0)

    clock = FrozenClock(NOW)
    return QuoteStreamWorker(
        ProviderRouter([provider], clock=clock),
        bus,
        asset_class=AssetClass.STOCK,
        symbols=["AAA"],
        clock=clock,
        sleep=instant,
        **kwargs,  # type: ignore[arg-type]
    )


async def collect(bus: EventBus, kinds: set[str], limit: int) -> list[str]:
    seen: list[str] = []
    async with bus.subscribe(queue_size=256) as subscription:
        while len(seen) < limit:
            event = await subscription.get(timeout=1)
            if event is None:
                break
            if event.kind in kinds:
                seen.append(event.kind)
    return seen


async def test_ticks_reach_the_bus() -> None:
    provider = StreamingProvider()
    provider.ticks = [tick("AAA", "100"), tick("AAA", "101")]
    bus = EventBus()
    worker = make_worker(provider, bus)

    async with bus.subscribe(queue_size=256) as subscription:
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        worker.stop()
        await asyncio.wait_for(task, timeout=2)

        prices = []
        while (event := await subscription.get(timeout=0.05)) is not None:
            if event.kind == "tick_received":
                prices.append(event.payload["price"])

    assert "100" in prices
    assert worker.stats.ticks >= 2


async def test_a_reconnect_replays_a_rest_snapshot_first() -> None:
    """Without this the first price after a drop is whatever the engine last saw."""
    provider = StreamingProvider()
    provider.prices["AAA"] = Decimal("250")
    provider.ticks = [tick("AAA", "100")]
    bus = EventBus()
    worker = make_worker(provider, bus)

    async with bus.subscribe(queue_size=256) as subscription:
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        worker.stop()
        await asyncio.wait_for(task, timeout=2)

        replayed = []
        while (event := await subscription.get(timeout=0.05)) is not None:
            if event.kind == "tick_received" and event.payload["replayed"]:
                replayed.append(event.payload["price"])

    assert "250" in replayed
    assert worker.stats.replayed >= 1


async def test_a_dropped_socket_reconnects_rather_than_dying() -> None:
    provider = StreamingProvider()
    provider.ticks = [tick("AAA", "100"), tick("AAA", "101")]
    provider.fail_after = 1
    provider.hold = False
    bus = EventBus()
    worker = make_worker(provider, bus)

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)

    assert worker.stats.reconnects >= 1
    assert provider.stream_calls >= 2
    assert "socket closed" in (worker.stats.last_error or "")


async def test_reconnecting_is_announced_on_the_bus() -> None:
    provider = StreamingProvider()
    provider.ticks = [tick("AAA", "100")]
    provider.fail_after = 0
    provider.hold = False
    bus = EventBus()
    worker = make_worker(provider, bus)

    async with bus.subscribe(queue_size=256) as subscription:
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        worker.stop()
        await asyncio.wait_for(task, timeout=2)

        kinds = set()
        while (event := await subscription.get(timeout=0.05)) is not None:
            kinds.add(event.kind)

    assert "stream_reconnecting" in kinds
    assert "stream_connected" in kinds


async def test_no_streaming_provider_exits_cleanly() -> None:
    """A provider chain without STREAM must not spin in a reconnect loop."""
    bus = EventBus()
    worker = make_worker(FakeProvider(), bus)  # type: ignore[arg-type]

    stats = await asyncio.wait_for(worker.run(), timeout=2)

    assert stats.last_error == "no streaming provider"
    assert stats.connects == 0


async def test_stopping_ends_the_loop_and_announces_it() -> None:
    provider = StreamingProvider()
    provider.ticks = [tick("AAA", "100")]
    bus = EventBus()
    worker = make_worker(provider, bus)

    async with bus.subscribe(queue_size=256) as subscription:
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        worker.stop()
        await asyncio.wait_for(task, timeout=2)

        kinds = set()
        while (event := await subscription.get(timeout=0.05)) is not None:
            kinds.add(event.kind)

    assert "stream_stopped" in kinds
    assert task.done()


async def test_a_failed_gap_replay_does_not_stop_the_stream() -> None:
    provider = StreamingProvider()
    provider.ticks = [tick("AAA", "100")]
    provider.capabilities = frozenset({Capability.STREAM})
    bus = EventBus()
    worker = make_worker(provider, bus)

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)

    assert worker.stats.replayed == 0
    assert worker.stats.ticks >= 1
