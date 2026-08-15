from decimal import Decimal

import pytest
from httpx import AsyncClient

from tradebot.context import AppContext
from tradebot.db.models import Instrument

PORTFOLIO = {"name": "Main", "initial_capital": "100000", "allow_fractional": True}


@pytest.fixture
async def instrument(context: AppContext) -> None:
    async with context.db.session() as session:
        session.add(
            Instrument(
                symbol="AAA",
                asset_class="crypto",
                last_quote_price=Decimal(100),
            )
        )


@pytest.fixture
async def portfolio_id(client: AsyncClient, registered: dict[str, str]) -> int:
    response = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    return int(response.json()["id"])


async def test_portfolios_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/portfolios")).status_code == 401
    assert (await client.post("/api/portfolios", json=PORTFOLIO)).status_code == 401


async def test_creating_a_portfolio_seeds_the_ledger(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    detail = await client.get(f"/api/portfolios/{portfolio_id}", headers=registered)
    ledger = await client.get(f"/api/portfolios/{portfolio_id}/ledger", headers=registered)

    assert Decimal(detail.json()["cash"]) == Decimal(100_000)
    assert Decimal(detail.json()["equity"]) == Decimal(100_000)
    assert [row["entry_type"] for row in ledger.json()] == ["DEPOSIT"]


async def test_a_duplicate_name_conflicts(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    response = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)

    assert response.status_code == 409


async def test_zero_capital_is_rejected(client: AsyncClient, registered: dict[str, str]) -> None:
    response = await client.post(
        "/api/portfolios", json={**PORTFOLIO, "initial_capital": "0"}, headers=registered
    )

    assert response.status_code == 422


async def test_another_users_portfolio_is_not_visible(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    await client.post(
        "/api/auth/register",
        json={
            "email": "other@example.com",
            "password": "correct-horse-battery-staple",
            "display_name": "Other",
        },
    )
    login = await client.post(
        "/api/auth/login",
        json={"email": "other@example.com", "password": "correct-horse-battery-staple"},
    )
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.get("/api/portfolios", headers=other)).json() == []
    assert (await client.get(f"/api/portfolios/{portfolio_id}", headers=other)).status_code == 404


async def test_placing_an_order_for_an_untracked_symbol_is_a_404(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    response = await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={"symbol": "NOPE", "side": "BUY", "qty": "1"},
        headers=registered,
    )

    assert response.status_code == 404


async def test_an_order_is_accepted_and_reserves_cash(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, instrument: None
) -> None:
    response = await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={
            "symbol": "AAA",
            "side": "BUY",
            "qty": "10",
            "order_type": "LIMIT",
            "limit_price": "100",
        },
        headers=registered,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "ACCEPTED"
    assert Decimal(response.json()["reserved_cash"]) > Decimal(1000)

    detail = await client.get(f"/api/portfolios/{portfolio_id}", headers=registered)
    assert Decimal(detail.json()["buying_power"]) < Decimal(100_000)


async def test_an_unaffordable_order_comes_back_rejected(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, instrument: None
) -> None:
    """A rejection is a result, not an error — the caller needs the reason."""
    response = await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={
            "symbol": "AAA",
            "side": "BUY",
            "qty": "100000",
            "order_type": "LIMIT",
            "limit_price": "100",
        },
        headers=registered,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "REJECTED"
    assert "buying power" in response.json()["reject_reason"]


async def test_an_invalid_order_type_is_refused(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, instrument: None
) -> None:
    response = await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={"symbol": "AAA", "side": "BUY", "qty": "1", "order_type": "ICEBERG"},
        headers=registered,
    )

    assert response.status_code == 422


async def test_cancelling_releases_the_reservation(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, instrument: None
) -> None:
    order = await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={
            "symbol": "AAA",
            "side": "BUY",
            "qty": "10",
            "order_type": "LIMIT",
            "limit_price": "100",
        },
        headers=registered,
    )
    order_id = order.json()["id"]

    cancelled = await client.delete(
        f"/api/portfolios/{portfolio_id}/orders/{order_id}", headers=registered
    )

    assert cancelled.json()["status"] == "CANCELED"
    detail = await client.get(f"/api/portfolios/{portfolio_id}", headers=registered)
    assert Decimal(detail.json()["buying_power"]) == Decimal(100_000)


async def test_cancelling_a_filled_order_is_refused(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, instrument: None
) -> None:
    order = await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={
            "symbol": "AAA",
            "side": "BUY",
            "qty": "10",
            "order_type": "LIMIT",
            "limit_price": "100",
        },
        headers=registered,
    )
    order_id = order.json()["id"]
    await client.delete(f"/api/portfolios/{portfolio_id}/orders/{order_id}", headers=registered)

    again = await client.delete(
        f"/api/portfolios/{portfolio_id}/orders/{order_id}", headers=registered
    )

    assert again.status_code == 422


async def test_a_reused_client_order_id_returns_the_same_order(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, instrument: None
) -> None:
    body = {
        "symbol": "AAA",
        "side": "BUY",
        "qty": "5",
        "order_type": "LIMIT",
        "limit_price": "100",
        "client_order_id": "idem-1",
    }

    first = await client.post(
        f"/api/portfolios/{portfolio_id}/orders", json=body, headers=registered
    )
    second = await client.post(
        f"/api/portfolios/{portfolio_id}/orders", json=body, headers=registered
    )

    assert first.json()["id"] == second.json()["id"]
    orders = await client.get(f"/api/portfolios/{portfolio_id}/orders", headers=registered)
    assert len(orders.json()) == 1


async def test_reconciliation_reports_clean_on_a_new_portfolio(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    response = await client.post(f"/api/portfolios/{portfolio_id}/reconcile", headers=registered)

    assert response.json()["ok"] is True
    assert response.json()["problems"] == []


async def test_a_snapshot_records_the_equity_point(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    created = await client.post(f"/api/portfolios/{portfolio_id}/snapshots", headers=registered)
    listed = await client.get(f"/api/portfolios/{portfolio_id}/snapshots", headers=registered)

    assert Decimal(created.json()["equity"]) == Decimal(100_000)
    assert len(listed.json()) == 1


async def test_positions_and_fills_start_empty(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int
) -> None:
    assert (
        await client.get(f"/api/portfolios/{portfolio_id}/positions", headers=registered)
    ).json() == []
    assert (
        await client.get(f"/api/portfolios/{portfolio_id}/fills", headers=registered)
    ).json() == []
