from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tradebot.analytics.features import extract
from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.core.errors import LookAheadError
from tradebot.db.models import DecisionRun, Instrument, Order, PriceBar
from tradebot.engine.cycle import DecisionCycle
from tradebot.engine.universe import UniverseSpec, resolve
from tradebot.marketdata.view import MarketView

NOW = datetime(2024, 12, 3, 12, 0, tzinfo=UTC)
LAST_BAR = NOW.date() - timedelta(days=1)


def closes(count: int, daily: float) -> list[float]:
    return [100.0 * (1 + daily) ** index for index in range(count)]


async def seed(
    context: AppContext,
    symbol: str,
    *,
    daily: float = 0.001,
    count: int = 400,
    asset_class: str = "stock",
    volume: Decimal = Decimal(3_000_000),
    name: str = "",
    end: object = None,
) -> int:
    last = end or LAST_BAR
    async with context.db.session() as session:
        instrument = Instrument(
            symbol=symbol,
            asset_class=asset_class,
            name=name or symbol,
            first_bar_date=last - timedelta(days=count),  # type: ignore[operator]
            last_bar_date=last,  # type: ignore[arg-type]
            last_quote_price=None,
        )
        session.add(instrument)
        await session.flush()

        for offset, close in enumerate(closes(count, daily)):
            price = Decimal(str(round(close, 6)))
            session.add(
                PriceBar(
                    instrument_id=instrument.id,
                    bar_date=last - timedelta(days=count - 1 - offset),  # type: ignore[operator]
                    open=price,
                    high=price * Decimal("1.01"),
                    low=price * Decimal("0.99"),
                    close=price,
                    volume=volume,
                    source="test",
                    adjusted=True,
                )
            )
        return int(instrument.id)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
async def portfolio_row(context: AppContext, clock: FrozenClock):  # type: ignore[no-untyped-def]
    broker = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    async with context.db.session() as session:
        from tradebot.db.models import User

        user = User(email="engine@example.com", password_hash="x", display_name="Engine")
        session.add(user)
        await session.flush()
        portfolio = await broker.create_portfolio(
            session,
            user_id=user.id,
            name="Momentum",
            initial_capital=Decimal(100_000),
            allow_fractional=True,
        )
        portfolio.autopilot = True
        return int(portfolio.id)


def cycle(context: AppContext, clock: FrozenClock) -> DecisionCycle:
    broker = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    return DecisionCycle(broker, context.events, clock)


async def test_market_view_refuses_data_stamped_after_the_clock(
    context: AppContext, clock: FrozenClock
) -> None:
    """The one bug that would invalidate every backtest silently, so it gets an explicit guard."""
    async with context.db.session() as session:
        view = MarketView(session, clock)

        with pytest.raises(LookAheadError):
            view.assert_visible(NOW + timedelta(seconds=1))

        view.assert_visible(NOW)
        view.assert_visible(NOW - timedelta(days=1))


async def test_market_view_hides_a_bar_whose_session_has_not_closed(
    context: AppContext, clock: FrozenClock
) -> None:
    """Today's bar contains a close that has not happened yet."""
    await seed(context, "AAA", end=NOW.date())

    async with context.db.session() as session:
        series = await MarketView(session, clock).bars("AAA")

    assert series.last_date == LAST_BAR
    assert all(bar.bar_date <= LAST_BAR for bar in series.bars)


async def test_market_view_reveals_the_bar_once_the_session_has_closed(context: AppContext) -> None:
    await seed(context, "AAA", end=NOW.date())
    after_close = FrozenClock(datetime(2024, 12, 3, 22, 0, tzinfo=UTC))

    async with context.db.session() as session:
        series = await MarketView(session, after_close).bars("AAA")

    assert series.last_date == NOW.date()


async def test_market_view_refuses_a_quote_stamped_in_the_future(
    context: AppContext, clock: FrozenClock
) -> None:
    instrument_id = await seed(context, "AAA")
    async with context.db.session() as session:
        instrument = await session.get(Instrument, instrument_id)
        instrument.last_quote_price = Decimal(100)
        instrument.last_quote_at = NOW + timedelta(hours=1)

    async with context.db.session() as session:
        with pytest.raises(LookAheadError):
            await MarketView(session, clock).quote("AAA")


async def test_market_view_falls_back_to_the_last_close_without_a_quote(
    context: AppContext, clock: FrozenClock
) -> None:
    await seed(context, "AAA")

    async with context.db.session() as session:
        view = MarketView(session, clock)
        assert await view.quote("AAA") is None
        assert await view.mark("AAA") is not None


async def test_crypto_bars_are_complete_only_after_utc_midnight(
    context: AppContext, clock: FrozenClock
) -> None:
    async with context.db.session() as session:
        view = MarketView(session, clock)

        assert view.last_complete_bar_date("crypto") == NOW.date() - timedelta(days=1)


async def test_features_are_computed_once_per_symbol_per_cycle(
    context: AppContext, clock: FrozenClock
) -> None:
    await seed(context, "AAA")

    async with context.db.session() as session:
        view = MarketView(session, clock)
        first = await view.features("AAA")
        second = await view.features("aaa")

    assert first is second


