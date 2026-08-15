from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from tradebot.api.deps import Context, DbSession

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    env: str
    database: str


@router.get("/health", response_model=Health)
async def health(context: Context, session: DbSession) -> Health:
    """Liveness and database connectivity."""
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"
    return Health(
        status="ok" if database == "ok" else "degraded",
        env=context.settings.env,
        database=database,
    )
