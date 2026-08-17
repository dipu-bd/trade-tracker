from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.fakes import FakeProvider, FlakyProvider
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.db.models import Instrument, Portfolio, PriceBar, User
from tradebot.marketdata.service import MarketDataService
from tradebot.providers.base import AssetClass, Bar, ProviderConfig, UniverseEntry
from tradebot.providers.router import ProviderRouter

NOW = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)
TODAY = NOW.date()


def make_service(context: AppContext, providers: list) -> MarketDataService:  # type: ignore[type-arg]
    clock = FrozenClock(NOW)
    return MarketDataService(ProviderRouter(providers, clock=clock), context.events, clock=clock)


def bars_for(symbol: str, count: int, end: date = TODAY) -> list[Bar]:
    """Consecutive calendar days, so a crypto instrument has no gaps by construction."""
    return [
        Bar(
            symbol=symbol,
            bar_date=end - timedelta(days=count - i - 1),
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal(1_000_000),
        )
        for i in range(count)
    ]


async def test_universe_sync_creates_instruments(context: AppContext) -> None:
    service = make_service(context, [FakeProvider()])

    async with context.db.session() as session:
        created = await service.sync_universe(session, AssetClass.STOCK)

    assert [i.symbol for i in created] == ["AAA"]
    assert created[0].asset_class == "stock"


async def test_universe_sync_is_idempotent(context: AppContext) -> None:
    service = make_service(context, [FakeProvider()])

    async with context.db.session() as session:
        await service.sync_universe(session, AssetClass.STOCK)
    async with context.db.session() as session:
        await service.sync_universe(session, AssetClass.STOCK)
        rows = list(await session.scalars(select(Instrument)))

    assert len(rows) == 1


async def test_upsert_updates_metadata_without_duplicating(context: AppContext) -> None:
    service = make_service(context, [FakeProvider()])
    entry = UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK, name="Alpha", sector="Tech")

    async with context.db.session() as session:
        await service.upsert_instruments(session, [entry])
    async with context.db.session() as session:
        updated = await service.upsert_instruments(
            session,
            [UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK, name="Alpha Corp")],
        )

    assert len(updated) == 1
    assert updated[0].name == "Alpha Corp"
    assert updated[0].sector == "Tech"


async def test_bars_are_written_and_the_date_range_recorded(context: AppContext) -> None:
    provider = FakeProvider()
    service = make_service(context, [provider])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK)]
        )
        report = await service.refresh_bars(session, instruments, days=10)

    assert report.bars_written == 10
    async with context.db.session() as session:
        instrument = await session.scalar(select(Instrument))
        rows = list(await session.scalars(select(PriceBar)))

    assert instrument is not None
    assert len(rows) == 10
    assert instrument.last_bar_date is not None


async def test_a_fresh_instrument_is_skipped(context: AppContext) -> None:
    provider = FakeProvider()
    service = make_service(context, [provider])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.CRYPTO)]
        )
        instruments[0].last_bar_date = TODAY
        await session.flush()
        report = await service.refresh_bars(session, instruments, days=10)

    assert report.skipped_fresh == 1
    assert report.bars_written == 0


async def test_force_refreshes_even_when_fresh(context: AppContext) -> None:
    service = make_service(context, [FakeProvider()])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK)]
        )
        instruments[0].last_bar_date = TODAY
        await session.flush()
        report = await service.refresh_bars(session, instruments, days=10, force=True)

    assert report.skipped_fresh == 0
    assert report.bars_written == 10


async def test_rewriting_the_same_bars_is_a_no_op(context: AppContext) -> None:
    service = make_service(context, [FakeProvider()])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK)]
        )
        await service.refresh_bars(session, instruments, days=10)
        second = await service.refresh_bars(session, instruments, days=10, force=True)

    assert second.bars_written == 0


async def test_a_changed_final_bar_is_updated_in_place(context: AppContext) -> None:
    provider = FakeProvider()
    service = make_service(context, [provider])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.CRYPTO)]
        )
        await service._store_bars(session, instruments[0], bars_for("AAA", 3))
        revised = bars_for("AAA", 3)
        revised[-1] = Bar(
            symbol="AAA",
            bar_date=revised[-1].bar_date,
            open=revised[-1].open,
            high=revised[-1].high,
            low=revised[-1].low,
            close=Decimal("999"),
            volume=revised[-1].volume,
        )
        written = await service._store_bars(session, instruments[0], revised)

    assert written == 1
    async with context.db.session() as session:
        rows = sorted(await session.scalars(select(PriceBar)), key=lambda r: r.bar_date)
    assert rows[-1].close == Decimal("999")
    assert len(rows) == 3


async def test_a_provider_outage_is_reported_not_raised(context: AppContext) -> None:
    service = make_service(context, [FlakyProvider(ProviderConfig(priority=1))])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK)]
        )
        report = await service.refresh_bars(session, instruments, days=10)

    assert report.failed == ["AAA"]
    assert report.bars_written == 0


async def test_a_missing_session_is_detected_as_a_gap(context: AppContext) -> None:
    service = make_service(context, [FakeProvider()])
    complete = bars_for("AAA", 10)
    with_hole = complete[:4] + complete[5:]

    assert service._detect_gaps(complete, AssetClass.CRYPTO) == 0
    assert service._detect_gaps(with_hole, AssetClass.CRYPTO) == 1


