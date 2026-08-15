from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.db.models import Event
from tradebot.obs.bus import BusEvent, EventBus


class EventRecorder:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def record(
        self,
        session: AsyncSession,
        *,
        domain: str,
        kind: str,
        severity: str = "info",
        user_id: int | None = None,
        portfolio_id: int | None = None,
        correlation_id: str | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        event = Event(
            domain=domain,
            kind=kind,
            severity=severity,
            user_id=user_id,
            portfolio_id=portfolio_id,
            correlation_id=correlation_id,
            message=message,
            payload=payload or {},
        )
        session.add(event)
        await session.flush()

        await self._bus.publish(
            BusEvent(
                domain=domain,
                kind=kind,
                severity=severity,
                user_id=user_id,
                portfolio_id=portfolio_id,
                correlation_id=correlation_id,
                message=message,
                payload=payload or {},
                created_at=event.created_at,
            )
        )
        return event
