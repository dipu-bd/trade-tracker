from fastapi import APIRouter, status

from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.schemas.credential import CredentialCreate, CredentialOut

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.post("", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
async def store(
    body: CredentialCreate, user: CurrentUser, context: Context, session: DbSession
) -> CredentialOut:
    """Store an API key, encrypted at rest. Re-posting the same slot replaces it."""
    record = await context.vault.store(
        session,
        user_id=user.id,
        provider_key=body.provider_key,
        field=body.field,
        secret=body.secret,
        label=body.label,
    )
    await context.events.record(
        session,
        domain="auth",
        kind="credential_stored",
        user_id=user.id,
        message=f"{body.provider_key}.{body.field}",
    )
    return CredentialOut.model_validate(record)


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    user: CurrentUser,
    context: Context,
    session: DbSession,
    provider_key: str | None = None,
) -> list[CredentialOut]:
    """List stored credentials with masked values."""
    records = await context.vault.list_for_user(session, user_id=user.id, provider_key=provider_key)
    return [CredentialOut.model_validate(record) for record in records]


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: int, user: CurrentUser, context: Context, session: DbSession
) -> None:
    """Permanently delete a stored credential."""
    await context.vault.delete(session, credential_id=credential_id, user_id=user.id)
    await context.events.record(session, domain="auth", kind="credential_deleted", user_id=user.id)
