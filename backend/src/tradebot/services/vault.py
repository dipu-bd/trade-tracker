from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.core.crypto import SealedSecret, SecretBox, fingerprint, mask
from tradebot.core.errors import NotFoundError
from tradebot.db.models import Credential


class CredentialVault:
    def __init__(self, box: SecretBox) -> None:
        self._box = box

    def _aad(self, user_id: int, provider_key: str, field: str) -> bytes:
        """Binds ciphertext to its owner, so a row copied to another user fails to decrypt."""
        return f"{user_id}:{provider_key}:{field}".encode()

    async def store(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        provider_key: str,
        field: str,
        secret: str,
        label: str = "default",
    ) -> Credential:
        sealed = self._box.seal(secret, aad=self._aad(user_id, provider_key, field))

        existing = await session.scalar(
            select(Credential).where(
                Credential.user_id == user_id,
                Credential.provider_key == provider_key,
                Credential.field == field,
                Credential.label == label,
            )
        )
        record = existing or Credential(
            user_id=user_id, provider_key=provider_key, field=field, label=label
        )
        record.wrapped_key = sealed.wrapped_key
        record.nonce = sealed.nonce
        record.ciphertext = sealed.ciphertext
        record.key_id = sealed.key_id
        record.masked = mask(secret)
        record.fingerprint = fingerprint(secret)

        if existing is None:
            session.add(record)
        await session.flush()
        return record

    async def reveal(self, session: AsyncSession, *, credential_id: int, user_id: int) -> str:
        record = await session.scalar(
            select(Credential).where(Credential.id == credential_id, Credential.user_id == user_id)
        )
        if record is None:
            raise NotFoundError("credential not found")
        return self._box.open(
            SealedSecret(
                wrapped_key=record.wrapped_key,
                nonce=record.nonce,
                ciphertext=record.ciphertext,
                key_id=record.key_id,
            ),
            aad=self._aad(record.user_id, record.provider_key, record.field),
        )

    async def list_for_user(
        self, session: AsyncSession, *, user_id: int, provider_key: str | None = None
    ) -> list[Credential]:
        stmt = select(Credential).where(Credential.user_id == user_id)
        if provider_key is not None:
            stmt = stmt.where(Credential.provider_key == provider_key)
        result = await session.scalars(stmt.order_by(Credential.provider_key, Credential.field))
        return list(result)

    async def delete(self, session: AsyncSession, *, credential_id: int, user_id: int) -> None:
        record = await session.scalar(
            select(Credential).where(Credential.id == credential_id, Credential.user_id == user_id)
        )
        if record is None:
            raise NotFoundError("credential not found")
        await session.delete(record)
