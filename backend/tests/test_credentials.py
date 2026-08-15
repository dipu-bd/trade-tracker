from httpx import AsyncClient

SECRET = "sk-live-9f8e7d6c5b4a32100000"
BODY = {"provider_key": "alpaca", "field": "api_key", "secret": SECRET}


async def test_store_returns_a_masked_value_never_the_secret(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.post("/api/credentials", json=BODY, headers=registered)
    assert response.status_code == 201

    body = response.json()
    assert body["masked"] == "…0000"
    assert SECRET not in response.text
    assert "secret" not in body
    assert "ciphertext" not in body


async def test_listing_never_leaks_the_secret(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    await client.post("/api/credentials", json=BODY, headers=registered)
    response = await client.get("/api/credentials", headers=registered)
    assert response.status_code == 200
    assert SECRET not in response.text
    assert len(response.json()) == 1


async def test_restoring_the_same_slot_replaces_rather_than_duplicates(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    first = await client.post("/api/credentials", json=BODY, headers=registered)
    second = await client.post(
        "/api/credentials", json={**BODY, "secret": "sk-live-replacement-9999"}, headers=registered
    )

    assert first.json()["id"] == second.json()["id"]
    assert second.json()["masked"] == "…9999"

    listing = await client.get("/api/credentials", headers=registered)
    assert len(listing.json()) == 1


async def test_filter_by_provider(client: AsyncClient, registered: dict[str, str]) -> None:
    await client.post("/api/credentials", json=BODY, headers=registered)
    await client.post(
        "/api/credentials",
        json={"provider_key": "fmp", "field": "api_key", "secret": "fmp-key-000"},
        headers=registered,
    )

    response = await client.get("/api/credentials?provider_key=fmp", headers=registered)
    assert [row["provider_key"] for row in response.json()] == ["fmp"]


async def test_the_stored_secret_decrypts_back(
    client: AsyncClient, registered: dict[str, str], context
) -> None:  # type: ignore[no-untyped-def]
    created = await client.post("/api/credentials", json=BODY, headers=registered)
    credential_id = created.json()["id"]

    async with context.db.session() as session:
        revealed = await context.vault.reveal(session, credential_id=credential_id, user_id=1)
    assert revealed == SECRET


async def test_delete_removes_the_credential(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    created = await client.post("/api/credentials", json=BODY, headers=registered)
    credential_id = created.json()["id"]

    assert (
        await client.delete(f"/api/credentials/{credential_id}", headers=registered)
    ).status_code == 204
    assert (await client.get("/api/credentials", headers=registered)).json() == []


async def test_deleting_a_missing_credential_is_a_404(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    response = await client.delete("/api/credentials/9999", headers=registered)
    assert response.status_code == 404


async def test_credentials_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/credentials")).status_code == 401
    assert (await client.post("/api/credentials", json=BODY)).status_code == 401
