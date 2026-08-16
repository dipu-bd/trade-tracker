from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from tests.test_backtest_sanity import CAPITAL, LAST_BAR, NOW
from tests.test_engine_cycle import seed
from tradebot.backtest.ic import SignalQuality
from tradebot.backtest.metrics import Performance
from tradebot.backtest.report import BacktestReport, StrategyReport, leakage_split
from tradebot.backtest.runner import ReplayResult
from tradebot.backtest.statistics import DeflatedSharpe
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock

PORTFOLIO = {"name": "Replay", "initial_capital": "100000", "allow_fractional": True}


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


def perf(total: float, sharpe: float = 1.0) -> Performance:
    return Performance(
        periods=250,
        total_return=total,
        cagr=total,
        sharpe=sharpe,
        sortino=sharpe,
        calmar=1.0,
        max_drawdown=0.1,
        drawdown_days=10,
        volatility=0.2,
    )


def strategy(total: float, sharpe: float = 1.0, significant: bool = True, orders: int = 10):  # type: ignore[no-untyped-def]
    return StrategyReport(
        label="rules",
        performance=perf(total, sharpe),
        deflated=DeflatedSharpe(
            observed=sharpe,
            expected_max=0.5,
            deflated=3.0 if significant else 0.2,
            probability=0.99 if significant else 0.4,
            trials=3,
            skew=0.0,
            kurtosis=3.0,
        ),
        orders=orders,
    )


def test_losing_to_the_benchmark_is_the_headline_not_a_footnote() -> None:
    """The plan's whole point: being told it is not working is the valuable output."""
    report = BacktestReport(
        strategies=[strategy(0.04)],
        benchmark=StrategyReport("buy_and_hold:SPY", perf(0.12), strategy(0.12).deflated, 1),
    )

    verdict = report.verdict()

    assert verdict.startswith("It did NOT beat")
    assert "an index fund was the better choice" in verdict


def test_beating_the_benchmark_is_stated_with_the_gap() -> None:
    report = BacktestReport(
        strategies=[strategy(0.20)],
        benchmark=StrategyReport("buy_and_hold:SPY", perf(0.12), strategy(0.12).deflated, 1),
    )

    assert "It beat buy_and_hold:SPY by +8.00%" in report.verdict()


def test_a_sharpe_that_fails_deflation_is_called_luck() -> None:
    report = BacktestReport(strategies=[strategy(0.5, sharpe=2.5, significant=False)])

    verdict = report.verdict()

    assert "does NOT survive deflation" in verdict
    assert "indistinguishable from luck" in verdict


def test_failing_to_beat_the_random_control_is_reported() -> None:
    report = BacktestReport(
        strategies=[strategy(0.05)],
        control=StrategyReport("random_entry", perf(0.09), strategy(0.09).deflated, 1),
    )

    assert "did not beat a random-entry control" in report.verdict()
    assert "drift rather than signal" in report.verdict()


def test_a_strategy_that_never_traded_is_not_dressed_up_as_a_result() -> None:
    report = BacktestReport(strategies=[strategy(0.0, orders=0)])

    assert report.verdict() == "The strategy placed no orders. There is nothing to evaluate."


def test_a_failed_run_reports_the_failure() -> None:
    broken = strategy(0.0)
    broken.error = "no bars"

    assert "The run failed: no bars" in BacktestReport(strategies=[broken]).verdict()


def test_a_dead_ai_signal_appears_in_the_verdict() -> None:
    report = BacktestReport(
        strategies=[strategy(0.2)],
        signals=[SignalQuality("llm_confidence", 200, -0.02, -1.5, 100)],
    )

    assert "influence decayed to zero" in report.verdict()


def test_a_large_leakage_gap_is_a_warning_not_a_result() -> None:
    report = BacktestReport(
        strategies=[strategy(0.2)],
        leakage={"checked": True, "gap": 0.9},
    )

    verdict = report.verdict()

    assert "LEAKAGE WARNING" in verdict
    assert "pre-cutoff result as invalid" in verdict


def test_the_leakage_split_treats_a_pre_cutoff_only_run_as_invalid() -> None:
    before = ReplayResult(label="pre", equity=[100.0, 180.0])
    after = ReplayResult(label="post")

    split = leakage_split(before, after, date(2024, 6, 1))

    assert split["checked"] is True
    assert split["valid"] is False
    assert split["gap"] == pytest.approx(0.8)
    assert "invalid" in split["note"]


def test_the_report_serialises_with_its_verdict_and_trial_count() -> None:
    report = BacktestReport(
        start=date(2024, 1, 1), end=date(2024, 12, 31), trials=9, strategies=[strategy(0.1)]
    )

    body = report.as_dict()

    assert body["trials"] == 9
    assert body["verdict"]
    assert body["strategies"][0]["deflated"]["trials"] == 3
    assert body["start"] == "2024-01-01"


async def test_the_backtest_endpoint_returns_a_verdict(
    client: AsyncClient, registered: dict[str, str], context: AppContext
) -> None:
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.002, count=400, end=LAST_BAR)

    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    portfolio_id = created.json()["id"]

    response = await client.post(
        f"/api/portfolios/{portfolio_id}/backtest",
        params={"start": str(LAST_BAR - timedelta(days=120)), "end": str(LAST_BAR)},
        headers=registered,
    )
    body = response.json()

    assert response.status_code == 200
    assert body["verdict"]
    assert body["benchmark"]["label"] == "buy_and_hold:SPY"
    assert body["trials"] >= 1
    assert "deflated" in body["strategies"][0]


async def test_a_backtest_never_writes_into_the_live_portfolio(
    client: AsyncClient, registered: dict[str, str], context: AppContext
) -> None:
    """A replay that touched the real ledger would corrupt the curve it exists to measure."""
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)
    await seed(context, "AAA", daily=0.002, count=400, end=LAST_BAR)

    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    portfolio_id = created.json()["id"]

    await client.post(
        f"/api/portfolios/{portfolio_id}/backtest",
        params={"start": str(LAST_BAR - timedelta(days=90)), "end": str(LAST_BAR)},
        headers=registered,
    )

    orders = await client.get(f"/api/portfolios/{portfolio_id}/orders", headers=registered)
    detail = await client.get(f"/api/portfolios/{portfolio_id}", headers=registered)

    assert orders.json() == []
    assert Decimal(detail.json()["cash"]) == CAPITAL


async def test_the_sandbox_portfolio_is_cleaned_up(
    client: AsyncClient, registered: dict[str, str], context: AppContext
) -> None:
    await seed(context, "SPY", daily=0.0008, count=400, asset_class="index", end=LAST_BAR)

    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    await client.post(
        f"/api/portfolios/{created.json()['id']}/backtest",
        params={"start": str(LAST_BAR - timedelta(days=60)), "end": str(LAST_BAR)},
        headers=registered,
    )

    listed = await client.get("/api/portfolios", headers=registered)

    assert all("__backtest__" not in row["name"] for row in listed.json())


async def test_a_window_without_bars_is_reported_rather_than_crashing(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    created = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)

    response = await client.post(
        f"/api/portfolios/{created.json()['id']}/backtest",
        params={"start": "2020-01-01", "end": "2020-03-01"},
        headers=registered,
    )

    assert response.status_code == 200
    assert response.json()["notes"]


async def test_the_backtest_endpoint_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/api/portfolios/1/backtest")).status_code == 401


def test_the_trial_count_reaching_the_report_is_the_machines_count() -> None:
    """A DSR built on a hand-entered trial count is worthless."""
    report = BacktestReport(trials=4, strategies=[strategy(0.1)])

    assert report.as_dict()["trials"] == 4
