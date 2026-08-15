import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.db.models import Event
from tradebot.obs import metrics
from tradebot.schemas.event import EventOut

router = APIRouter(tags=["observability"])

HEARTBEAT_SECONDS = 20


@router.get("/events", response_model=list[EventOut])
async def list_events(
    user: CurrentUser,
    session: DbSession,
    domain: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
    correlation_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[EventOut]:
    """Recorded events for the current account, newest first."""
    stmt = select(Event).where(Event.user_id == user.id)
    if domain:
        stmt = stmt.where(Event.domain == domain)
    if kind:
        stmt = stmt.where(Event.kind == kind)
    if severity:
        stmt = stmt.where(Event.severity == severity)
    if correlation_id:
        stmt = stmt.where(Event.correlation_id == correlation_id)

    result = await session.scalars(stmt.order_by(Event.id.desc()).limit(limit))
    return [EventOut.model_validate(row) for row in result]


@router.get("/events/stream")
async def stream_events(user: CurrentUser, context: Context) -> StreamingResponse:
    """Server-sent stream of live events for the current account."""

    async def publisher() -> AsyncIterator[bytes]:
        metrics.sse_subscribers.inc()
        try:
            async with context.bus.subscribe() as subscription:
                while True:
                    event = await subscription.get(timeout=HEARTBEAT_SECONDS)
                    if event is None:
                        yield b": keepalive\n\n"
                        continue
                    if event.user_id is not None and event.user_id != user.id:
                        continue
                    yield f"event: {event.domain}\ndata: {json.dumps(event.as_dict())}\n\n".encode()
        finally:
            metrics.sse_subscribers.dec()

    return StreamingResponse(
        publisher(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")
