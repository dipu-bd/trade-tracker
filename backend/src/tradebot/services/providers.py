from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.logging import get_logger
from tradebot.db.models import Credential
from tradebot.providers import impl, registry  # noqa: F401  (import registers the adapters)
from tradebot.providers.base import Provider, ProviderConfig
from tradebot.providers.router import ProviderRouter
from tradebot.services.vault import CredentialVault

_log = get_logger(__name__)

ENV_PREFIX = "TRADEBOT_"


class ProviderService:
    """Builds a router for one user from their stored credentials.

    Keyless providers are always present; keyed ones appear only once the user has supplied
    every required field, which is what makes the router's availability check meaningful.
    """

    def __init__(self, vault: CredentialVault, *, clock: Clock | None = None) -> None:
        self._vault = vault
        self._clock = clock or LiveClock()

    async def credentials_for(
        self, session: AsyncSession, user_id: int
    ) -> dict[str, dict[str, str]]:
        stored: dict[str, dict[str, str]] = {}
        for record in await self._vault.list_for_user(session, user_id=user_id):
            secret = await self._vault.reveal(session, credential_id=record.id, user_id=user_id)
            stored.setdefault(record.provider_key, {})[record.field] = secret
        return stored

    async def build_router(self, session: AsyncSession, user_id: int) -> ProviderRouter:
        stored = await self.credentials_for(session, user_id)

        providers: list[Provider] = []
        for key, provider_cls in registry.registered().items():
            config = ProviderConfig(
                credentials=stored.get(key, {}),
                priority=provider_cls.default_priority,
            )
            providers.append(provider_cls(config))
        return ProviderRouter(providers, clock=self._clock)

    async def seed_from_env(
        self, session: AsyncSession, user_id: int, env: dict[str, str]
    ) -> list[str]:
        """Local-development convenience: move keys out of the environment and into the vault.

        Returns provider keys seeded, never the values.
        """
        seeded: list[str] = []
        for key, provider_cls in registry.registered().items():
            if not provider_cls.credential_fields:
                continue

            values: dict[str, str] = {}
            for field in provider_cls.credential_fields:
                name = f"{ENV_PREFIX}{key}_{field.name}".upper()
                value = env.get(name, "").strip()
                if value:
                    values[field.name] = value

            missing = [f.name for f in provider_cls.credential_fields if f.required]
            if not values or any(name not in values for name in missing):
                continue

            for field_name, secret in values.items():
                await self._vault.store(
                    session,
                    user_id=user_id,
                    provider_key=key,
                    field=field_name,
                    secret=secret,
                )
            seeded.append(key)
            _log.info("credential_seeded", provider=key, fields=sorted(values))

        return seeded

    async def masked_summary(self, session: AsyncSession, user_id: int) -> list[dict[str, object]]:
        records: dict[str, list[Credential]] = {}
        for record in await self._vault.list_for_user(session, user_id=user_id):
            records.setdefault(record.provider_key, []).append(record)

        summary: list[dict[str, object]] = []
        for key, provider_cls in registry.registered().items():
            stored = records.get(key, [])
            summary.append(
                {
                    "provider": key,
                    "label": provider_cls.label,
                    "keyless": not provider_cls.credential_fields,
                    "configured": bool(stored) or not provider_cls.credential_fields,
                    "fields": [{"field": row.field, "masked": row.masked} for row in stored],
                }
            )
        return summary
