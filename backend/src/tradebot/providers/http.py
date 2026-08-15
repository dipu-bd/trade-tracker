from typing import Any

import httpx

from tradebot.providers.base import (
    ProviderError,
    ProviderRateLimitedError,
)

DEFAULT_TIMEOUT = 20.0
PLAN_DENIED = frozenset({401, 402, 403})


class HttpProviderMixin:
    """Shared httpx plumbing: one client per provider, uniform error translation.

    A plan-denied path is remembered so a tier that lacks an endpoint stops being retried on
    every cycle.
    """

    base_url: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: httpx.AsyncClient | None = None
        self._denied: set[str] = set()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "tradebot/0.1"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def denied(self, path: str) -> bool:
        return path in self._denied

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        key = getattr(self, "key", "http")
        if path in self._denied:
            raise ProviderError(key, f"endpoint not available on this plan: {path}")

        try:
            response = await self.client.get(path, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError(key, f"timeout calling {path}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(key, f"transport error calling {path}: {exc}") from exc

        if response.status_code == 429:
            raise ProviderRateLimitedError(key, retry_after=_retry_after(response))
        if response.status_code in PLAN_DENIED:
            self._denied.add(path)
            raise ProviderError(
                key, f"denied ({response.status_code}) for {path}", status=response.status_code
            )
        if response.status_code >= 400:
            raise ProviderError(
                key, f"{response.status_code} from {path}", status=response.status_code
            )

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(key, f"malformed JSON from {path}") from exc


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
