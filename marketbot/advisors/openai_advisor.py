"""Advisor backed by any OpenAI-compatible endpoint.

Works against OpenAI itself or anything speaking the same protocol —
OpenRouter, Groq, vLLM, Ollama — by pointing `LLM_BASE_URL` at it. Claude is
never routed through this shim; it has its own SDK-native implementation.
"""

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

MAX_TOKENS = 4000


class OpenAICompatibleAdvisor(LLMAdvisor):
    def __init__(
        self,
        model: str,
        api_key: str,
        timeout: float = 60.0,
        base_url: str = '',
    ):
        super().__init__(model, api_key, timeout)
        self.base_url = base_url
        self._client = None

    @property
    def provider(self) -> str:
        return 'openai'

    @property
    def available(self) -> bool:
        # A self-hosted endpoint often needs no key at all.
        return bool(self.api_key or self.base_url)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI  # imported lazily; package is optional
            kwargs: Dict[str, Any] = {
                'api_key': self.api_key or 'not-needed',
                'timeout': self.timeout,
            }
            if self.base_url:
                kwargs['base_url'] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def review(self, brief: Dict[str, Any]) -> AdvisorResult:
        result = AdvisorResult(provider=self.provider, model=self.model)
        started = time.monotonic()

        try:
            client = self._get_client()
        except Exception as e:  # noqa: BLE001
            result.error = f'client init failed: {e}'
            return result

        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': json.dumps(brief, separators=(',', ':'))},
        ]

        response = None
        for attempt in self._attempts(client, messages):
            try:
                response = attempt()
                break
            except Exception as e:  # noqa: BLE001
                _log.debug(f'OpenAI-compatible advisor attempt failed: {e}')
                result.error = str(e)[:400]

        result.latency_ms = int((time.monotonic() - started) * 1000)
        if response is None:
            return result

        result.error = None
        usage = getattr(response, 'usage', None)
        if usage is not None:
            result.input_tokens = getattr(usage, 'prompt_tokens', 0) or 0
            result.output_tokens = getattr(usage, 'completion_tokens', 0) or 0

        text = _first_text(response)
        if not text:
            result.error = 'empty response'
            return result

        result.verdicts = parse_verdicts(text)
        if not result.verdicts:
            result.error = 'no parsable verdicts in response'
        return result

    def _attempts(self, client, messages) -> List:
        """Strict schema first, then plain JSON mode, then prompt-only."""
        base = {
            'model': self.model,
            'messages': messages,
            'max_tokens': MAX_TOKENS,
        }

        def strict_schema():
            return client.chat.completions.create(
                response_format={
                    'type': 'json_schema',
                    'json_schema': {
                        'name': 'portfolio_verdicts',
                        'schema': VERDICT_SCHEMA,
                        'strict': True,
                    },
                },
                **base,
            )

        def json_mode():
            return client.chat.completions.create(
                response_format={'type': 'json_object'}, **base
            )

        def plain():
            nudged = list(messages)
            nudged[0] = {
                'role': 'system',
                'content': (
                    SYSTEM_PROMPT
                    + '\nReply with a single JSON object matching this schema '
                    'and nothing else: ' + json.dumps(VERDICT_SCHEMA)
                ),
            }
            return client.chat.completions.create(
                model=self.model, messages=nudged, max_tokens=MAX_TOKENS
            )

        return [strict_schema, json_mode, plain]


def _first_text(response: Any) -> Optional[str]:
    choices = getattr(response, 'choices', None) or []
    if not choices:
        return None
    message = getattr(choices[0], 'message', None)
    return getattr(message, 'content', None) if message else None
