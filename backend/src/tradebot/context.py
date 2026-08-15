from dataclasses import dataclass

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.crypto import SecretBox
from tradebot.core.settings import Settings
from tradebot.db.session import Database
from tradebot.obs import EventBus, EventRecorder
from tradebot.services import AuthService, CredentialVault


@dataclass
class AppContext:
    settings: Settings
    db: Database
    clock: Clock
    bus: EventBus
    events: EventRecorder
    auth: AuthService
    vault: CredentialVault

    @classmethod
    def build(cls, settings: Settings, *, clock: Clock | None = None) -> "AppContext":
        bus = EventBus()
        return cls(
            settings=settings,
            db=Database(settings.database_url, echo=settings.database_echo),
            clock=clock or LiveClock(),
            bus=bus,
            events=EventRecorder(bus),
            auth=AuthService(settings),
            vault=CredentialVault(SecretBox(settings.secret_key)),
        )

    async def aclose(self) -> None:
        await self.db.dispose()
