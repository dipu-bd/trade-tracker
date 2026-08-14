"""The optional LLM second opinion on a scan's proposed action set.

The deterministic strategy always runs first and produces the plan; the
advisor may only narrow it. Concretely it can reject or halve a proposed
entry, and it can force an exit on a holding the rules have not flagged. It
can never invent an entry, raise a size, widen a stop, or veto a protective
exit — those are enforced in `apply_verdicts`, not merely requested in the
prompt, because model output is treated as untrusted input.
"""

import json
import logging
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

_log = logging.getLogger(__name__)

# Verdicts the advisor is allowed to return, per proposed action.
BUY_VERDICTS = ('approve', 'reduce', 'reject')
HOLD_VERDICTS = ('hold', 'force_exit')

MAX_REASON_CHARS = 400
REDUCE_FACTOR = 0.5


VERDICT_SCHEMA: Dict[str, Any] = {
    'type': 'object',
    'properties': {
        'verdicts': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'symbol': {'type': 'string'},
                    'action': {'type': 'string', 'enum': ['BUY', 'HOLD']},
                    'verdict': {
                        'type': 'string',
                        'enum': list(BUY_VERDICTS) + list(HOLD_VERDICTS),
                    },
                    'confidence': {'type': 'number'},
                    'reason': {'type': 'string'},
                },
                'required': ['symbol', 'action', 'verdict', 'confidence', 'reason'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['verdicts'],
    'additionalProperties': False,
}


SYSTEM_PROMPT = """\
You are a risk reviewer for a systematic swing-trading portfolio. A quantitative \
engine has already screened the market, scored every candidate, sized the positions \
against a fixed risk budget, and placed the stops. Your job is to review its proposed \
actions and catch the ones a careful human would question.

You are reviewing, not trading. Your authority is strictly limited:

- For each proposed BUY, answer `approve`, `reduce` (half size), or `reject`.
- For each current HOLD, answer `hold` or `force_exit`.
- You cannot propose a symbol that is not in the input. You cannot increase a size, \
move a stop, or cancel a sell the engine has already decided on.

Judge each action on the evidence given: trend quality, momentum consistency, whether \
the move looks extended or is being chased, liquidity, concentration against what is \
already held, and coherence with the market regime. Prefer `approve` when the setup is \
ordinary and the numbers support it — the engine's rules are the baseline and \
second-guessing every trade destroys the edge. Reserve `reject` for setups that are \
clearly poor on the evidence: a chased parabolic move, a broken trend the score has not \
caught up with, or a position that would concentrate the book badly. Use `reduce` when \
the idea is sound but the conviction is thin. Use `force_exit` only when a holding's \
thesis has plainly broken.

Return a verdict for every symbol in the input, exactly once. `confidence` is 0.0 to \
1.0. `reason` is one sentence, under 200 characters, stating the specific evidence you \
relied on — it is shown to the portfolio owner in an email.
"""


@dataclass
class Verdict:
    symbol: str
    action: str
    verdict: str
    reason: str = ''
    confidence: float = 0.0

    @property
    def rejects_entry(self) -> bool:
        return self.action == 'BUY' and self.verdict == 'reject'

    @property
    def reduces_entry(self) -> bool:
        return self.action == 'BUY' and self.verdict == 'reduce'

    @property
    def forces_exit(self) -> bool:
        return self.action == 'HOLD' and self.verdict == 'force_exit'


@dataclass
class AdvisorResult:
    provider: str = ''
    model: str = ''
    verdicts: List[Verdict] = field(default_factory=list)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def by_symbol(self) -> Dict[str, Verdict]:
        return {v.symbol.upper(): v for v in self.verdicts}


class LLMAdvisor(metaclass=ABCMeta):
    """One provider's implementation of the review call."""

    def __init__(self, model: str, api_key: str, timeout: float = 60.0):
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    @abstractmethod
    def provider(self) -> str:
        pass

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @abstractmethod
    def review(self, brief: Dict[str, Any]) -> AdvisorResult:
        """Send the brief and return parsed verdicts, never raising."""


# --------------------------------------------------------------------------- #
# Brief construction and response parsing
# --------------------------------------------------------------------------- #

def build_brief(
    regime: str,
    equity: float,
    cash: float,
    entries: Sequence[Any],
    holdings: Sequence[Dict[str, Any]],
    pending_exits: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compact, numeric-only description of the run. No prose, no history."""
    return {
        'market_regime': regime,
        'portfolio': {
            'equity': round(equity, 2),
            'cash': round(cash, 2),
            'open_positions': len(holdings),
        },
        'proposed_buys': [entry.brief() for entry in entries],
        'current_holdings': list(holdings),
        'already_selling': list(pending_exits),
    }


def parse_verdicts(payload: Any) -> List[Verdict]:
    """Coerce a model response into verdicts, dropping anything malformed."""
    if isinstance(payload, str):
        payload = _extract_json(payload)
    if not isinstance(payload, dict):
        return []

    raw = payload.get('verdicts')
    if not isinstance(raw, list):
        return []

    verdicts: List[Verdict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get('symbol') or '').upper().strip()
        action = str(item.get('action') or '').upper().strip()
        verdict = str(item.get('verdict') or '').lower().strip()
        if not symbol or action not in ('BUY', 'HOLD'):
            continue
        allowed = BUY_VERDICTS if action == 'BUY' else HOLD_VERDICTS
        if verdict not in allowed:
            continue

        try:
            confidence = float(item.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        verdicts.append(Verdict(
            symbol=symbol,
            action=action,
            verdict=verdict,
            reason=str(item.get('reason') or '')[:MAX_REASON_CHARS],
            confidence=max(0.0, min(1.0, confidence)),
        ))
    return verdicts


def _extract_json(text: str) -> Any:
    """Tolerate a fenced or prose-wrapped JSON object."""
    text = text.strip()
    if text.startswith('```'):
        text = text.split('```')[1] if '```' in text[3:] else text[3:]
        if text.lstrip().startswith('json'):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except ValueError:
        pass

    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None
