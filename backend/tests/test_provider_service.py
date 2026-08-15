import pytest
from httpx import AsyncClient

from tradebot.context import AppContext
from tradebot.providers.base import AssetClass, Capability
from tradebot.services.providers import ProviderService

ALPACA_ID = "AKTESTKEYID000000000000000"
ALPACA_SECRET = "SKTESTSECRET00000000000000000000000000000000"
FMP_KEY = "FMPTESTKEY0000000000000000000000"

ENV = {
    "TRADEBOT_ALPACA_API_KEY_ID": ALPACA_ID,
    "TRADEBOT_ALPACA_SECRET_KEY": ALPACA_SECRET,
    "TRADEBOT_FMP_API_KEY": FMP_KEY,
}


@pytest.fixture
async def user_id(client: AsyncClient) -> int:
    await client.post(
        "/api/auth/register",
        json={
            "email": "seed@example.com",
            "password": "correct-horse-battery-staple",
            "display_name": "Seeder",
        },
    )
    return 1


async def test_seeding_writes_every_complete_provider(context: AppContext, user_id: int) -> None:
    service = ProviderService(context.vault)

    async with context.db.session() as session:
        seeded = await service.seed_from_env(session, user_id, ENV)

    assert sorted(seeded) == ["alpaca", "fmp"]


async def test_a_partially_supplied_provider_is_skipped(context: AppContext, user_id: int) -> None:
    """Alpaca needs both a key id and a secret; half of it is worse than none."""
    service = ProviderService(context.vault)

    async with context.db.session() as session:
        seeded = await service.seed_from_env(
            session, user_id, {"TRADEBOT_ALPACA_API_KEY_ID": ALPACA_ID}
        )

    assert "alpaca" not in seeded


async def test_seeded_values_round_trip_out_of_the_vault(context: AppContext, user_id: int) -> None:
    service = ProviderService(context.vault)

    async with context.db.session() as session:
        await service.seed_from_env(session, user_id, ENV)
        stored = await service.credentials_for(session, user_id)

    assert stored["fmp"]["api_key"] == FMP_KEY
    assert stored["alpaca"]["secret_key"] == ALPACA_SECRET


async def test_the_summary_masks_every_seeded_value(context: AppContext, user_id: int) -> None:
    """Seeding is a new write path; it must not become a way for plaintext to escape."""
    service = ProviderService(context.vault)

    async with context.db.session() as session:
        await service.seed_from_env(session, user_id, ENV)
        summary = await service.masked_summary(session, user_id)

    rendered = repr(summary)
    assert ALPACA_SECRET not in rendered
    assert FMP_KEY not in rendered
    assert ALPACA_ID not in rendered

    fmp = next(row for row in summary if row["provider"] == "fmp")
    assert fmp["fields"] == [{"field": "api_key", "masked": "…0000"}]


async def test_seeded_keys_are_not_returned_by_the_credentials_api(
    client: AsyncClient, context: AppContext, user_id: int
) -> None:
    service = ProviderService(context.vault)
    async with context.db.session() as session:
        await service.seed_from_env(session, user_id, ENV)

    login = await client.post(
        "/api/auth/login",
        json={"email": "seed@example.com", "password": "correct-horse-battery-staple"},
    )
    token = login.json()["access_token"]
    response = await client.get("/api/credentials", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert FMP_KEY not in response.text
    assert ALPACA_SECRET not in response.text


async def test_the_router_omits_providers_without_credentials(
    context: AppContext, user_id: int
) -> None:
    service = ProviderService(context.vault)

    async with context.db.session() as session:
        router = await service.build_router(session, user_id)

    available = {p.key for p in router.providers if p.available}
    assert "binance" in available
    assert "cryptocom" in available
    assert "alpaca" not in available


async def test_seeding_makes_a_provider_routable(context: AppContext, user_id: int) -> None:
    service = ProviderService(context.vault)

    async with context.db.session() as session:
        await service.seed_from_env(session, user_id, ENV)
        router = await service.build_router(session, user_id)

    chain = [p.key for p in router.candidates(Capability.QUOTES, AssetClass.STOCK)]
    assert chain == ["alpaca", "fmp"]


async def test_keyless_providers_are_always_configured(context: AppContext, user_id: int) -> None:
    service = ProviderService(context.vault)

    async with context.db.session() as session:
        summary = await service.masked_summary(session, user_id)

    binance = next(row for row in summary if row["provider"] == "binance")
    assert binance["keyless"] is True
    assert binance["configured"] is True
