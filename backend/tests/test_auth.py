import pytest
from httpx import AsyncClient

from tradebot.api.deps import REFRESH_COOKIE

REGISTER = {
    "email": "alice@example.com",
    "password": "correct-horse-battery-staple",
    "display_name": "Alice",
}


async def test_register_returns_user_without_password(client: AsyncClient) -> None:
    response = await client.post("/api/auth/register", json=REGISTER)
    assert response.status_code == 201

    body = response.json()
    assert body["email"] == "alice@example.com"
    assert "password" not in body
    assert "password_hash" not in body


async def test_first_account_becomes_admin(client: AsyncClient) -> None:
    first = await client.post("/api/auth/register", json=REGISTER)
    second = await client.post("/api/auth/register", json={**REGISTER, "email": "bob@example.com"})
    assert first.json()["is_admin"] is True
    assert second.json()["is_admin"] is False


async def test_duplicate_email_conflicts(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    response = await client.post("/api/auth/register", json=REGISTER)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_email_is_normalized_to_lowercase(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json={**REGISTER, "email": "MiXeD@Example.COM"})
    response = await client.post(
        "/api/auth/login", json={"email": "mixed@example.com", "password": REGISTER["password"]}
    )
    assert response.status_code == 200


async def test_short_password_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/auth/register", json={**REGISTER, "password": "short"})
    assert response.status_code == 422


async def test_login_issues_access_token_and_refresh_cookie(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    response = await client.post(
        "/api/auth/login",
        json={"email": REGISTER["email"], "password": REGISTER["password"]},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert REFRESH_COOKIE in response.cookies


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "alice@example.com", "password": "wrong-password-entirely"},
        {"email": "nobody@example.com", "password": "correct-horse-battery-staple"},
    ],
)
async def test_bad_credentials_are_indistinguishable(
    client: AsyncClient, payload: dict[str, str]
) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    response = await client.post("/api/auth/login", json=payload)
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "invalid email or password"


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_returns_the_authenticated_user(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.get("/api/auth/me", headers=registered)
    assert response.status_code == 200
    assert response.json()["email"] == "trader@example.com"


async def test_garbage_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert response.status_code == 401


async def test_refresh_rotates_the_cookie_and_mints_a_new_token(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    login = await client.post(
        "/api/auth/login", json={"email": REGISTER["email"], "password": REGISTER["password"]}
    )
    original = login.cookies[REFRESH_COOKIE]

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.cookies[REFRESH_COOKIE] != original


async def test_a_rotated_refresh_token_cannot_be_reused(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    login = await client.post(
        "/api/auth/login", json={"email": REGISTER["email"], "password": REGISTER["password"]}
    )
    stale = login.cookies[REFRESH_COOKIE]

    await client.post("/api/auth/refresh")
    replay = await client.post("/api/auth/refresh", cookies={REFRESH_COOKIE: stale})
    assert replay.status_code == 401


async def test_refresh_without_a_cookie_is_rejected(client: AsyncClient) -> None:
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_logout_revokes_the_session(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    await client.post(
        "/api/auth/login", json={"email": REGISTER["email"], "password": REGISTER["password"]}
    )

    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.post("/api/auth/refresh")).status_code == 401
