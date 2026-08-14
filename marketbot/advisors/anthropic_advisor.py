"""Advisor backed by the official Anthropic SDK."""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .base import (
    SYSTEM_PROMPT,
    VERDICT_SCHEMA,
    AdvisorResult,
    LLMAdvisor,
    parse_verdicts,
)

_log = logging.getLogger(__name__)

MAX_TOKENS = 8000
FALLBACK_BETA = 'server-side-fallback-2026-07-01'


class AnthropicAdvisor(LLMAdvisor):
    def __init__(self, model: str, api_key: str, timeout: float = 60.0):
        super().__init__(model, api_key, timeout)
        self._client = None

    @property
    def provider(self) -> str:
        return 'anthropic'

    def _get_client(self):
        if self._client is None:
            import anthropic  # imported lazily so the package stays optional
            self._client = anthropic.Anthropic(
                api_key=self.api_key or None,
                timeout=self.timeout,
            )
        return self._client

    def review(self, brief: Dict[str, Any]) -> AdvisorResult:
        result = AdvisorResult(provider=self.provider, model=self.model)
        started = time.monotonic()

        try:
            client = self._get_client()
        except Exception as e:  # noqa: BLE001 — missing package or bad key
            result.error = f'client init failed: {e}'
            return result

        # The system prompt is stable across runs and sits first, so repeated
        # scans read it from cache rather than re-billing it every time.
        system = [{
            'type': 'text',
            'text': SYSTEM_PROMPT,
            'cache_control': {'type': 'ephemeral'},
        }]
        messages = [{
            'role': 'user',
            'content': json.dumps(brief, separators=(',', ':')),
        }]

        response = None
        for attempt in self._attempts(client, system, messages):
            try:
                response = attempt()
                break
            except Exception as e:  # noqa: BLE001
                _log.debug(f'Anthropic advisor attempt failed: {e}')
                result.error = str(e)[:400]

        elapsed_ms = int((time.monotonic() - started) * 1000)
        result.latency_ms = elapsed_ms
        if response is None:
            return result

        result.error = None
        usage = getattr(response, 'usage', None)
        if usage is not None:
            result.input_tokens = getattr(usage, 'input_tokens', 0) or 0
            result.output_tokens = getattr(usage, 'output_tokens', 0) or 0

        # A safety classifier can decline; content is then empty or partial.
        if getattr(response, 'stop_reason', None) == 'refusal':
            result.error = 'model declined the request'
            return result

        text = _first_text(response)
        if not text:
            result.error = 'empty response'
            return result

        result.verdicts = parse_verdicts(text)
        if not result.verdicts:
            result.error = 'no parsable verdicts in response'
        return result

    def _attempts(self, client, system, messages) -> List:
        """Degradation ladder, so a non-default model still works.

        Newer models take a structured-output schema and a server-side
        fallback; older or third-party-hosted ones may reject either, in which
        case we fall back to asking for JSON in the prompt and parsing it.
        """
        base = {
            'model': self.model,
            'max_tokens': MAX_TOKENS,
            'system': system,
            'messages': messages,
        }
        schema_config = {
            'effort': 'low',
            'format': {'type': 'json_schema', 'schema': VERDICT_SCHEMA},
        }

        def with_fallback():
            return client.beta.messages.create(
                betas=[FALLBACK_BETA],
                fallbacks='default',
                output_config=schema_config,
                **base,
            )

        def structured():
            return client.messages.create(output_config=schema_config, **base)

        def schema_only():
            return client.messages.create(
                output_config={'format': {
                    'type': 'json_schema', 'schema': VERDICT_SCHEMA,
                }},
                **base,
            )

        def plain():
            nudged = list(system) + [{
                'type': 'text',
                'text': (
                    'Reply with a single JSON object matching this schema and '
                    'nothing else: ' + json.dumps(VERDICT_SCHEMA)
                ),
            }]
            return client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=nudged,
                messages=messages,
            )

        return [with_fallback, structured, schema_only, plain]


def _first_text(response: Any) -> Optional[str]:
    for block in getattr(response, 'content', None) or []:
        if getattr(block, 'type', None) == 'text':
            return getattr(block, 'text', None)
    return None