async def test_quotes_are_recorded_against_the_instrument(context: AppContext) -> None:
    provider = FakeProvider()
    provider.prices["AAA"] = Decimal("123.45")
    service = make_service(context, [provider])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK)]
        )
        quotes = await service.get_quotes(session, instruments)

    assert quotes["AAA"].price == Decimal("123.45")
    async with context.db.session() as session:
        instrument = await session.scalar(select(Instrument))
    assert instrument is not None
    assert instrument.last_quote_price == Decimal("123.45")
    assert instrument.last_quote_source == "fake"


async def test_quotes_group_by_asset_class(context: AppContext) -> None:
    equity = FakeProvider(ProviderConfig(priority=1))
    service = make_service(context, [equity])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session,
            [
                UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK),
                UniverseEntry(symbol="BBB", asset_class=AssetClass.CRYPTO),
            ],
        )
        quotes = await service.get_quotes(session, instruments)

    assert "AAA" in quotes
    assert "BBB" not in quotes


async def test_load_bars_returns_ascending(context: AppContext) -> None:
    service = make_service(context, [FakeProvider()])

    async with context.db.session() as session:
        instruments = await service.upsert_instruments(
            session, [UniverseEntry(symbol="AAA", asset_class=AssetClass.STOCK)]
        )
        await service.refresh_bars(session, instruments, days=10)
        loaded = await service.load_bars(session, instruments[0])

    assert len(loaded) == 10
    assert all(loaded[i].bar_date < loaded[i + 1].bar_date for i in range(len(loaded) - 1))


@pytest.mark.parametrize(
    ("last_bar", "stale"),
    [(None, True), (TODAY, False), (TODAY - timedelta(days=30), True)],
)
async def test_staleness_rules(context: AppContext, last_bar: date | None, stale: bool) -> None:
    service = make_service(context, [FakeProvider()])
    instrument = Instrument(symbol="AAA", asset_class="crypto", last_bar_date=last_bar)

    assert service._is_stale(instrument) is stale


async def test_the_unified_sync_pulls_universe_bars_and_price_in_one_pass(
    context: AppContext,
) -> None:
    provider = FakeProvider()
    provider.prices["AAA"] = Decimal("50.25")
    service = make_service(context, [provider])

    async with context.db.session() as session:
        instruments, report = await service.sync(session, AssetClass.STOCK)

    assert [i.symbol for i in instruments] == ["AAA"]
    assert report.bars_written > 0
    assert report.quotes_updated == 1
    assert instruments[0].last_quote_price == Decimal("50.25")


async def test_the_unified_sync_tracks_named_symbols_outside_the_listing(
    context: AppContext,
) -> None:
    provider = FakeProvider()
    provider.prices["ZZZ"] = Decimal("7")
    service = make_service(context, [provider])

    async with context.db.session() as session:
        instruments, report = await service.sync(session, AssetClass.STOCK, symbols=["zzz"])

    assert [i.symbol for i in instruments] == ["ZZZ"]
    assert report.quotes_updated == 1


async def test_a_bar_less_instrument_is_still_refreshed(context: AppContext) -> None:
    """It used to be filtered out of every refresh, so a failed first fetch never recovered."""
    from tradebot.marketdata.refresh import MarketSync

    async with context.db.session() as session:
        user = User(email="owner@example.com", password_hash="x", display_name="Owner")
        session.add(user)
        await session.flush()
        session.add(Instrument(symbol="AAA", asset_class="stock", is_active=True))
        session.add(
            Portfolio(user_id=user.id, name="p", initial_capital=Decimal(1000), base_currency="USD")
        )
        await session.flush()

    report = await MarketSync(context).refresh_all()

    assert report.instruments == 1


@pytest.mark.parametrize(
    ("now", "last_quote_at", "quotable"),
    [
        (datetime(2026, 8, 17, 15, 0, tzinfo=UTC), None, True),
        (datetime(2026, 8, 17, 15, 0, tzinfo=UTC), datetime(2026, 8, 14, 20, 30, tzinfo=UTC), True),
        (datetime(2026, 8, 17, 2, 0, tzinfo=UTC), None, True),
        (
            datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
            datetime(2026, 8, 14, 20, 30, tzinfo=UTC),
            False,
        ),
        (datetime(2026, 8, 17, 2, 0, tzinfo=UTC), datetime(2026, 8, 13, 20, 30, tzinfo=UTC), True),
    ],
)
def test_a_closed_market_is_quoted_once_then_left_alone(
    context: AppContext,
    now: datetime,
    last_quote_at: datetime | None,
    quotable: bool,
) -> None:
    """Before this, an equity synced outside US hours stayed blank until someone looked in them."""
    from dataclasses import replace

    from tradebot.marketdata.refresh import MarketSync

    sync = MarketSync(replace(context, clock=FrozenClock(now)))
    instrument = Instrument(symbol="AAA", asset_class="stock", last_quote_at=last_quote_at)

    assert sync._quotable(instrument) is quotable
