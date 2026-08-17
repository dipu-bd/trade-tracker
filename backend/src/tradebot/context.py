from dataclasses import dataclass

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.crypto import SecretBox
from tradebot.core.settings import Settings
from tradebot.db.session import Database
from tradebot.marketdata.jobs import MarketSyncJob
from tradebot.obs import EventBus, EventRecorder
from tradebot.services import AuthService, CredentialVault
from tradebot.services.providers import ProviderService


@dataclass
class AppContext:
    settings: Settings
    db: Database
    clock: Clock
    bus: EventBus
    events: EventRecorder
    auth: AuthService
    vault: CredentialVault
    providers: ProviderService
    sync_job: MarketSyncJob

    @classmethod
    def build(cls, settings: Settings, *, clock: Clock | None = None) -> "AppContext":
        bus = EventBus()
        vault = CredentialVault(SecretBox(settings.secret_key))
        clock = clock or LiveClock()
        return cls(
            settings=settings,
            db=Database(settings.database_url, echo=settings.database_echo),
            clock=clock,
            bus=bus,
            events=EventRecorder(bus),
            auth=AuthService(settings),
            vault=vault,
            providers=ProviderService(vault, clock=clock),
            sync_job=MarketSyncJob(clock=clock),
        )

    async def aclose(self) -> None:
        await self.db.dispose()
