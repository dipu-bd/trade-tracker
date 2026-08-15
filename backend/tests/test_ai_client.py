from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from tradebot.ai.client import AIClient, Endpoint, ModelTier, Rung
from tradebot.ai.cost import estimate, estimate_tokens, lookup
from tradebot.ai.schema import deliberation_schema
from tradebot.core.clock import FrozenClock
from tradebot.providers.base import RateLimits

NOW = datetime(2024, 12, 3, 12, 0, tzinfo=UTC)
API_KEY = "sk-test-9f2a7c41d8e6"
PRIMARY = "https://primary.test/v1"
FALLBACK = "https://fallback.test/v1"


def endpoint(base: str = PRIMARY, model: str = "gpt-5-mini", **kwargs: object) -> Endpoint:
    return Endpoint(base_url=base, api_key=API_KEY, model=model, label=base, **kwargs)  # type: ignore[arg-type]


def reply(content: str = '{"verdicts": []}', model: str = "gpt-5-mini") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_tokens_details": {"cached_tokens": 400},
            },
        },
    )


async def call(client: AIClient, tier: ModelTier, schema: object = None):  # type: ignore[no-untyped-def]
    return await client.complete(tier, system="s", user="u", schema=schema or deliberation_schema())


@respx.mock
async def test_a_successful_call_reports_tokens_latency_and_cost() -> None:
    respx.post(f"{PRIMARY}/chat/completions").mock(return_value=reply())

    async with httpx.AsyncClient() as http:
        result = await call(AIClient(http, clock=FrozenClock(NOW)), ModelTier(endpoint()))

    assert result.ok
    assert result.rung is Rung.JSON_SCHEMA
    assert result.prompt_tokens == 1000
    assert result.cached_tokens == 400
    assert result.cost_usd > 0


@respx.mock
async def test_a_schema_rejection_steps_down_the_ladder_on_the_same_endpoint() -> None:
    """A 400 on json_schema is a capability gap, not an outage — degrade before failing over."""
    route = respx.post(f"{PRIMARY}/chat/completions")
    route.side_effect = [httpx.Response(400, json={"error": "unsupported"}), reply()]

    async with httpx.AsyncClient() as http:
        result = await call(AIClient(http, clock=FrozenClock(NOW)), ModelTier(endpoint()))

    assert result.ok
    assert result.rung is Rung.JSON_OBJECT
    assert result.endpoint == PRIMARY
    assert any("rejected json_schema" in attempt for attempt in result.attempts)


@respx.mock
async def test_the_bottom_rung_inlines_the_schema_in_the_prompt() -> None:
    route = respx.post(f"{PRIMARY}/chat/completions")
    route.side_effect = [httpx.Response(400), httpx.Response(400), reply()]

    async with httpx.AsyncClient() as http:
        result = await call(AIClient(http, clock=FrozenClock(NOW)), ModelTier(endpoint()))

    assert result.rung is Rung.PROMPT
    body = route.calls[-1].request.content.decode()
    assert "response_format" not in body
    assert "verdicts" in body


@respx.mock
async def test_an_exhausted_free_tier_fails_over_rather_than_skipping_the_cycle() -> None:
    """Rate limits are the binding constraint: degrade to the secondary, never drop the cycle."""
    respx.post(f"{PRIMARY}/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "1"})
    )
    respx.post(f"{FALLBACK}/chat/completions").mock(return_value=reply(model="gemini-2.0-flash"))

    tier = ModelTier(endpoint(), endpoint(FALLBACK, "gemini-2.0-flash"))
    async with httpx.AsyncClient() as http:
        client = AIClient(http, clock=FrozenClock(NOW))
        result = await client.complete(tier, system="s", user="u", schema=deliberation_schema())

    assert result.ok
    assert result.endpoint == FALLBACK
    assert any("rate limited" in attempt for attempt in result.attempts)


@respx.mock
async def test_a_429_penalises_the_primary_governor() -> None:
    respx.post(f"{PRIMARY}/chat/completions").mock(return_value=httpx.Response(429))
    respx.post(f"{FALLBACK}/chat/completions").mock(return_value=reply())

    primary = endpoint()
    tier = ModelTier(primary, endpoint(FALLBACK))
    async with httpx.AsyncClient() as http:
        client = AIClient(http, clock=FrozenClock(NOW))
        await client.complete(tier, system="s", user="u", schema=deliberation_schema())

        assert client.governor(primary).penalised


