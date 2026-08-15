import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.logging import get_logger
from tradebot.obs.bus import BusEvent, EventBus
from tradebot.providers.base import (
    AssetClass,
    Capability,
    Provider,
    ProviderError,
    ProviderUnavailableError,
    Quote,
)
from tradebot.providers.router import ProviderRouter

_log = get_logger(__name__)

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0


@dataclass
class StreamStats:
    connects: int = 0
    ticks: int = 0
    reconnects: int = 0
    replayed: int = 0
    last_error: str | None = None
    symbols: list[str] = field(default_factory=list)


class QuoteStreamWorker:
    """Keeps one venue's quote stream flowing into the bus.

    A dropped socket loses ticks, so every reconnect replays the current state over REST before
    resuming — otherwise the matching engine would price against whatever it last happened to see.
    """

    def __init__(
        self,
        router: ProviderRouter,
        bus: EventBus,
        *,
        asset_class: AssetClass,
        symbols: Sequence[str],
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._router = router
        self._bus = bus
        self._asset_class = asset_class
        self._symbols = [s.upper() for s in symbols]
        self._clock = clock or LiveClock()
        self._sleep = sleep or asyncio.sleep
        self._stop = asyncio.Event()
        self.stats = StreamStats(symbols=list(self._symbols))

    @property
    def provider(self) -> Provider | None:
        candidates = self._router.candidates(Capability.STREAM, self._asset_class)
        return candidates[0] if candidates else None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> StreamStats:
        backoff = INITIAL_BACKOFF

        while not self._stop.is_set():
            provider = self.provider
            if provider is None:
                self.stats.last_error = "no streaming provider"
                await self._publish_status("stream_unavailable", severity="warning")
                return self.stats

            try:
                await self._replay_gap()
                self.stats.connects += 1
                await self._publish_status("stream_connected")
                backoff = INITIAL_BACKOFF
                await self._consume(provider)
            except asyncio.CancelledError:
                raise
            except (ProviderError, OSError) as exc:
                self.stats.last_error = str(exc)[:300]
            except Exception as exc:
                self.stats.last_error = f"{type(exc).__name__}: {exc}"[:300]

            if self._stop.is_set():
                break

            self.stats.reconnects += 1
            await self._publish_status("stream_reconnecting", severity="warning")
            await self._sleep(backoff + random.uniform(0, backoff / 4))  # noqa: S311
            backoff = min(backoff * 2, MAX_BACKOFF)

        await self._publish_status("stream_stopped")
        return self.stats

    async def _consume(self, provider: Provider) -> None:
        """Races the socket against the stop signal.

        Checking the flag only after a tick arrives would leave shutdown hanging on a quiet
        market until the next trade printed.
        """
        reader = asyncio.create_task(self._read(provider))
        stopper = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait({reader, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                reader.result()
        finally:
            for task in (reader, stopper):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader, stopper, return_exceptions=True)

    async def _read(self, provider: Provider) -> None:
        async for quote in provider.stream_quotes(self._symbols):  # type: ignore[attr-defined]
            self.stats.ticks += 1
            await self._publish_tick(quote)

    async def _replay_gap(self) -> None:
        """Pull a REST snapshot so the first post-reconnect price is current, not stale."""

        async def call(provider: Provider) -> dict[str, Quote]:
            found: dict[str, Quote] = await provider.get_quotes(self._symbols)  # type: ignore[attr-defined]
            return found

        try:
            quotes = await self._router.execute(
                Capability.QUOTES, call, asset_class=self._asset_class
            )
        except ProviderUnavailableError as exc:
            _log.warning("gap_replay_failed", error=str(exc))
            return

        for quote in quotes.values():
            self.stats.replayed += 1
            await self._publish_tick(quote, replayed=True)

    async def _publish_tick(self, quote: Quote, *, replayed: bool = False) -> None:
        await self._bus.publish(
            BusEvent(
                domain="market",
                kind="tick_received",
                severity="debug",
                message=quote.symbol,
                payload={
                    "symbol": quote.symbol,
                    "price": str(quote.price),
                    "at": quote.at.isoformat(),
                    "replayed": replayed,
                },
                created_at=self._clock.now(),
            )
        )

    async def _publish_status(self, kind: str, *, severity: str = "info") -> None:
        await self._bus.publish(
            BusEvent(
                domain="market",
                kind=kind,
                severity=severity,
                message=self._asset_class.value,
                payload={
                    "symbols": len(self._symbols),
                    "ticks": self.stats.ticks,
                    "reconnects": self.stats.reconnects,
                    "last_error": self.stats.last_error,
                },
                created_at=self._clock.now(),
            )
        )
