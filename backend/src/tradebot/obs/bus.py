import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any

from tradebot.db.base import utcnow

DEFAULT_QUEUE_SIZE = 256


@dataclass(frozen=True)
class BusEvent:
    domain: str
    kind: str
    severity: str = "info"
    user_id: int | None = None
    portfolio_id: int | None = None
    correlation_id: str | None = None
    message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data


class Subscription:
    """Registers its queue on creation, not on first iteration.

    A generator-based subscriber would miss anything published before the consumer's first
    `__anext__`, which for a live feed is exactly the window that matters.
    """

    def __init__(self, bus: "EventBus", queue_size: int) -> None:
        self._bus = bus
        self.queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=queue_size)
        bus._attach(self)

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._bus._detach(self)

    async def __anext__(self) -> BusEvent:
        return await self.queue.get()

    def __aiter__(self) -> AsyncIterator[BusEvent]:
        return self

    async def get(self, *, timeout: float | None = None) -> BusEvent | None:  # noqa: ASYNC109
        if timeout is None:
            return await self.queue.get()
        try:
            async with asyncio.timeout(timeout):
                return await self.queue.get()
        except TimeoutError:
            return None


class EventBus:
    """Fan-out to live subscribers.

    A subscriber that stops draining is dropped rather than allowed to block the publisher, so a
    stalled browser tab cannot back up the matching engine.
    """

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: set[Subscription] = set()

    def subscribe(self, queue_size: int | None = None) -> Subscription:
        return Subscription(self, queue_size or self._queue_size)

    def _attach(self, subscription: Subscription) -> None:
        self._subscribers.add(subscription)

    def _detach(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    async def publish(self, event: BusEvent) -> None:
        for subscription in list(self._subscribers):
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                self._detach(subscription)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
