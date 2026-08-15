from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from tests.fakes import FakeProvider
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.db.models import CorporateActionRecord, Instrument, PriceBar
from tradebot.marketdata.corporate_actions import (
    CorporateActionService,
    dividend_factor,
    split_factor,
)
from tradebot.providers.base import (
    Capability,
    CorporateAction,
    ProviderConfig,
    SplitKind,
)
from tradebot.providers.router import ProviderRouter

NOW = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)
SPLIT_DAY = date(2026, 8, 12)

FOUR_FOR_ONE = CorporateAction(
    symbol="AAA", effective_date=SPLIT_DAY, kind=SplitKind.SPLIT, split_ratio=Decimal(4)
)
DIVIDEND = CorporateAction(
    symbol="AAA",
    effective_date=SPLIT_DAY,
    kind=SplitKind.DIVIDEND,
    cash_amount=Decimal("2.00"),
)


class ActionProvider(FakeProvider):
    key = "actions"
    label = "Actions"
    capabilities = frozenset({Capability.CORPORATE_ACTIONS})
    default_priority = 1

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)
        self.actions: list[CorporateAction] = []
        self.calls = 0

    async def get_corporate_actions(
        self, symbol: str, *, since: date | None = None
    ) -> list[CorporateAction]:
        self.calls += 1
        return list(self.actions)


def test_a_split_divides_earlier_prices() -> None:
    """A 4:1 split without adjustment reads as a 75% loss on the effective date."""
    assert split_factor([FOUR_FOR_ONE], date(2026, 8, 11)) == Decimal(4)


def test_a_split_does_not_touch_later_prices() -> None:
    assert split_factor([FOUR_FOR_ONE], SPLIT_DAY) == Decimal(1)
    assert split_factor([FOUR_FOR_ONE], date(2026, 8, 13)) == Decimal(1)


def test_consecutive_splits_compound() -> None:
    second = CorporateAction(
        symbol="AAA",
        effective_date=date(2026, 8, 13),
        kind=SplitKind.SPLIT,
        split_ratio=Decimal(2),
    )
    assert split_factor([FOUR_FOR_ONE, second], date(2026, 8, 11)) == Decimal(8)


def test_a_dividend_scales_earlier_prices_down() -> None:
    factor = dividend_factor([DIVIDEND], date(2026, 8, 11), Decimal(100))

    assert factor == Decimal(1) - (Decimal("2.00") / Decimal(100))


def test_a_dividend_does_not_touch_later_prices() -> None:
    assert dividend_factor([DIVIDEND], SPLIT_DAY, Decimal(100)) == Decimal(1)


def test_a_dividend_larger_than_the_price_is_ignored() -> None:
    absurd = CorporateAction(
        symbol="AAA",
        effective_date=SPLIT_DAY,
        kind=SplitKind.DIVIDEND,
        cash_amount=Decimal(500),
    )
    assert dividend_factor([absurd], date(2026, 8, 11), Decimal(100)) == Decimal(1)


def test_factors_are_neutral_without_actions() -> None:
    assert split_factor([], date(2026, 8, 11)) == Decimal(1)
    assert dividend_factor([], date(2026, 8, 11), Decimal(100)) == Decimal(1)


async def _seed(context: AppContext, asset_class: str = "stock") -> int:
    async with context.db.session() as session:
        instrument = Instrument(symbol="AAA", asset_class=asset_class)
        session.add(instrument)
        await session.flush()
        for offset, day in enumerate((10, 11, 13)):
            session.add(
                PriceBar(
                    instrument_id=instrument.id,
                    bar_date=date(2026, 8, day),
                    open=Decimal(400),
                    high=Decimal(420),
                    low=Decimal(380),
                    close=Decimal(400 + offset),
                    volume=Decimal(1_000_000),
                )
            )
        return instrument.id


def make_service(context: AppContext, provider: ActionProvider) -> CorporateActionService:
    clock = FrozenClock(NOW)
    return CorporateActionService(
        ProviderRouter([provider], clock=clock), context.events, clock=clock
    )


async def test_pre_split_bars_are_divided_and_volume_multiplied(context: AppContext) -> None:
    instrument_id = await _seed(context)
    provider = ActionProvider()
    provider.actions = [FOUR_FOR_ONE]
    service = make_service(context, provider)

    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        report = await service.sync(session, [instrument])

    assert report.actions_applied == 1

    async with context.db.session() as session:
        bars = sorted(await session.scalars(select(PriceBar)), key=lambda b: b.bar_date)

    assert bars[0].close == Decimal(100)
    assert bars[0].volume == Decimal(4_000_000)
    assert bars[-1].close == Decimal(402)
    assert bars[-1].volume == Decimal(1_000_000)


async def test_an_action_is_never_applied_twice(context: AppContext) -> None:
    """Double-applying is worse than missing it: prices would be quartered again."""
    instrument_id = await _seed(context)
    provider = ActionProvider()
    provider.actions = [FOUR_FOR_ONE]
    service = make_service(context, provider)

    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        await service.sync(session, [instrument])
    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        second = await service.sync(session, [instrument])

    assert second.actions_applied == 0

    async with context.db.session() as session:
        bars = sorted(await session.scalars(select(PriceBar)), key=lambda b: b.bar_date)
    assert bars[0].close == Decimal(100)


async def test_the_action_is_recorded_with_an_applied_timestamp(context: AppContext) -> None:
    instrument_id = await _seed(context)
    provider = ActionProvider()
    provider.actions = [FOUR_FOR_ONE]
    service = make_service(context, provider)

    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        await service.sync(session, [instrument])

    async with context.db.session() as session:
        record = await session.scalar(select(CorporateActionRecord))

    assert record is not None
    assert record.kind == SplitKind.SPLIT
    assert record.split_ratio == Decimal(4)
    assert record.applied_at is not None


async def test_crypto_is_skipped_entirely(context: AppContext) -> None:
    instrument_id = await _seed(context, asset_class="crypto")
    provider = ActionProvider()
    provider.actions = [FOUR_FOR_ONE]
    service = make_service(context, provider)

    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        report = await service.sync(session, [instrument])

    assert provider.calls == 0
    assert report.actions_found == 0


async def test_a_provider_outage_is_reported_not_raised(context: AppContext) -> None:
    instrument_id = await _seed(context)
    service = make_service(context, ActionProvider(ProviderConfig(enabled=False)))

    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        report = await service.sync(session, [instrument])

    assert report.failed == ["AAA"]


async def test_no_actions_leaves_bars_untouched(context: AppContext) -> None:
    instrument_id = await _seed(context)
    service = make_service(context, ActionProvider())

    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        report = await service.sync(session, [instrument])

    assert report.bars_adjusted == 0

    async with context.db.session() as session:
        bars = sorted(await session.scalars(select(PriceBar)), key=lambda b: b.bar_date)
    assert bars[0].close == Decimal(400)


async def test_the_adjustment_preserves_relative_returns(context: AppContext) -> None:
    """The point of adjusting: the return series must be unchanged by the split."""
    instrument_id = await _seed(context)
    provider = ActionProvider()
    provider.actions = [FOUR_FOR_ONE]
    service = make_service(context, provider)

    async with context.db.session() as session:
        before = sorted(await session.scalars(select(PriceBar)), key=lambda b: b.bar_date)
        original = [bar.close for bar in before[:2]]
        original_return = original[1] / original[0]

    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        await service.sync(session, [instrument])

    async with context.db.session() as session:
        after = sorted(await session.scalars(select(PriceBar)), key=lambda b: b.bar_date)

    assert after[1].close / after[0].close == original_return
