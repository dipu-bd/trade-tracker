from decimal import Decimal

import pytest
from httpx import AsyncClient

from tests.test_engine_cycle import NOW, seed
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock

PORTFOLIO = {"name": "Momentum", "initial_capital": "100000", "allow_fractional": True}


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


@pytest.fixture
async def portfolio_id(client: AsyncClient, registered: dict[str, str]) -> int:
    response = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    return int(response.json()["id"])


async def test_the_strategy_endpoint_resolves_every_default(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    response = await client.get(f"/api/portfolios/{portfolio_id}/strategy", headers=registered)
    body = response.json()

    assert body["benchmark"] == "SPY"
    assert body["cadence"] == "daily"
    assert body["autopilot"] is False
    assert body["sizing"]["risk_per_trade"] == 0.01
    assert body["regime"]["panic_exposure"] == 0.25
    assert body["costs"]["slippage_bps"] == 10.0


async def test_the_parameter_count_is_reported_for_the_deflated_sharpe(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    """Every knob is a degree of freedom, so the count has to be visible rather than implicit."""
    response = await client.get(f"/api/portfolios/{portfolio_id}/strategy", headers=registered)

    assert response.json()["parameter_count"] > 20


async def test_strategy_settings_round_trip(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    payload = {
        "benchmark": "qqq",
        "cadence": "hourly",
        "autopilot": True,
        "strategy": {"sizing": {"risk_per_trade": 0.005, "max_positions": 8}},
        "universe": {"asset_classes": ["stock"], "never": ["TSLA"]},
    }

    updated = await client.put(
        f"/api/portfolios/{portfolio_id}/strategy", json=payload, headers=registered
    )
    body = updated.json()

    assert body["benchmark"] == "QQQ"
    assert body["cadence"] == "hourly"
    assert body["autopilot"] is True
    assert body["sizing"]["risk_per_trade"] == 0.005
    assert body["sizing"]["max_positions"] == 8


async def test_an_unknown_key_inside_a_section_is_ignored_not_rejected(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    payload = {"strategy": {"sizing": {"nonsense": 1, "risk_per_trade": 0.02}}}

    response = await client.put(
        f"/api/portfolios/{portfolio_id}/strategy", json=payload, headers=registered
    )

    assert response.status_code == 200
    assert response.json()["sizing"]["risk_per_trade"] == 0.02


async def test_an_unknown_cadence_is_refused(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    response = await client.put(
        f"/api/portfolios/{portfolio_id}/strategy",
        json={"cadence": "every-fortnight"},
        headers=registered,
    )

    assert response.status_code == 404


async def test_running_a_cycle_places_orders_and_is_inspectable(
    client: AsyncClient,
    registered: dict[str, str],
    context: AppContext,
    portfolio_id: int,
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "AAA", daily=0.002)
    await seed(context, "THIN", daily=0.002, volume=Decimal(1))

    triggered = await client.post(f"/api/portfolios/{portfolio_id}/cycles", headers=registered)
    body = triggered.json()

    assert triggered.status_code == 201
    assert body["status"] == "ok"
    assert body["orders_placed"] >= 1
    assert body["regime"]

    detail = await client.get(
        f"/api/portfolios/{portfolio_id}/cycles/{body['run_id']}", headers=registered
    )
    reasoning = detail.json()["detail"]

    assert "THIN" in reasoning["screened_out"]
    assert [item["symbol"] for item in reasoning["entries"]] == ["AAA"]
    assert reasoning["entries"][0]["binding"]


async def test_cycles_are_listed_newest_first(
    client: AsyncClient, registered: dict[str, str], context: AppContext, portfolio_id: int
) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "AAA", daily=0.002)

    await client.post(f"/api/portfolios/{portfolio_id}/cycles", headers=registered)
    await client.post(f"/api/portfolios/{portfolio_id}/cycles", headers=registered)

    listed = await client.get(f"/api/portfolios/{portfolio_id}/cycles", headers=registered)
    rows = listed.json()

    assert len(rows) == 2
    assert rows[0]["id"] > rows[1]["id"]


async def test_a_cycle_on_an_empty_universe_succeeds_without_orders(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    triggered = await client.post(f"/api/portfolios/{portfolio_id}/cycles", headers=registered)

    assert triggered.json()["status"] == "ok"
    assert triggered.json()["orders_placed"] == 0


async def test_engine_routes_require_authentication(client: AsyncClient, portfolio_id: int) -> None:
    assert (await client.get(f"/api/portfolios/{portfolio_id}/strategy")).status_code == 401
    assert (await client.post(f"/api/portfolios/{portfolio_id}/cycles")).status_code == 401
    assert (await client.get("/api/engine/schedule")).status_code == 401


async def test_another_users_cycles_are_not_visible(
    client: AsyncClient,
    registered: dict[str, str],
    portfolio_id: int,
    other_user: dict[str, str],
) -> None:
    response = await client.get(f"/api/portfolios/{portfolio_id}/cycles", headers=other_user)

    assert response.status_code == 404


async def test_an_unknown_run_id_is_a_404(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    response = await client.get(f"/api/portfolios/{portfolio_id}/cycles/999", headers=registered)

    assert response.status_code == 404


async def test_the_schedule_is_empty_when_the_scheduler_is_disabled(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/engine/schedule", headers=registered)

    assert response.json() == []


async def test_the_scheduler_lists_the_bar_refresh_alongside_the_cycles(
    context: AppContext,
) -> None:
    """A job id the cron lookup did not know about used to raise rather than list."""
    from tradebot.workers.scheduler import EngineScheduler

    scheduler = EngineScheduler(context)
    scheduler.start()
    try:
        jobs = {job["id"]: job for job in scheduler.jobs()}
    finally:
        scheduler.shutdown()

    assert "market:bars" in jobs
    assert jobs["market:bars"]["cron"]
    assert all(job["cron"] for job in jobs.values())


async def test_the_bar_refresh_adds_no_instruments_when_none_are_tracked(
    context: AppContext,
) -> None:
    from tradebot.marketdata.refresh import MarketDataRefresher

    report = await MarketDataRefresher(context).refresh_all()

    assert report.instruments == 0
    assert report.bars_written == 0
