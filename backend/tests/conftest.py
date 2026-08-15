from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from tradebot.context import AppContext
from tradebot.core.settings import Settings
from tradebot.db.models import Base
from tradebot.main import create_app

TEST_SECRET = "unit-test-secret-key-long-enough-000000"


@pytest.fixture
def settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        secret_key=TEST_SECRET,
        cookie_secure=False,
        log_json=False,
        scheduler_enabled=False,
    )


@pytest.fixture
async def context(settings: Settings) -> AsyncIterator[AppContext]:
    ctx = AppContext.build(settings)
    async with ctx.db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield ctx
    await ctx.aclose()


@pytest.fixture
async def client(settings: Settings, context: AppContext) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, context=context)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def registered(client: AsyncClient) -> dict[str, str]:
    payload = {
        "email": "trader@example.com",
        "password": "correct-horse-battery-staple",
        "display_name": "Trader",
    }
    await client.post("/api/auth/register", json=payload)
    response = await client.post(
        "/api/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