@respx.mock
async def test_a_transport_error_fails_over_without_retrying_the_same_endpoint() -> None:
    respx.post(f"{PRIMARY}/chat/completions").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{FALLBACK}/chat/completions").mock(return_value=reply())

    tier = ModelTier(endpoint(), endpoint(FALLBACK))
    async with httpx.AsyncClient() as http:
        result = await AIClient(http, clock=FrozenClock(NOW)).complete(
            tier, system="s", user="u", schema=deliberation_schema()
        )

    assert result.ok
    assert result.endpoint == FALLBACK
    assert len([a for a in result.attempts if PRIMARY in a]) == 1


@respx.mock
async def test_every_endpoint_failing_reports_an_error_rather_than_raising() -> None:
    """A dead model must degrade the cycle to rules-only, not crash it."""
    respx.post(f"{PRIMARY}/chat/completions").mock(return_value=httpx.Response(500))
    respx.post(f"{FALLBACK}/chat/completions").mock(return_value=httpx.Response(503))

    tier = ModelTier(endpoint(), endpoint(FALLBACK))
    async with httpx.AsyncClient() as http:
        result = await AIClient(http, clock=FrozenClock(NOW)).complete(
            tier, system="s", user="u", schema=deliberation_schema()
        )

    assert not result.ok
    assert result.error is not None
    assert len(result.attempts) == 2


@respx.mock
async def test_a_tier_without_a_fallback_uses_the_primary_alone() -> None:
    route = respx.post(f"{PRIMARY}/chat/completions").mock(return_value=reply())

    async with httpx.AsyncClient() as http:
        await call(AIClient(http, clock=FrozenClock(NOW)), ModelTier(endpoint()))

    assert route.call_count == 1


class VirtualClock:
    """Time only moves when something sleeps, so a rate-limit wait is instant but real."""

    def __init__(self, start: datetime) -> None:
        self.moment = start

    def now(self) -> datetime:
        return self.moment

    async def sleep(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


@respx.mock
async def test_the_governor_queues_a_third_call_on_a_two_per_minute_tier() -> None:
    """Parallel passes must wait rather than 429 — the plan's rate-limit test, in miniature."""
    route = respx.post(f"{PRIMARY}/chat/completions").mock(return_value=reply())
    clock = VirtualClock(NOW)

    limited = endpoint(limits=RateLimits(requests_per_minute=2))
    async with httpx.AsyncClient() as http:
        client = AIClient(http, clock=clock)
        client.governor(limited)._sleep = clock.sleep  # type: ignore[method-assign]

        for _ in range(3):
            await client.complete(
                ModelTier(limited), system="s", user="u", schema=deliberation_schema()
            )

    assert route.call_count == 3, "no call may be dropped"
    assert clock.moment > NOW, "the third call should have waited rather than been dropped"


@respx.mock
async def test_the_api_key_is_sent_as_a_bearer_token_and_not_in_the_body() -> None:
    route = respx.post(f"{PRIMARY}/chat/completions").mock(return_value=reply())

    async with httpx.AsyncClient() as http:
        await call(AIClient(http, clock=FrozenClock(NOW)), ModelTier(endpoint()))

    request = route.calls[0].request
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in request.content.decode()


def test_an_unknown_model_prices_at_zero_rather_than_guessing() -> None:
    assert lookup("some-unreleased-model") is None
    assert estimate("some-unreleased-model", 1000, 1000) == 0.0


def test_a_vendor_prefixed_model_resolves_to_its_price() -> None:
    assert lookup("google/gemini-2.5-flash") == lookup("gemini-2.5-flash")


def test_a_free_tier_model_costs_nothing() -> None:
    assert estimate("meta-llama/llama-3.3-70b:free", 100_000, 100_000) == 0.0
    assert estimate("ollama/qwen-2.5-72b", 100_000, 100_000) == 0.0


def test_cached_prompt_tokens_are_billed_at_the_cached_rate() -> None:
    full = estimate("gpt-5", 10_000, 0)
    cached = estimate("gpt-5", 10_000, 0, cached=10_000)

    assert cached < full


@pytest.mark.parametrize("text", ["", "a", "a" * 5000])
def test_the_token_estimate_is_never_zero(text: str) -> None:
    assert estimate_tokens(text) >= 1
