import logging
from typing import Dict, List, Optional, Tuple

from marketbot.db import ExitReason, Position
from marketbot.services.strategy import ActionPlan, ProposedExit

from .base import (
    REDUCE_FACTOR,
    AdvisorResult,
    LLMAdvisor,
    Verdict,
    build_brief,
    parse_verdicts,
)

_log = logging.getLogger(__name__)

__all__ = [
    'AdvisorResult',
    'LLMAdvisor',
    'Verdict',
    'apply_verdicts',
    'build_advisor',
    'build_brief',
    'parse_verdicts',
]


def build_advisor(config) -> Optional[LLMAdvisor]:
    """Construct the configured advisor, or None when it is switched off."""
    if not config.enabled:
        return None

    if config.provider == 'anthropic':
        from .anthropic_advisor import AnthropicAdvisor
        advisor: LLMAdvisor = AnthropicAdvisor(
            model=config.model,
            api_key=config.api_key,
            timeout=config.timeout,
        )
    else:
        from .openai_advisor import OpenAICompatibleAdvisor
        advisor = OpenAICompatibleAdvisor(
            model=config.model,
            api_key=config.api_key,
            timeout=config.timeout,
            base_url=config.base_url,
        )

    if not advisor.available:
        _log.warning(
            f'LLM advisor mode is {config.mode!r} but no credentials are '
            f'configured for {config.provider!r}; running deterministically'
        )
        return None
    return advisor


def apply_verdicts(
    plan: ActionPlan,
    result: AdvisorResult,
    mode: str,
    holdings: Dict[str, Tuple[Position, float, float]],
) -> List[dict]:
    """Fold advisor verdicts into the plan, within hard bounds.

    Returns audit rows for `llm_advice`. In `annotate` mode nothing is
    changed and every row is recorded as not applied. Verdicts naming a
    symbol that is not in the plan are dropped: the advisor cannot introduce
    a trade the engine did not propose.
    """
    rows: List[dict] = []
    if not result.ok or not result.verdicts:
        return rows

    enforce = mode == 'veto'
    by_symbol = result.by_symbol()
    entries_by_symbol = {e.candidate.symbol.upper(): e for e in plan.entries}
    exiting = {x.position.instrument.symbol.upper() for x in plan.exits}

    dropped: List[str] = []

    for symbol, verdict in by_symbol.items():
        applied = False
        entry = entries_by_symbol.get(symbol)

        if verdict.action == 'BUY':
            if entry is None:
                continue  # not a proposed buy — ignore it
            if enforce and verdict.rejects_entry:
                dropped.append(symbol)
                applied = True
            elif enforce and verdict.reduces_entry:
                entry.qty = _reduce_qty(entry.qty, symbol)
                if entry.qty <= 0:
                    dropped.append(symbol)
                applied = True
            if verdict.reason:
                entry.advisor_note = verdict.reason

        else:  # HOLD
            held = holdings.get(symbol)
            if held is None or symbol in exiting:
                continue  # unknown, or the rules are already selling it
            if enforce and verdict.forces_exit:
                position, price, score = held
                plan.exits.append(ProposedExit(
                    position=position,
                    price=price,
                    reason=ExitReason.ADVISOR_EXIT,
                    score=score,
                    advisor_note=verdict.reason,
                ))
                applied = True

        rows.append({
            'symbol': symbol,
            'proposed_action': verdict.action,
            'verdict': verdict.verdict,
            'reason': verdict.reason,
            'confidence': verdict.confidence,
            'applied': applied,
        })

    if dropped:
        plan.entries = [
            e for e in plan.entries
            if e.candidate.symbol.upper() not in dropped
        ]
        _log.info(f'Advisor removed proposed entries: {", ".join(dropped)}')

    return rows


def _reduce_qty(qty: float, symbol: str) -> float:
    reduced = qty * REDUCE_FACTOR
    if qty >= 1 and reduced < 1:
        # Whole-share instruments cannot be halved below one share.
        return 0.0
    _log.info(f'Advisor halved proposed size for {symbol}')
    return reduced
