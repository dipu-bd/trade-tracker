from fastapi import APIRouter, status
from sqlalchemy import func, select

from tradebot.ai.config import TIERS
from tradebot.ai.deliberation import STRATEGIES
from tradebot.ai.pipeline import QUALITY_PROFILES
from tradebot.api.deps import Context, CurrentUser, DbSession
from tradebot.core.errors import ConflictError, NotFoundError, ValidationError
from tradebot.db.models import ModelProfile, Portfolio
from tradebot.schemas.ai import ModelProfileIn, ModelProfileOut

router = APIRouter(prefix="/model-profiles", tags=["ai"])


async def _load(session: DbSession, profile_id: int, user_id: int) -> ModelProfile:
    profile = await session.get(ModelProfile, profile_id)
    if profile is None or profile.user_id != user_id:
        raise NotFoundError(f"model profile {profile_id} not found")
    return profile


def _validate(body: ModelProfileIn) -> None:
    if body.quality not in QUALITY_PROFILES:
        raise ValidationError(f"unknown quality: {body.quality}")
    if body.deliberation not in STRATEGIES:
        raise ValidationError(f"unknown deliberation strategy: {body.deliberation}")


async def _out(session: DbSession, profile: ModelProfile, available: set[str]) -> ModelProfileOut:
    endpoints = dict(profile.endpoints or {})
    wanted = {
        str(endpoint.get("credential"))
        for endpoint in endpoints.values()
        if isinstance(endpoint, dict) and endpoint.get("credential")
    }
    used_by = await session.scalar(
        select(func.count(Portfolio.id)).where(Portfolio.model_profile_id == profile.id)
    )
    return ModelProfileOut(
        id=profile.id,
        name=profile.name,
        quality=profile.quality,
        deliberation=profile.deliberation,
        missing_credentials=sorted(wanted - available),
        used_by=int(used_by or 0),
        **{name: endpoints.get(name) for name in TIERS},
    )


@router.get("", response_model=list[ModelProfileOut])
async def list_profiles(
    user: CurrentUser, context: Context, session: DbSession
) -> list[ModelProfileOut]:
    """Every saved model setup, with how many portfolios use each."""
    available = set(await context.providers.llm_keys(session, user.id))
    rows = await session.scalars(
        select(ModelProfile).where(ModelProfile.user_id == user.id).order_by(ModelProfile.name)
    )
    return [await _out(session, row, available) for row in rows]


@router.post("", response_model=ModelProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ModelProfileIn, user: CurrentUser, context: Context, session: DbSession
) -> ModelProfileOut:
    """Save a named model setup that portfolios can then pick."""
    _validate(body)
    existing = await session.scalar(
        select(ModelProfile).where(ModelProfile.user_id == user.id, ModelProfile.name == body.name)
    )
    if existing is not None:
        raise ConflictError(f"a model profile named {body.name!r} already exists")

    profile = ModelProfile(
        user_id=user.id,
        name=body.name,
        endpoints={
            name: endpoint.model_dump()
            for name in TIERS
            if (endpoint := getattr(body, name)) is not None
        },
        quality=body.quality,
        deliberation=body.deliberation,
    )
    session.add(profile)
    await session.flush()

    available = set(await context.providers.llm_keys(session, user.id))
    return await _out(session, profile, available)


@router.put("/{profile_id}", response_model=ModelProfileOut)
async def update_profile(
    profile_id: int,
    body: ModelProfileIn,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> ModelProfileOut:
    """Replace a profile. Every portfolio pointing at it changes with it."""
    _validate(body)
    profile = await _load(session, profile_id, user.id)

    profile.name = body.name
    profile.endpoints = {
        name: endpoint.model_dump()
        for name in TIERS
        if (endpoint := getattr(body, name)) is not None
    }
    profile.quality = body.quality
    profile.deliberation = body.deliberation
    await session.flush()

    available = set(await context.providers.llm_keys(session, user.id))
    return await _out(session, profile, available)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: int, user: CurrentUser, session: DbSession) -> None:
    """Portfolios using it fall back to their own stored config rather than breaking."""
    profile = await _load(session, profile_id, user.id)
    await session.delete(profile)
    await session.flush()
