"""The self-honesty loop: impact costs, leakage, ablation and de-weighting.

Each of these is easy to build as a no-op that passes a test and does nothing, so every test
here asserts the mechanism *changed an outcome*, not merely that it ran.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from tests.test_backtest_sanity import LAST_BAR, NOW, make_portfolio
from tests.test_engine_cycle import seed
from tradebot.backtest import ic
from tradebot.backtest.service import BacktestService
from tradebot.broker.costs import CostModel
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.db.models import DecisionRun, Fill, Instrument, Order, Portfolio, PriceBar

PORTFOLIO = {"name": "Replay", "initial_capital": "100000", "allow_fractional": True}


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
async def settings(tmp_path):  # type: ignore[no-untyped-def]
    from tradebot.core.settings import Settings

    return Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        secret_key="unit-test-secret-key-long-enough-000000",
        cookie_secure=False,
        log_json=False,
        scheduler_enabled=False,
    )


@pytest.fixture
async def context(settings):  # type: ignore[no-untyped-def]
    from tradebot.db.models import Base

    ctx = AppContext.build(settings, clock=FrozenClock(NOW))
    async with ctx.db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield ctx
    await ctx.aclose()


def model(**kwargs: object) -> CostModel:
    base: dict[str, object] = {
        "slippage_bps": Decimal(10),
        "commission_bps": Decimal(0),
        "min_commission": Decimal(0),
    }
    return CostModel(**{**base, **kwargs})  # type: ignore[arg-type]


def test_impact_is_zero_for_an_order_that_moves_nothing() -> None:
    assert model().at_participation(Decimal(0)).impact_bps == Decimal(0)


def test_impact_grows_with_the_square_root_of_participation() -> None:
    """A flat guess prices a $1k and a $10m order in the same name identically."""
    small = model().at_participation(Decimal("0.0001")).impact_bps
    large = model().at_participation(Decimal("0.01")).impact_bps

    assert large > small > 0
    # 100x the participation is 10x the impact, not 100x.
    assert large / small == pytest.approx(Decimal(10), rel=Decimal("0.01"))


def test_a_larger_order_fills_at_a_worse_price() -> None:
    reference = Decimal(100)

    tiny = model().at_participation(Decimal("0.00001")).fill_price(reference, "BUY")
    huge = model().at_participation(Decimal("0.25")).fill_price(reference, "BUY")

    assert huge > tiny > reference


def test_impact_moves_a_sell_the_other_way() -> None:
    reference = Decimal(100)

    assert model().at_participation(Decimal("0.25")).fill_price(reference, "SELL") < reference


async def test_a_replay_charges_more_impact_on_a_thin_book_than_a_thick_one(
    context: AppContext, clock: FrozenClock
) -> None:
    """The check that impact is wired into replay, not merely implemented.

    Same price path, same order sizing, hundred-fold difference in daily volume: the thin name
    must fill further from its reference price.
    """
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "THICK", daily=0.003, count=400, volume=Decimal(400_000_000), end=LAST_BAR)
    await seed(context, "THIN", daily=0.003, count=400, volume=Decimal(400_000), end=LAST_BAR)

    from tests.test_backtest_sanity import days_for, replay

    days = await days_for(context, 45)
    portfolio_id = await make_portfolio(context, clock)
    await replay(context, portfolio_id, days)

    async with context.db.session() as session:
        rows = await session.execute(
            select(Instrument.symbol, Fill.price, PriceBar.open)
            .join(Order, Fill.order_id == Order.id)
            .join(Instrument, Order.instrument_id == Instrument.id)
            .join(
                PriceBar,
                (PriceBar.instrument_id == Instrument.id)
                & (PriceBar.bar_date == func.date(Fill.executed_at)),
            )
            .where(Order.portfolio_id == portfolio_id, Order.side == "BUY")
        )
        slippage = {}
        for symbol, price, reference in rows.all():
            if reference and reference > 0:
                slippage.setdefault(symbol, []).append(float(price / reference - 1))

    assert {"THIN", "THICK"} <= set(slippage), f"both names must trade, got {sorted(slippage)}"

    thin = sum(slippage["THIN"]) / len(slippage["THIN"])
    thick = sum(slippage["THICK"]) / len(slippage["THICK"])

    assert thin > thick, "a thin book must cost more to enter than a thick one"


async def test_the_ablation_runs_every_arm_on_the_same_window_and_trial_count(
    context: AppContext, clock: FrozenClock
) -> None:
    """Arms deflated by different trial counts would flatter whichever was tried least."""
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.002, count=400, end=LAST_BAR)
    portfolio_id = await make_portfolio(context, clock)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        report = await BacktestService(context.events).ablate(
            session,
            portfolio,
            LAST_BAR - timedelta(days=90),
            LAST_BAR,
            arms={"rules_only": None, "rules_repeat": None},
        )

    assert len(report.strategies) == 2
    periods = {item.performance.periods for item in report.strategies}
    trials = {item.deflated.trials for item in report.strategies}

    assert len(periods) == 1, "arms ran different windows"
    assert len(trials) == 1, "arms were deflated by different trial counts"
    assert "same trial count" in " ".join(report.notes)


async def test_a_leakage_check_reports_both_sides_of_the_cutoff(
    context: AppContext, clock: FrozenClock
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.002, count=700, end=LAST_BAR)
    portfolio_id = await make_portfolio(context, clock)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        split = await BacktestService(context.events).leakage_check(
            session, portfolio, LAST_BAR - timedelta(days=200), span_days=150
        )

    assert split["checked"] is True
    assert "pre_cutoff_return" in split and "post_cutoff_return" in split
    assert "gap" in split
    assert "invalid" in str(split["note"])


async def test_a_leakage_check_without_history_on_both_sides_says_so(
    context: AppContext, clock: FrozenClock
) -> None:
    await seed(context, "SPY", daily=0.0008, count=100, asset_class="index", end=LAST_BAR)
    portfolio_id = await make_portfolio(context, clock)

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        split = await BacktestService(context.events).leakage_check(
            session, portfolio, LAST_BAR - timedelta(days=900), span_days=60
        )

    assert split["checked"] is False
    assert "not enough history" in str(split["reason"])


async def test_measuring_ic_from_stored_decisions_finds_no_signal_in_a_flat_confidence(
    context: AppContext, clock: FrozenClock
) -> None:
    """The de-weighting input comes from what the guardrails already recorded."""
    await seed(context, "AAA", daily=0.002, count=400, end=LAST_BAR)
    portfolio_id = await make_portfolio(context, clock)

    async with context.db.session() as session:
        for index in range(30):
            session.add(
                DecisionRun(
                    portfolio_id=portfolio_id,
                    correlation_id=f"c{index}",
                    started_at=NOW,
                    as_of=LAST_BAR - timedelta(days=200 - index),
                    status="ok",
                    detail={"ai": {"confidence": {"AAA": 0.5}}},
                )
            )

    async with context.db.session() as session:
        quality = await ic.measure_from_decisions(session, portfolio_id)

    assert quality.observations > 0
    assert quality.mean_ic == 0.0, "a constant confidence orders nothing"


async def test_measuring_ic_with_no_decisions_reports_warm_up_rather_than_failure(
    context: AppContext, clock: FrozenClock
) -> None:
    portfolio_id = await make_portfolio(context, clock)

    async with context.db.session() as session:
        quality = await ic.measure_from_decisions(session, portfolio_id)

    assert quality.observations == 0
    assert quality.is_warming_up
    assert quality.weight == 1.0


def test_deweighting_a_failing_signal_removes_its_influence_entirely() -> None:
    """Item 13 must bite: a measurably useless AI ends at zero, not merely reduced."""
    failing = ic.SignalQuality(
        "llm_confidence", observations=200, mean_ic=-0.08, t_stat=-3.1, windows=90
    )

    clamped = ic.apply_deweighting({"AAA": 0.9, "BBB": 0.4}, failing)

    assert clamped == {"AAA": 0.0, "BBB": 0.0}
    assert failing.weight == 0.0
    assert "not positive" in failing.verdict()


def test_a_weak_but_positive_signal_is_partially_deweighted() -> None:
    weak = ic.SignalQuality(
        "llm_confidence", observations=200, mean_ic=0.02, t_stat=1.0, windows=90
    )

    clamped = ic.apply_deweighting({"AAA": 1.0}, weak)

    assert 0.0 < clamped["AAA"] < 1.0
    assert "not distinguishable from noise" in weak.verdict()


def test_a_reliable_signal_keeps_all_of_its_influence() -> None:
    strong = ic.SignalQuality(
        "llm_confidence", observations=200, mean_ic=0.09, t_stat=4.0, windows=90
    )

    assert ic.apply_deweighting({"AAA": 0.8}, strong) == {"AAA": 0.8}
    assert "reliably positive" in strong.verdict()


async def test_the_ablation_endpoint_returns_arms_and_a_verdict(
    client: AsyncClient, registered: dict[str, str], context: AppContext
) -> None:
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.002, count=400, end=LAST_BAR)

    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    response = await client.post(
        f"/api/portfolios/{created.json()['id']}/backtest/ablation",
        params={"start": str(LAST_BAR - timedelta(days=90)), "end": str(LAST_BAR)},
        headers=registered,
    )
    body = response.json()

    assert response.status_code == 200
    assert body["strategies"]
    assert body["verdict"]


async def test_the_leakage_endpoint_requires_a_cutoff(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)

    response = await client.post(
        f"/api/portfolios/{created.json()['id']}/backtest/leakage", headers=registered
    )

    assert response.status_code == 422
