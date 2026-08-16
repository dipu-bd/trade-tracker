from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.ai import guardrails
from tradebot.ai.analysts import AnalystCache, AnalystKind, AnalystPool
from tradebot.ai.client import AIClient, Endpoint, ModelTier
from tradebot.ai.config import ModelConfig, resolve
from tradebot.ai.deliberation import (
    STRATEGIES,
    DeliberationResult,
    DeliberationStrategy,
    FirmDebate,
    MultiRoundDebate,
    SingleCall,
)
from tradebot.ai.guardrails import GuardrailConfig, GuardrailResult
from tradebot.ai.reflection import recall
from tradebot.analytics.features import Features
from tradebot.backtest.ic import SignalQuality, apply_deweighting, measure_from_decisions
from tradebot.core.clock import Clock
from tradebot.db.models import AICall, Portfolio
from tradebot.engine.strategy import Decision, PortfolioState, StrategyConfig
from tradebot.providers.base import Capability, RateLimits


@dataclass(frozen=True, slots=True)
class QualityProfile:
    """How much depth a portfolio buys. Raises tier, analyst set and debate rounds together."""

    kinds: tuple[AnalystKind, ...]
    rounds: int
    strategy: str


QUALITY_PROFILES = {
    "economy": QualityProfile((AnalystKind.TECHNICAL,), 1, "single_call"),
    "balanced": QualityProfile((AnalystKind.TECHNICAL, AnalystKind.NEWS), 1, "firm_debate"),
    "thorough": QualityProfile(tuple(AnalystKind), 2, "multi_round_debate"),
}


@dataclass
class AIOutcome:
    """Everything the audit row needs, whether or not the model was reachable."""

    confidence: dict[str, float] = field(default_factory=dict)
    deliberation: DeliberationResult | None = None
    guardrail: GuardrailResult | None = None
    lessons: list[str] = field(default_factory=list)
    quality: SignalQuality | None = None
    enabled: bool = False
    reason: str = ""

    @property
    def used(self) -> bool:
        return self.enabled and self.deliberation is not None and self.deliberation.ok

    def as_detail(self) -> dict[str, object]:
        detail: dict[str, object] = {"enabled": self.enabled, "used": self.used}
        if self.reason:
            detail["reason"] = self.reason
        if self.deliberation is not None:
            detail["strategy"] = self.deliberation.strategy
            detail["rounds"] = self.deliberation.rounds
            brief = self.deliberation.brief
            detail["brief_hash"] = brief.digest if brief else ""
            detail["cost_usd"] = round(self.deliberation.cost_usd, 6)
            detail["parse_error"] = self.deliberation.parse_error
            if self.deliberation.analysts is not None:
                detail["analysts_skipped"] = self.deliberation.analysts.skipped
                detail["analyst_cache_hits"] = self.deliberation.analysts.cache_hits
        if self.guardrail is not None:
            detail["guardrail_diff"] = self.guardrail.diff
            detail["accepted"] = self.guardrail.accepted
            detail["confidence"] = {k: round(v, 4) for k, v in self.confidence.items()}
            detail["confidence_before_deweighting"] = {
                k: round(v, 4) for k, v in self.guardrail.confidence.items()
            }
        if self.quality is not None:
            detail["signal_quality"] = self.quality.as_dict()
            detail["deweighting"] = self.quality.verdict()
        return detail


def endpoint_from(raw: dict[str, object] | None, api_key: str) -> Endpoint | None:
    if not raw or not raw.get("model") or not raw.get("base_url"):
        return None
    raw_limits = raw.get("limits")
    limits: dict[str, int] = raw_limits if isinstance(raw_limits, dict) else {}
    return Endpoint(
        base_url=str(raw["base_url"]),
        api_key=api_key,
        model=str(raw["model"]),
        label=str(raw.get("label") or raw["model"]),
        limits=RateLimits(
            requests_per_minute=int(limits.get("rpm", 0)),
            requests_per_day=int(limits.get("rpd", 0)),
            tokens_per_minute=int(limits.get("tpm", 0)),
            max_concurrency=int(limits.get("concurrency", 4)),
        ),
    )


def tiers_for(
    source: "Portfolio | ModelConfig", keys: dict[str, str]
) -> tuple[ModelTier | None, ModelTier | None]:
    """Build the quick and deep tiers from stored config plus vault-resolved keys."""
    config = dict(source.endpoints if isinstance(source, ModelConfig) else source.models or {})

    def build(name: str) -> ModelTier | None:
        raw = config.get(name)
        if not isinstance(raw, dict):
            return None
        primary = endpoint_from(raw, keys.get(str(raw.get("credential") or name), ""))
        if primary is None:
            return None
        fallback_raw = config.get(f"{name}_fallback")
        fallback = (
            endpoint_from(fallback_raw, keys.get(str(fallback_raw.get("credential") or name), ""))
            if isinstance(fallback_raw, dict)
            else None
        )
        return ModelTier(primary, fallback)

    return build("quick"), build("deep")


