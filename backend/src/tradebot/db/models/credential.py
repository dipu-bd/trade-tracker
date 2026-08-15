from sqlalchemy import ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db.base import Base, TimestampMixin


class Credential(Base, TimestampMixin):
    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_key", "field", "label", name="uq_credential_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_key: Mapped[str] = mapped_column(String(64), index=True)
    field: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(120), default="default")

    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(32))

    masked: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(24))
