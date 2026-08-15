from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CredentialCreate(BaseModel):
    provider_key: str = Field(min_length=1, max_length=64)
    field: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1, max_length=4096)
    label: str = Field(default="default", max_length=120)


class CredentialOut(BaseModel):
    """A stored credential. The secret itself is never returned — only a masked tail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_key: str
    field: str
    label: str
    masked: str
    fingerprint: str
    key_id: str
    created_at: datetime
    updated_at: datetime