class AIPipeline:
    """Runs deliberation and clamps its output, or explains why it did not.

    A model that is unreachable, unconfigured or unparseable must leave the cycle running on the
    rules alone. The AI is an overlay on a strategy that already works without it, never a
    dependency of it.
    """

    def __init__(self, client: AIClient, clock: Clock, cache: AnalystCache | None = None) -> None:
        self._client = client
        self._clock = clock
        self._cache = cache or AnalystCache()

    def strategy_for(
        self, models: ModelConfig, quick: ModelTier | None, deep: ModelTier
    ) -> DeliberationStrategy:
        profile = QUALITY_PROFILES.get(models.quality, QUALITY_PROFILES["balanced"])
        name = models.deliberation or profile.strategy
        kind = STRATEGIES.get(name, STRATEGIES["firm_debate"])

        if kind is SingleCall or quick is None:
            return SingleCall(self._client, deep)

        pool = AnalystPool(self._client, quick, cache=self._cache)
        kinds = list(profile.kinds)
        if kind is MultiRoundDebate:
            return MultiRoundDebate(self._client, deep, pool, kinds, rounds=profile.rounds)
        return FirmDebate(self._client, deep, pool, kinds)

    async def run(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        as_of: date,
        decision: Decision,
        state: PortfolioState,
        features: dict[str, Features],
        config: StrategyConfig,
        keys: dict[str, str],
        capabilities: frozenset[Capability] = frozenset(),
        correlation_id: str = "",
        run_id: int | None = None,
    ) -> AIOutcome:
        if not portfolio.ai_enabled:
            return AIOutcome(reason="ai disabled for this portfolio")
        if not decision.entries:
            return AIOutcome(enabled=True, reason="no candidates to judge")

        models = await resolve(session, portfolio)
        quick, deep = tiers_for(models, keys)
        if deep is None:
            return AIOutcome(enabled=True, reason="no deep model configured")

        lessons = await recall(session, portfolio.id, [e.symbol for e in decision.entries])
        strategy = self.strategy_for(models, quick, deep)
        result = await strategy.run(as_of, decision, state, features, lessons, capabilities)

        await self._audit(session, portfolio, result, correlation_id, run_id)

        if not result.ok:
            return AIOutcome(
                deliberation=result,
                lessons=lessons,
                enabled=True,
                reason=result.parse_error or "model unavailable",
            )

        quality = await measure_from_decisions(session, portfolio.id)

        clamped = guardrails.apply(
            result.verdicts,
            decision,
            state,
            as_of,
            GuardrailConfig(
                max_position_weight=config.sizing.max_position_weight,
                max_positions=config.sizing.max_positions,
            ),
            sizing=config.sizing,
            turnover=config.turnover,
        )

        # The self-honesty loop: influence the model has not earned out of sample is removed
        # before sizing sees it, so a failing AI degrades the system toward rules-only.
        return AIOutcome(
            confidence=apply_deweighting(clamped.confidence, quality),
            deliberation=result,
            guardrail=clamped,
            lessons=lessons,
            quality=quality,
            enabled=True,
        )

    async def _audit(
        self,
        session: AsyncSession,
        portfolio: Portfolio,
        result: DeliberationResult,
        correlation_id: str,
        run_id: int | None,
    ) -> None:
        now = self._clock.now()
        brief_hash = result.brief.digest if result.brief else ""

        for completion in result.completions:
            session.add(
                AICall(
                    decision_run_id=run_id,
                    portfolio_id=portfolio.id,
                    correlation_id=correlation_id,
                    stage="deliberation",
                    model=completion.model,
                    endpoint=completion.endpoint,
                    rung=completion.rung.value,
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                    cached_tokens=completion.cached_tokens,
                    latency_ms=completion.latency_ms,
                    cost_usd=Decimal(str(round(completion.cost_usd, 8))),
                    brief_hash=brief_hash,
                    system_prompt=result.brief.system if result.brief else "",
                    user_prompt=result.brief.text if result.brief else "",
                    response=completion.text,
                    attempts={"tried": completion.attempts},
                    error=completion.error,
                    created_at=now,
                )
            )

        if result.analysts is not None:
            for completion in result.analysts.completions:
                session.add(
                    AICall(
                        decision_run_id=run_id,
                        portfolio_id=portfolio.id,
                        correlation_id=correlation_id,
                        stage="analyst",
                        model=completion.model,
                        endpoint=completion.endpoint,
                        rung=completion.rung.value,
                        prompt_tokens=completion.prompt_tokens,
                        completion_tokens=completion.completion_tokens,
                        cached_tokens=completion.cached_tokens,
                        latency_ms=completion.latency_ms,
                        cost_usd=Decimal(str(round(completion.cost_usd, 8))),
                        response=completion.text,
                        error=completion.error,
                        created_at=now,
                    )
                )

        await session.flush()
