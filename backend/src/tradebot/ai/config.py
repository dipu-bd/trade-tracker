from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.db.models import ModelProfile, Portfolio

TIERS = ("quick", "quick_fallback", "deep", "deep_fallback")


@dataclass(frozen=True, slots=True)
class ModelConfig:
    endpoints: dict[str, Any]
    quality: str
    deliberation: str
    profile_id: int | None = None
    profile_name: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.endpoints.get("deep"))


async def resolve(session: AsyncSession, portfolio: Portfolio) -> ModelConfig:
    """The model setup a portfolio actually runs with.

    A linked profile wins over the portfolio's own `models` blob, which stays as the fallback so
    portfolios configured before profiles existed keep working untouched.
    """
    if portfolio.model_profile_id is not None:
        profile = await session.get(ModelProfile, portfolio.model_profile_id)
        if profile is not None:
            return ModelConfig(
                endpoints=dict(profile.endpoints or {}),
                quality=profile.quality,
                deliberation=profile.deliberation,
                profile_id=profile.id,
                profile_name=profile.name,
            )

    return ModelConfig(
        endpoints=dict(portfolio.models or {}),
        quality=portfolio.quality,
        deliberation=portfolio.deliberation,
    )
