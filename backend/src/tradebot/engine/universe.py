from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.db.models import Instrument

DEFAULT_MAX_SYMBOLS = 120


@dataclass(frozen=True, slots=True)
class UniverseSpec:
    asset_classes: tuple[str, ...] = ("stock", "etf")
    always: tuple[str, ...] = ()
    never: tuple[str, ...] = ()
    max_symbols: int = DEFAULT_MAX_SYMBOLS

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "UniverseSpec":
        raw = raw or {}
        return cls(
            asset_classes=tuple(raw.get("asset_classes") or ("stock", "etf")),
            always=tuple(symbol.upper() for symbol in raw.get("always") or ()),
            never=tuple(symbol.upper() for symbol in raw.get("never") or ()),
            max_symbols=int(raw.get("max_symbols") or DEFAULT_MAX_SYMBOLS),
        )


@dataclass(frozen=True, slots=True)
class ResolvedUniverse:
    instruments: list[Instrument]
    truncated: int

    @property
    def symbols(self) -> list[str]:
        return [item.symbol for item in self.instruments]

    @property
    def names(self) -> dict[str, str]:
        return {item.symbol: item.name for item in self.instruments}


async def resolve(
    session: AsyncSession, spec: UniverseSpec, held: frozenset[str] = frozenset()
) -> ResolvedUniverse:
    """Instruments that are tracked, active, and have bar coverage.

    This is the whole screener, and it is narrower than the plan's stored-spec design because
    the entitled FMP plan has no company-screener endpoint. Ranking on fundamentals happens
    against locally stored bars instead; see the M4 note in the plan file.
    """
    stmt = select(Instrument).where(
        Instrument.is_active.is_(True),
        Instrument.asset_class.in_(spec.asset_classes),
        Instrument.first_bar_date.is_not(None),
    )
    rows = list(await session.scalars(stmt.order_by(Instrument.symbol)))

    pinned = set(spec.always) | set(held)
    if pinned:
        extra = await session.scalars(select(Instrument).where(Instrument.symbol.in_(pinned)))
        rows.extend(item for item in extra if item not in rows)

    never = set(spec.never)
    kept = [item for item in rows if item.symbol not in never or item.symbol in held]
    kept.sort(key=lambda item: (item.symbol not in pinned, item.symbol))

    truncated = max(0, len(kept) - spec.max_symbols)
    return ResolvedUniverse(instruments=kept[: spec.max_symbols], truncated=truncated)
