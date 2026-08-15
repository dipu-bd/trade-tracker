from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.core.clock import Clock, LiveClock
from tradebot.core.logging import get_logger
from tradebot.db.models import CorporateActionRecord, Instrument, PriceBar
from tradebot.obs import EventRecorder
from tradebot.providers.base import (
    AssetClass,
    Capability,
    CorporateAction,
    Provider,
    ProviderUnavailableError,
    SplitKind,
)
from tradebot.providers.router import ProviderRouter

_log = get_logger(__name__)

PRICE_FIELDS = ("open", "high", "low", "close")


@dataclass
class AdjustmentReport:
    actions_found: int = 0
    actions_applied: int = 0
    bars_adjusted: int = 0
    failed: list[str] = field(default_factory=list)


def split_factor(actions: Sequence[CorporateAction], before: date) -> Decimal:
    """Cumulative divisor for bars dated before these splits.

    A 4:1 split quadruples the share count, so every earlier price must be divided by four or the
    chart shows a 75% loss on the effective date.
    """
    factor = Decimal(1)
    for action in actions:
        if action.kind != SplitKind.SPLIT or not action.split_ratio:
            continue
        if action.effective_date > before:
            factor *= action.split_ratio
    return factor


def dividend_factor(actions: Sequence[CorporateAction], before: date, close_on: Decimal) -> Decimal:
    """Total-return multiplier for bars dated before these dividends.

    Ignoring dividends understates the return of anything that pays them, permanently.
    """
    factor = Decimal(1)
    if close_on <= 0:
        return factor
    for action in actions:
        if action.kind != SplitKind.DIVIDEND or not action.cash_amount:
            continue
        if action.effective_date > before:
            ratio = Decimal(1) - (action.cash_amount / close_on)
            if ratio > 0:
                factor *= ratio
    return factor


class CorporateActionService:
    """Fetches splits and dividends, then back-adjusts stored bars exactly once each.

    Applying an action twice is worse than missing it, so every record carries `applied_at` and
    is skipped once set.
    """

    def __init__(
        self,
        router: ProviderRouter,
        events: EventRecorder,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._router = router
        self._events = events
        self._clock = clock or LiveClock()

    async def sync(
        self,
        session: AsyncSession,
        instruments: Sequence[Instrument],
        *,
        since: date | None = None,
    ) -> AdjustmentReport:
        report = AdjustmentReport()

        for instrument in instruments:
            asset_class = AssetClass(instrument.asset_class)
            if asset_class is AssetClass.CRYPTO:
                continue

            async def call(
                provider: Provider, symbol: str = instrument.symbol
            ) -> list[CorporateAction]:
                found: list[CorporateAction] = await provider.get_corporate_actions(  # type: ignore[attr-defined]
                    symbol, since=since
                )
                return found

            try:
                actions = await self._router.execute(
                    Capability.CORPORATE_ACTIONS, call, asset_class=asset_class
                )
            except ProviderUnavailableError as exc:
                report.failed.append(instrument.symbol)
                _log.warning("corporate_actions_failed", symbol=instrument.symbol, error=str(exc))
                continue

            report.actions_found += len(actions)
            report.bars_adjusted += await self._apply(session, instrument, actions, report)

        return report

    async def _apply(
        self,
        session: AsyncSession,
        instrument: Instrument,
        actions: Sequence[CorporateAction],
        report: AdjustmentReport,
    ) -> int:
        pending = await self._record(session, instrument, actions)
        if not pending:
            return 0

        bars = list(
            await session.scalars(
                select(PriceBar)
                .where(PriceBar.instrument_id == instrument.id)
                .order_by(PriceBar.bar_date)
            )
        )
        if not bars:
            for record in pending:
                record.applied_at = self._clock.now()
            return 0

        closes = {bar.bar_date: bar.close for bar in bars}
        adjusted = 0

        for bar in bars:
            divisor = split_factor(actions, bar.bar_date)
            reference = _nearest_close(closes, actions, bar.bar_date)
            multiplier = dividend_factor(actions, bar.bar_date, reference)

            if divisor == 1 and multiplier == 1:
                continue

            for name in PRICE_FIELDS:
                setattr(bar, name, (getattr(bar, name) / divisor) * multiplier)
            if divisor != 1:
                bar.volume = bar.volume * divisor
            adjusted += 1

        applied_at = self._clock.now()
        for record in pending:
            record.applied_at = applied_at
        report.actions_applied += len(pending)

        await session.flush()
        await self._events.record(
            session,
            domain="market",
            kind="corporate_action_applied",
            message=instrument.symbol,
            payload={"actions": len(pending), "bars_adjusted": adjusted},
        )
        return adjusted

    async def _record(
        self,
        session: AsyncSession,
        instrument: Instrument,
        actions: Sequence[CorporateAction],
    ) -> list[CorporateActionRecord]:
        existing = {
            (row.effective_date, row.kind): row
            for row in await session.scalars(
                select(CorporateActionRecord).where(
                    CorporateActionRecord.instrument_id == instrument.id
                )
            )
        }

        pending: list[CorporateActionRecord] = []
        for action in actions:
            key = (action.effective_date, action.kind)
            record = existing.get(key)
            if record is None:
                record = CorporateActionRecord(
                    instrument_id=instrument.id,
                    effective_date=action.effective_date,
                    kind=action.kind,
                    split_ratio=action.split_ratio,
                    cash_amount=action.cash_amount,
                )
                session.add(record)
                pending.append(record)
            elif record.applied_at is None:
                pending.append(record)

        await session.flush()
        return pending


def _nearest_close(
    closes: dict[date, Decimal], actions: Sequence[CorporateAction], bar_date: date
) -> Decimal:
    """Dividend adjustment is proportional to the price it was paid against."""
    candidates = [
        action.effective_date
        for action in actions
        if action.kind == SplitKind.DIVIDEND and action.effective_date > bar_date
    ]
    if not candidates:
        return closes.get(bar_date, Decimal(0))

    earliest = min(candidates)
    prior = [day for day in closes if day < earliest]
    return closes[max(prior)] if prior else closes.get(bar_date, Decimal(0))
