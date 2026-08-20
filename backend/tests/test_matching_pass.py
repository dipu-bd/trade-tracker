"""The loop that turns a placed order into a fill.

Every one of these would have passed vacuously before `MatchingPass` existed, because nothing
in the live app ever called `BrokerService.on_quote` — orders were placed and then rested at
ACCEPTED forever. They assert the join, not the matcher, which `test_broker_service` covers.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tradebot.context import AppContext
from tradebot.db.models import (
    Instrument,
    Order,
    OrderStatus,
    Portfolio,
    Position,
    PositionStatus,
)
from tradebot.workers.matching import MatchingPass

PORTFOLIO = {"name": "Main", "initial_capital": "100000", "allow_fractional": True}

# A Wednesday inside US regular hours, and the Saturday after it.
OPEN_MOMENT = datetime(2024, 12, 4, 16, 0, tzinfo=UTC)
CLOSED_MOMENT = datetime(2024, 12, 7, 16, 0, tzinfo=UTC)


@pytest.fixture
async def portfolio_id(client: AsyncClient, registered: dict[str, str]) -> int:
    response = await client.post("/api/portfolios", json=PORTFOLIO, headers=registered)
    return int(response.json()["id"])


@pytest.fixture
async def crypto(context: AppContext) -> int:
    """Crypto so the venue is open whatever the wall clock says during the test run."""
    async with context.db.session() as session:
        instrument = Instrument(
            symbol="AAA-USD",
            asset_class="crypto",
            last_quote_price=Decimal(100),
            last_quote_at=context.clock.now(),
        )
        session.add(instrument)
        await session.flush()
        return int(instrument.id)


async def buy(client: AsyncClient, headers: dict[str, str], portfolio_id: int, qty: str) -> int:
    response = await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={"symbol": "AAA-USD", "side": "BUY", "qty": qty},
        headers=headers,
    )
    assert response.json()["status"] == "ACCEPTED", response.json()
    return int(response.json()["id"])


async def order_status(context: AppContext, order_id: int) -> str:
    async with context.db.session() as session:
        return str(await session.scalar(select(Order.status).where(Order.id == order_id)))


async def test_a_resting_order_fills_on_the_next_pass(
    client: AsyncClient,
    registered: dict[str, str],
    context: AppContext,
    portfolio_id: int,
    crypto: int,
) -> None:
    """The regression this module exists for: ACCEPTED in, FILLED out."""
    order_id = await buy(client, registered, portfolio_id, "10")

    report = await MatchingPass(context).run()

    assert report.filled == 1
    assert await order_status(context, order_id) == OrderStatus.FILLED

    positions = await client.get(f"/api/portfolios/{portfolio_id}/positions", headers=registered)
    assert [row["symbol"] for row in positions.json()] == ["AAA-USD"]
    assert Decimal(positions.json()[0]["qty"]) == Decimal(10)

    detail = await client.get(f"/api/portfolios/{portfolio_id}", headers=registered)
    assert Decimal(detail.json()["cash"]) < Decimal(100_000)
    assert Decimal(detail.json()["reserved"]) == 0


async def test_a_sell_closes_the_position(
    client: AsyncClient,
    registered: dict[str, str],
    context: AppContext,
    portfolio_id: int,
    crypto: int,
) -> None:
    await buy(client, registered, portfolio_id, "10")
    await MatchingPass(context).run()

    await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={"symbol": "AAA-USD", "side": "SELL", "qty": "10"},
        headers=registered,
    )
    report = await MatchingPass(context).run()

    assert report.filled == 1
    async with context.db.session() as session:
        position = await session.scalar(
            select(Position).where(Position.portfolio_id == portfolio_id)
        )
    assert position is not None
    assert position.status == PositionStatus.CLOSED
    assert position.qty == 0


async def test_a_breached_stop_exits_within_the_same_pass(
    client: AsyncClient,
    registered: dict[str, str],
    context: AppContext,
    portfolio_id: int,
    crypto: int,
) -> None:
    """Stops run before the order loop, so the sell they place is also filled by that pass."""
    await buy(client, registered, portfolio_id, "10")
    await MatchingPass(context).run()

    async with context.db.session() as session:
        position = await session.scalar(
            select(Position).where(Position.portfolio_id == portfolio_id)
        )
        assert position is not None
        position.stop_price = Decimal(95)
        instrument = await session.get(Instrument, crypto)
        assert instrument is not None
        instrument.last_quote_price = Decimal(90)
        instrument.last_quote_at = context.clock.now()

    report = await MatchingPass(context).run()

    assert report.stops == 1
    assert report.filled == 1
    async with context.db.session() as session:
        refreshed = await session.get(Position, position.id)
        assert refreshed is not None
        assert refreshed.status == PositionStatus.CLOSED


async def test_an_inactive_portfolio_is_left_alone(
    client: AsyncClient,
    registered: dict[str, str],
    context: AppContext,
    portfolio_id: int,
    crypto: int,
) -> None:
    order_id = await buy(client, registered, portfolio_id, "10")
    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        assert portfolio is not None
        portfolio.is_active = False

    report = await MatchingPass(context).run()

    assert report.filled == 0
    assert await order_status(context, order_id) == OrderStatus.ACCEPTED


async def test_only_portfolios_with_something_to_work_are_visited(
    client: AsyncClient,
    registered: dict[str, str],
    context: AppContext,
    portfolio_id: int,
    crypto: int,
) -> None:
    assert (await MatchingPass(context).run()).portfolios == 0

    await buy(client, registered, portfolio_id, "10")

    assert (await MatchingPass(context).run()).portfolios == 1


def instrument_at(price: Decimal | None, at: datetime | None, asset_class: str) -> Instrument:
    return Instrument(
        symbol="AAA", asset_class=asset_class, last_quote_price=price, last_quote_at=at
    )


async def test_a_shut_exchange_is_not_matched(context: AppContext) -> None:
    """Filling against the last close while the venue is shut invents an unobtainable price."""
    pass_ = MatchingPass(context)
    equity = instrument_at(Decimal(100), CLOSED_MOMENT, "stock")

    assert pass_._quote(equity, CLOSED_MOMENT) == "market closed"


async def test_crypto_matches_around_the_clock(context: AppContext) -> None:
    pass_ = MatchingPass(context)
    coin = instrument_at(Decimal(100), CLOSED_MOMENT, "crypto")

    quote = pass_._quote(coin, CLOSED_MOMENT)

    assert not isinstance(quote, str)
    assert quote.price == Decimal(100)


async def test_a_stale_quote_is_not_matched(context: AppContext) -> None:
    pass_ = MatchingPass(context)
    coin = instrument_at(Decimal(100), OPEN_MOMENT - timedelta(hours=6), "crypto")

    assert "old" in str(pass_._quote(coin, OPEN_MOMENT))


async def test_an_unquoted_instrument_is_not_matched(context: AppContext) -> None:
    pass_ = MatchingPass(context)

    assert pass_._quote(instrument_at(None, None, "crypto"), OPEN_MOMENT) == "no quote on record"
    assert (
        pass_._quote(instrument_at(Decimal(0), OPEN_MOMENT, "crypto"), OPEN_MOMENT)
        == "quoted at or below zero"
    )


async def test_an_open_exchange_is_matched(context: AppContext) -> None:
    pass_ = MatchingPass(context)
    equity = instrument_at(Decimal(100), OPEN_MOMENT, "stock")

    quote = pass_._quote(equity, OPEN_MOMENT)

    assert not isinstance(quote, str)
    assert quote.symbol == "AAA"


async def test_the_match_endpoint_reports_what_it_did(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, crypto: int
) -> None:
    await buy(client, registered, portfolio_id, "10")

    response = await client.post(f"/api/portfolios/{portfolio_id}/match", headers=registered)

    assert response.status_code == 200
    assert response.json()["filled"] == 1
    assert response.json()["waiting"] == {}


async def test_the_match_endpoint_says_why_an_order_is_still_resting(
    client: AsyncClient, registered: dict[str, str], context: AppContext, portfolio_id: int
) -> None:
    """The answer to "why is my order still ACCEPTED?", which is what this whole path lacked."""
    async with context.db.session() as session:
        session.add(
            Instrument(
                symbol="ZZZ", asset_class="crypto", last_quote_price=None, last_quote_at=None
            )
        )
    await client.post(
        f"/api/portfolios/{portfolio_id}/orders",
        json={"symbol": "ZZZ", "side": "BUY", "qty": "1"},
        headers=registered,
    )

    response = await client.post(f"/api/portfolios/{portfolio_id}/match", headers=registered)

    assert response.json()["filled"] == 0
    assert response.json()["waiting"] == {"ZZZ": "no quote on record"}


async def test_another_user_cannot_match_a_portfolio(
    client: AsyncClient, registered: dict[str, str], portfolio_id: int, other_user: dict[str, str]
) -> None:
    response = await client.post(f"/api/portfolios/{portfolio_id}/match", headers=other_user)

    assert response.status_code == 404
