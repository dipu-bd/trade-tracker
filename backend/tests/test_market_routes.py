from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tradebot.context import AppContext
from tradebot.db.models import Instrument, PriceBar

SECRET = "sk-live-should-never-appear-9999"


@pytest.fixture
async def tracked(context: AppContext) -> None:
    async with context.db.session() as session:
        instrument = Instrument(
            symbol="AAPL",
            asset_class="stock",
            name="Apple",
            exchange="NASDAQ",
            sector="Technology",
        )
        session.add(instrument)
        await session.flush()
        for day in range(1, 4):
            session.add(
                PriceBar(
                    instrument_id=instrument.id,
                    bar_date=date(2026, 8, 10 + day),
                    open=Decimal(100),
                    high=Decimal(102),
                    low=Decimal(99),
                    close=Decimal(100 + day),
                    volume=Decimal(1_000_000),
                )
            )


async def test_instruments_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/market/instruments")).status_code == 401


async def test_instruments_are_listed_with_staleness(
    client: AsyncClient, registered: dict[str, str], tracked: None
) -> None:
    response = await client.get("/api/market/instruments", headers=registered)

    assert response.status_code == 200
    row = response.json()[0]
    assert row["symbol"] == "AAPL"
    assert row["asset_class"] == "stock"
    assert "staleness_seconds" in row


async def test_instruments_filter_by_asset_class(
    client: AsyncClient, registered: dict[str, str], tracked: None
) -> None:
    assert (
        await client.get("/api/market/instruments?asset_class=crypto", headers=registered)
    ).json() == []
    assert (
        await client.get("/api/market/instruments?asset_class=stock", headers=registered)
    ).json() != []


async def test_an_unknown_asset_class_is_rejected(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/market/instruments?asset_class=bonds", headers=registered)

    assert response.status_code == 422
    assert "bonds" in response.json()["error"]["message"]


async def test_bars_are_returned_oldest_first(
    client: AsyncClient, registered: dict[str, str], tracked: None
) -> None:
    response = await client.get("/api/market/instruments/AAPL/bars", headers=registered)

    assert response.status_code == 200
    dates = [row["bar_date"] for row in response.json()]
    assert dates == sorted(dates)
    assert len(dates) == 3


async def test_bars_for_an_untracked_symbol_are_a_404(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/market/instruments/NOPE/bars", headers=registered)

    assert response.status_code == 404


async def test_quotes_reject_an_empty_symbol_list(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/market/quotes?symbols=%20", headers=registered)

    assert response.status_code == 422


async def test_quotes_for_untracked_symbols_are_a_404(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/market/quotes?symbols=NOPE", headers=registered)

    assert response.status_code == 404


async def test_provider_status_lists_every_registered_provider(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/market/providers", headers=registered)

    assert response.status_code == 200
    keys = {row["provider"] for row in response.json()}
    assert {"alpaca", "binance", "cryptocom", "fmp", "finnhub", "polygon"} <= keys


async def test_keyless_providers_are_available_without_setup(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    rows = {
        row["provider"]: row
        for row in (await client.get("/api/market/providers", headers=registered)).json()
    }

    assert rows["binance"]["available"] is True
    assert rows["binance"]["keyless"] is True
    assert rows["alpaca"]["available"] is False
    assert sorted(rows["alpaca"]["missing_credentials"]) == ["api_key_id", "secret_key"]


async def test_provider_status_never_leaks_a_stored_secret(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    await client.post(
        "/api/credentials",
        json={"provider_key": "fmp", "field": "api_key", "secret": SECRET},
        headers=registered,
    )

    response = await client.get("/api/market/providers", headers=registered)

    assert SECRET not in response.text
    fmp = next(row for row in response.json() if row["provider"] == "fmp")
    assert fmp["available"] is True
    assert fmp["fields"] == [{"field": "api_key", "masked": "…9999"}]


async def test_provider_status_includes_health(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    rows = (await client.get("/api/market/providers", headers=registered)).json()

    binance = next(row for row in rows if row["provider"] == "binance")
    assert binance["health"]["state"] == "closed"
    assert binance["health"]["requests"] == 0


async def test_sync_rejects_an_unknown_asset_class(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.post(
        "/api/market/sync", json={"asset_classes": ["bonds"]}, headers=registered
    )

    assert response.status_code == 422


async def test_sync_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/market/sync", json={"asset_classes": ["crypto"]})

    assert response.status_code == 401


async def test_instruments_are_visible_to_any_authenticated_user(
    client: AsyncClient, registered: dict[str, str], tracked: None, context: AppContext
) -> None:
    """Instruments are shared reference data, unlike credentials which are per user."""
    async with context.db.session() as session:
        count = len(list(await session.scalars(select(Instrument))))

    response = await client.get("/api/market/instruments", headers=registered)
    assert len(response.json()) == count


async def test_sync_returns_immediately_and_reports_progress(
    client: AsyncClient, registered: dict[str, str], context: AppContext
) -> None:
    """The pass is minutes of provider calls, so the request must not wait for it."""
    started = await client.post(
        "/api/market/sync", json={"asset_classes": ["etf", "stock"]}, headers=registered
    )
    assert started.status_code == 200
    assert started.json()["running"] is True

    await context.sync_job.wait()

    status = await client.get("/api/market/sync", headers=registered)
    assert status.status_code == 200
    assert status.json()["running"] is False


async def test_a_second_sync_is_refused_while_one_is_running(
    client: AsyncClient, registered: dict[str, str], context: AppContext
) -> None:
    await client.post("/api/market/sync", json={"asset_classes": ["etf"]}, headers=registered)
    second = await client.post(
        "/api/market/sync", json={"asset_classes": ["etf"]}, headers=registered
    )
    await context.sync_job.wait()

    assert second.status_code == 409


async def test_one_failing_asset_class_does_not_cost_the_others(context: AppContext) -> None:
    """A listing that 402s used to raise a 500 that took every other sleeve with it."""
    from tradebot.marketdata.refresh import MarketSync
    from tradebot.providers.base import AssetClass

    report = await MarketSync(context).discover(
        1, asset_classes=[AssetClass.STOCK, AssetClass.ETF], limit=5
    )

    assert len(report.failed) == 2
