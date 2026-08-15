from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    domain: str
    kind: str
    severity: str
    portfolio_id: int | None
    correlation_id: str | None
    message: str | None
    payload: dict[str, Any]