async def test_universe_resolution_skips_instruments_without_bars(context: AppContext) -> None:
    await seed(context, "WITHBARS")
    async with context.db.session() as session:
        session.add(Instrument(symbol="NOBARS", asset_class="stock", name="No Bars"))

    async with context.db.session() as session:
        resolved = await resolve(session, UniverseSpec())

    assert resolved.symbols == ["WITHBARS"]


async def test_the_never_list_is_honoured_unless_the_name_is_held(context: AppContext) -> None:
    await seed(context, "AAA")
    await seed(context, "BBB")

    async with context.db.session() as session:
        spec = UniverseSpec(never=("BBB",))
        assert (await resolve(session, spec)).symbols == ["AAA"]
        assert "BBB" in (await resolve(session, spec, held=frozenset({"BBB"}))).symbols


async def test_a_cycle_on_a_trending_universe_places_buy_orders(
    context: AppContext, clock: FrozenClock, portfolio_row: int
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "AAA", daily=0.002)
    await seed(context, "BBB", daily=0.0015)

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        report = await cycle(context, clock).run(session, portfolio, trigger="manual")

    assert report.ok, report.error
    assert report.decision is not None
    assert report.orders

    async with context.db.session() as session:
        orders = list(await session.scalars(select(Order)))

    assert orders
    assert all(order.side == "BUY" for order in orders)
    assert all(order.status in ("ACCEPTED", "FILLED") for order in orders)


async def test_a_cycle_records_a_decision_run_with_its_reasoning(
    context: AppContext, clock: FrozenClock, portfolio_row: int
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "AAA", daily=0.002)
    await seed(context, "THIN", daily=0.002, volume=Decimal(1))

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        await cycle(context, clock).run(session, portfolio)

    async with context.db.session() as session:
        run = await session.scalar(select(DecisionRun))

    assert run.status == "ok"
    assert run.as_of == LAST_BAR
    assert run.regime
    assert "THIN" in run.detail["screened_out"]
    assert [item["symbol"] for item in run.detail["entries"]] == ["AAA"]


async def test_a_cycle_on_a_falling_universe_places_nothing(
    context: AppContext, clock: FrozenClock, portfolio_row: int
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "DOWN", daily=-0.002)

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        report = await cycle(context, clock).run(session, portfolio)

    assert report.ok
    assert report.orders == []


async def test_a_cycle_emits_a_correlated_event_sequence(
    context: AppContext, clock: FrozenClock, portfolio_row: int
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "AAA", daily=0.002)

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        report = await cycle(context, clock).run(session, portfolio)

    async with context.db.session() as session:
        from tradebot.db.models import Event

        events = list(
            await session.scalars(
                select(Event)
                .where(Event.correlation_id == report.correlation_id)
                .order_by(Event.id)
            )
        )

    kinds = [event.kind for event in events]
    assert kinds[0] == "cycle_started"
    assert kinds[-1] == "cycle_finished"
    assert "screened" in kinds
    assert {event.domain for event in events} == {"engine"}


async def test_a_leveraged_name_is_never_bought_by_a_live_cycle(
    context: AppContext, clock: FrozenClock, portfolio_row: int
) -> None:
    """Carried forward from M2, asserted against the real path rather than the pure function."""
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "TQQQ", daily=0.004, name="ProShares UltraPro QQQ")
    await seed(context, "AAA", daily=0.002)

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        await cycle(context, clock).run(session, portfolio)

    async with context.db.session() as session:
        bought = list(
            await session.scalars(
                select(Instrument.symbol).join(Order, Order.instrument_id == Instrument.id)
            )
        )

    assert "TQQQ" not in bought
    assert "AAA" in bought


async def test_a_second_cycle_does_not_re_buy_a_name_with_a_working_order(
    context: AppContext, clock: FrozenClock, portfolio_row: int
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "AAA", daily=0.002)

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        first = await cycle(context, clock).run(session, portfolio)

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        second = await cycle(context, clock).run(session, portfolio)

    assert first.orders
    assert second.orders == []
    assert second.skipped_orders["AAA"] == "an order is already working"

    async with context.db.session() as session:
        orders = list(await session.scalars(select(Order)))

    assert len(orders) == len(first.orders)
    assert len({order.client_order_id for order in orders}) == len(orders)


async def test_a_cycle_failure_is_recorded_rather_than_raised(
    context: AppContext, clock: FrozenClock, portfolio_row: int
) -> None:
    broken = cycle(context, clock)

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("provider exploded")

    broken._decide = explode  # type: ignore[method-assign]

    async with context.db.session() as session:
        from tradebot.db.models import Portfolio

        portfolio = await session.get(Portfolio, portfolio_row)
        report = await broken.run(session, portfolio)

    assert not report.ok
    assert "provider exploded" in (report.error or "")

    async with context.db.session() as session:
        run = await session.scalar(select(DecisionRun))

    assert run.status == "failed"
    assert "provider exploded" in (run.error or "")


async def test_features_from_stored_bars_match_the_pure_extractor(
    context: AppContext, clock: FrozenClock
) -> None:
    """The DB path and the pure path must agree, or the backtest and live diverge silently."""
    await seed(context, "AAA", daily=0.002)

    async with context.db.session() as session:
        view = MarketView(session, clock)
        series = await view.bars("AAA")
        from_view = await view.features("AAA")

    assert from_view == extract(series)
