from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar


class AssetClass(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"
    COMMODITY = "commodity"


EQUITY_CLASSES = frozenset({AssetClass.STOCK, AssetClass.ETF})


class Capability(StrEnum):
    UNIVERSE = "universe"
    QUOTES = "quotes"
    BARS = "bars"
    STREAM = "stream"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
    CORPORATE_ACTIONS = "corporate_actions"


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    required: bool = True
    secret: bool = True


@dataclass(frozen=True)
class RateLimits:
    """Zero means unmetered on that axis."""

    requests_per_minute: int = 0
    requests_per_day: int = 0
    tokens_per_minute: int = 0
    max_concurrency: int = 4


@dataclass(frozen=True)
class UniverseEntry:
    symbol: str
    asset_class: AssetClass
    name: str = ""
    exchange: str = ""
    sector: str = ""
    currency: str = "USD"
    dollar_volume: Decimal | None = None


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    at: datetime
    previous_close: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: Decimal | None = None

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True)
class Bar:
    symbol: str
    bar_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class NewsItem:
    symbol: str
    headline: str
    published_at: datetime
    source: str = ""
    url: str = ""
    summary: str = ""


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    effective_date: date
    kind: str
    split_ratio: Decimal | None = None
    cash_amount: Decimal | None = None


class SplitKind:
    SPLIT = "split"
    DIVIDEND = "dividend"


@dataclass
class ProviderConfig:
    credentials: dict[str, str] = field(default_factory=dict)
    priority: int = 100
    enabled: bool = True


class Provider(ABC):
    """One venue or vendor, declaring what it can do rather than being hardcoded per asset class.

    Adding a provider is a new subclass plus a registry entry; no caller changes.
    """

    key: ClassVar[str]
    label: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    asset_classes: ClassVar[frozenset[AssetClass]] = frozenset()
    credential_fields: ClassVar[tuple[CredentialField, ...]] = ()
    rate_limits: ClassVar[RateLimits] = RateLimits()
    default_priority: ClassVar[int] = 100

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig()

    def to_canonical(self, venue_symbol: str) -> str:
        """Venue symbol to the form stored in `instruments`. Identity unless a venue differs."""
        return venue_symbol.upper()

    def to_venue(self, canonical_symbol: str) -> str:
        return canonical_symbol.upper()

    def supports(self, capability: Capability, asset_class: AssetClass | None = None) -> bool:
        if capability not in self.capabilities:
            return False
        return asset_class is None or asset_class in self.asset_classes

    @property
    def available(self) -> bool:
        return self.config.enabled and all(
            self.config.credentials.get(field.name)
            for field in self.credential_fields
            if field.required
        )

    @property
    def missing_credentials(self) -> tuple[str, ...]:
        return tuple(
            field.name
            for field in self.credential_fields
            if field.required and not self.config.credentials.get(field.name)
        )

    async def aclose(self) -> None:
        return None


class SupportsUniverse(Provider):
    @abstractmethod
    async def list_universe(self, asset_class: AssetClass) -> list[UniverseEntry]: ...


class SupportsQuotes(Provider):
    @abstractmethod
    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]: ...


class SupportsBars(Provider):
    @abstractmethod
    async def get_bars(
        self, symbol: str, *, days: int = 260, end: date | None = None
    ) -> list[Bar]: ...


class SupportsStream(Provider):
    @abstractmethod
    def stream_quotes(self, symbols: Sequence[str]) -> AsyncIterator[Quote]: ...


class SupportsNews(Provider):
    @abstractmethod
    async def get_news(self, symbols: Iterable[str], *, limit: int = 20) -> list[NewsItem]: ...


class SupportsCorporateActions(Provider):
    @abstractmethod
    async def get_corporate_actions(
        self, symbol: str, *, since: date | None = None
    ) -> list[CorporateAction]: ...


class ProviderError(Exception):
    def __init__(self, provider_key: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"{provider_key}: {message}")
        self.provider_key = provider_key
        self.status = status


class ProviderRateLimitedError(ProviderError):
    def __init__(self, provider_key: str, *, retry_after: float | None = None) -> None:
        super().__init__(provider_key, "rate limit exceeded", status=429)
        self.retry_after = retry_after


class ProviderUnavailableError(ProviderError):
    pass
