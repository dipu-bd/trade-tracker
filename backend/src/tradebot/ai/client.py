import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from tradebot.ai.cost import estimate, estimate_tokens
from tradebot.core.clock import Clock, LiveClock
from tradebot.core.logging import get_logger
from tradebot.providers.base import RateLimits
from tradebot.providers.governor import RateLimitGovernor

_log = get_logger(__name__)

DEFAULT_TIMEOUT = 120.0


class Rung(StrEnum):
    """The structured-output degradation ladder, best first."""

    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT = "prompt"


class Tier(StrEnum):
    QUICK = "quick"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One OpenAI-compatible target. base_url + api_key + model is the whole contract."""

    base_url: str
    api_key: str
    model: str
    label: str = ""
    limits: RateLimits = field(default_factory=RateLimits)

    @property
    def name(self) -> str:
        return self.label or f"{self.model}@{self.base_url}"


@dataclass(frozen=True, slots=True)
class ModelTier:
    primary: Endpoint
    fallback: Endpoint | None = None

    @property
    def endpoints(self) -> list[Endpoint]:
        return [self.primary] if self.fallback is None else [self.primary, self.fallback]


@dataclass
class Completion:
    text: str
    model: str
    endpoint: str
    rung: Rung
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    attempts: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text)


class ModelUnavailableError(RuntimeError):
    pass


class AIClient:
    """One client for every OpenAI-compatible provider.

    Rate limits are the binding constraint, not spend: an exhausted free tier must degrade to a
    slower endpoint or wait, never skip a decision cycle, and cost is computed for display only.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        *,
        clock: Clock | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._http = http
        self._owned = http is None
        self._clock = clock or LiveClock()
        self._timeout = timeout
        self._governors: dict[str, RateLimitGovernor] = {}

    async def aclose(self) -> None:
        if self._owned and self._http is not None:
            await self._http.aclose()

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    def governor(self, endpoint: Endpoint) -> RateLimitGovernor:
        if endpoint.name not in self._governors:
            self._governors[endpoint.name] = RateLimitGovernor(endpoint.limits, clock=self._clock)
        return self._governors[endpoint.name]

    async def complete(
        self,
        tier: ModelTier,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "response",
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> Completion:
        """Try each endpoint, and within each try each rung of the ladder.

        A refusal to honour `json_schema` is a capability gap rather than an outage, so it steps
        down a rung on the same endpoint before giving up on the endpoint entirely.
        """
        attempts: list[str] = []
        last_error = "no endpoint configured"

        for endpoint in tier.endpoints:
            for rung in _ladder(schema):
                try:
                    completion = await self._call(
                        endpoint,
                        system=system,
                        user=user,
                        schema=schema,
                        schema_name=schema_name,
                        rung=rung,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                except _RetryableError as failure:
                    attempts.append(f"{endpoint.name}/{rung.value}: {failure}")
                    last_error = str(failure)
                    if not failure.same_endpoint:
                        break
                    continue

                completion.attempts = attempts
                return completion

        return Completion(
            text="",
            model=tier.primary.model,
            endpoint=tier.primary.name,
            rung=Rung.PROMPT,
            attempts=attempts,
            error=last_error,
        )

    async def _call(
        self,
        endpoint: Endpoint,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        schema_name: str,
        rung: Rung,
        temperature: float,
        max_tokens: int,
    ) -> Completion:
        prompt = _prompt_for(system, user, schema, rung)
        governor = self.governor(endpoint)
        await governor.acquire(estimated_tokens=estimate_tokens(prompt[0] + prompt[1]))

        body: dict[str, Any] = {
            "model": endpoint.model,
            "messages": [
                {"role": "system", "content": prompt[0]},
                {"role": "user", "content": prompt[1]},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if rung is Rung.JSON_SCHEMA and schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        elif rung is Rung.JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            response = await self._client().post(
                f"{endpoint.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {endpoint.api_key}"},
                json=body,
            )
        except httpx.HTTPError as failure:
            governor.release()
            raise _RetryableError(
                f"transport: {type(failure).__name__}", same_endpoint=False
            ) from failure

        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            if response.status_code == 429:
                governor.record_rate_limited(retry_after=_retry_after(response))
                raise _RetryableError("rate limited", same_endpoint=False)

            if response.status_code == 400 and rung is not Rung.PROMPT:
                raise _RetryableError(f"rejected {rung.value}", same_endpoint=True)

            if response.status_code >= 400:
                raise _RetryableError(f"http {response.status_code}", same_endpoint=False)

            governor.record_success()
            payload = response.json()
        finally:
            governor.release()

        return _completion_from(payload, endpoint, rung, latency_ms)


class _RetryableError(RuntimeError):
    def __init__(self, message: str, *, same_endpoint: bool) -> None:
        super().__init__(message)
        self.same_endpoint = same_endpoint


def _ladder(schema: dict[str, Any] | None) -> list[Rung]:
    if schema is None:
        return [Rung.PROMPT]
    return [Rung.JSON_SCHEMA, Rung.JSON_OBJECT, Rung.PROMPT]


def _prompt_for(
    system: str, user: str, schema: dict[str, Any] | None, rung: Rung
) -> tuple[str, str]:
    """Static content first, volatile last, so provider prompt caching actually hits."""
    if rung is Rung.PROMPT and schema is not None:
        system = (
            f"{system}\n\nReply with JSON matching this schema and nothing else:"
            f"\n{json.dumps(schema)}"
        )
    elif rung is Rung.JSON_OBJECT:
        system = f"{system}\n\nReply with a single JSON object and nothing else."
    return system, user


def _completion_from(
    payload: dict[str, Any], endpoint: Endpoint, rung: Rung, latency_ms: int
) -> Completion:
    choices = payload.get("choices") or []
    text = ""
    if choices:
        text = (choices[0].get("message") or {}).get("content") or ""

    usage = payload.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    model = payload.get("model") or endpoint.model

    return Completion(
        text=text,
        model=model,
        endpoint=endpoint.name,
        rung=rung,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached,
        latency_ms=latency_ms,
        cost_usd=estimate(model, prompt_tokens, completion_tokens, cached),
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
