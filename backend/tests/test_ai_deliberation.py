from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from tests.factories import trending
from tradebot.ai.analysts import AnalystCache, AnalystKind, AnalystPool
from tradebot.ai.brief import ABSOLUTE_DATE, build, scrub_dates
from tradebot.ai.client import AIClient, Endpoint, ModelTier
from tradebot.ai.deliberation import STRATEGIES, FirmDebate, MultiRoundDebate, SingleCall
from tradebot.ai.schema import AnalystNote
from tradebot.analytics.exits import ExitAction, ExitReason, Holding
from tradebot.analytics.features import extract
from tradebot.analytics.signals import Regime, RegimeState
from tradebot.analytics.sizing import Sizing
from tradebot.engine.strategy import Decision, Entry, PortfolioState
from tradebot.providers.base import Capability

NOW = datetime(2024, 12, 3, 12, 0, tzinfo=UTC)
AS_OF = date(2024, 12, 2)
QUICK = "https://quick.test/v1"
DEEP = "https://deep.test/v1"
CALM = Regime(RegimeState.CALM, 1.0, 0.1, 0.2, 0.5, False)

FEATURES = {"AAA": extract(trending("AAA")), "BBB": extract(trending("BBB", daily=0.002))}


def entry(symbol: str = "AAA", weight: float = 0.10) -> Entry:
    return Entry(
        symbol,
        weight,
        0.0,
        weight * 100_000,
        0.8,
        Sizing(symbol, weight, 1.0, 0.2, 0.06, "atr_risk"),
        94.0,
    )


def decision(entries: list[Entry] | None = None, exits: list[ExitAction] | None = None) -> Decision:
    return Decision(
        as_of=AS_OF,
        regime=CALM,
        entries=entries if entries is not None else [entry()],
        exits=exits or [],
    )


def state(**kwargs: object) -> PortfolioState:
    base: dict[str, object] = {"equity": 100_000.0, "cash": 50_000.0}
    return PortfolioState(**{**base, **kwargs})  # type: ignore[arg-type]


def tier(base: str) -> ModelTier:
    return ModelTier(Endpoint(base_url=base, api_key="sk-x", model="gpt-5-mini", label=base))


VERDICTS = (
    '{"verdicts": [{"symbol": "AAA", "take": true, "confidence": 0.7, '
    '"bull": "b", "bear": "r", "thesis": "t"}]}'
)
NOTE = '{"stance": "bullish", "confidence": 0.6, "summary": "momentum is intact"}'


def reply(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "gpt-5-mini",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        },
    )


def test_the_brief_never_contains_an_absolute_date() -> None:
    """Stating the date lets a model recall what happened next — the leakage behind Sharpe 8.21."""
    brief = build(decision([entry("AAA"), entry("BBB")]), state(), FEATURES)

    assert not brief.leaks_a_date
    assert ABSOLUTE_DATE.search(brief.text) is None
    assert ABSOLUTE_DATE.search(brief.system) is None


def test_the_brief_scrubs_a_date_smuggled_in_through_an_analyst_note() -> None:
    notes = {
        "AAA": {
            "news": AnalystNote(
                stance="bullish", confidence=0.5, summary="On 2024-03-15 it rallied"
            )
        }
    }

    brief = build(decision(), state(), FEATURES, notes=notes)

    assert not brief.leaks_a_date
    assert "2024" not in brief.text


def test_the_brief_scrubs_a_date_smuggled_in_through_a_lesson() -> None:
    brief = build(decision(), state(), FEATURES, lessons=["In January 2023 this setup failed"])

    assert not brief.leaks_a_date


@pytest.mark.parametrize(
    "text",
    ["2024-03-15", "March 15", "15 March", "in 2019 the market", "Dec 2021", "Jan. 3"],
)
def test_every_common_date_shape_is_scrubbed(text: str) -> None:
    assert ABSOLUTE_DATE.search(scrub_dates(text)) is None


def test_a_number_that_is_not_a_date_survives_scrubbing() -> None:
    """Prices and returns must not be mangled by the date filter."""
    assert scrub_dates("px=1850.25 r12m=18.4% adx=31") == "px=1850.25 r12m=18.4% adx=31"


def test_the_brief_names_exactly_the_rules_candidates() -> None:
    brief = build(decision([entry("AAA"), entry("BBB")]), state(), FEATURES)

    assert brief.candidates == ["AAA", "BBB"]
    assert "Return a verdict for each of: AAA, BBB" in brief.text


def test_the_brief_marks_ordered_exits_as_not_negotiable() -> None:
    exits = [ExitAction("CCC", ExitReason.STOP_LOSS, 1.0, "stopped")]

    brief = build(decision(exits=exits), state(), FEATURES)

    assert "EXITS_ORDERED (not yours to change)" in brief.text
    assert "CCC:stop_loss" in brief.text


def test_the_brief_digest_is_stable_and_changes_with_content() -> None:
    first = build(decision(), state(), FEATURES)
    same = build(decision(), state(), FEATURES)
    other = build(decision([entry("BBB")]), state(), FEATURES)

    assert first.digest == same.digest
    assert first.digest != other.digest


def test_a_held_candidate_is_labelled_as_held() -> None:
    holdings = {"AAA": Holding("AAA", 10.0, 100.0, AS_OF, 100.0, 90.0)}

    brief = build(decision(), state(holdings=holdings), FEATURES)

    assert "CANDIDATE AAA held" in brief.text


@respx.mock
async def test_single_call_skips_the_analysts_entirely() -> None:
    deep = respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    quick = respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        result = await SingleCall(client, tier(DEEP)).run(AS_OF, decision(), state(), FEATURES)

    assert result.ok
    assert deep.call_count == 1
    assert quick.call_count == 0
    assert result.verdicts[0].symbol == "AAA"


@respx.mock
async def test_firm_debate_runs_analysts_then_one_deep_call() -> None:
    deep = respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    quick = respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        pool = AnalystPool(client, tier(QUICK))
        result = await FirmDebate(client, tier(DEEP), pool).run(
            AS_OF, decision(), state(), FEATURES, capabilities=frozenset(Capability)
        )

    assert result.ok
    assert deep.call_count == 1
    assert quick.call_count == len(AnalystKind)
    assert result.analysts is not None


@respx.mock
async def test_capability_gating_skips_the_news_analyst_when_no_provider_offers_news() -> None:
    """Carried forward: the news path has never been exercised live, so the skip must work."""
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    quick = respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        pool = AnalystPool(client, tier(QUICK))
        result = await FirmDebate(client, tier(DEEP), pool).run(
            AS_OF,
            decision(),
            state(),
            FEATURES,
            capabilities=frozenset({Capability.FUNDAMENTALS}),
        )

    assert result.analysts is not None
    assert "news" in result.analysts.skipped
    assert "sentiment" in result.analysts.skipped
    assert quick.call_count == 2


@respx.mock
async def test_with_no_capabilities_only_the_technical_analyst_runs() -> None:
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    quick = respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        pool = AnalystPool(client, tier(QUICK))
        await FirmDebate(client, tier(DEEP), pool).run(AS_OF, decision(), state(), FEATURES)

    assert quick.call_count == 1


@respx.mock
async def test_the_analyst_cache_is_shared_across_cycles_on_the_same_day() -> None:
    """Four cycles a day must share one set of passes, or the free tier is gone by lunch."""
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    quick = respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        pool = AnalystPool(client, tier(QUICK), cache=AnalystCache())
        strategy = FirmDebate(client, tier(DEEP), pool)

        first = await strategy.run(AS_OF, decision(), state(), FEATURES)
        second = await strategy.run(AS_OF, decision(), state(), FEATURES)

    assert quick.call_count == 1
    assert first.analysts is not None and first.analysts.cache_hits == 0
    assert second.analysts is not None and second.analysts.cache_hits == 1


@respx.mock
async def test_a_new_day_invalidates_the_analyst_cache() -> None:
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    quick = respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        strategy = FirmDebate(client, tier(DEEP), AnalystPool(client, tier(QUICK)))

        await strategy.run(AS_OF, decision(), state(), FEATURES)
        await strategy.run(date(2024, 12, 3), decision(), state(), FEATURES)

    assert quick.call_count == 2


def test_pruning_drops_only_stale_cache_entries() -> None:
    cache = AnalystCache()
    note = AnalystNote(stance="bullish", confidence=0.5, summary="s")
    cache.put("AAA", AnalystKind.NEWS, date(2024, 12, 1), note)
    cache.put("AAA", AnalystKind.NEWS, date(2024, 12, 2), note)

    assert cache.prune(date(2024, 12, 2)) == 1
    assert len(cache) == 1


@respx.mock
async def test_multi_round_debate_makes_the_model_attack_its_own_first_pass() -> None:
    deep = respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        pool = AnalystPool(client, tier(QUICK))
        result = await MultiRoundDebate(client, tier(DEEP), pool, rounds=3).run(
            AS_OF, decision(), state(), FEATURES
        )

    assert deep.call_count == 3
    assert result.rounds == 3
    assert result.strategy == "multi_round_debate"
    assert "Attack it" in deep.calls[-1].request.content.decode()


@respx.mock
async def test_an_empty_candidate_list_makes_no_model_call_at_all() -> None:
    deep = respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))

    async with httpx.AsyncClient() as http:
        result = await SingleCall(AIClient(http), tier(DEEP)).run(
            AS_OF, decision(entries=[]), state(), FEATURES
        )

    assert deep.call_count == 0
    assert result.verdicts == []


@respx.mock
async def test_an_unparseable_response_is_reported_rather_than_raised() -> None:
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply("I decline to answer."))

    async with httpx.AsyncClient() as http:
        result = await SingleCall(AIClient(http), tier(DEEP)).run(
            AS_OF, decision(), state(), FEATURES
        )

    assert not result.ok
    assert result.parse_error is not None
    assert result.verdicts == []


@respx.mock
async def test_a_dead_model_degrades_to_no_verdicts_rather_than_crashing() -> None:
    respx.post(f"{DEEP}/chat/completions").mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as http:
        result = await SingleCall(AIClient(http), tier(DEEP)).run(
            AS_OF, decision(), state(), FEATURES
        )

    assert not result.ok
    assert result.verdicts == []


@respx.mock
async def test_cost_and_tokens_aggregate_across_analysts_and_deliberation() -> None:
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(VERDICTS))
    respx.post(f"{QUICK}/chat/completions").mock(return_value=reply(NOTE))

    async with httpx.AsyncClient() as http:
        client = AIClient(http)
        pool = AnalystPool(client, tier(QUICK))
        result = await FirmDebate(client, tier(DEEP), pool).run(
            AS_OF, decision(), state(), FEATURES, capabilities=frozenset(Capability)
        )

    assert result.prompt_tokens == 500
    assert result.cost_usd > 0
    assert result.analysts is not None and result.analysts.cost_usd > 0
    assert result.model == "gpt-5-mini"


def test_every_strategy_is_registered_by_name() -> None:
    assert set(STRATEGIES) == {"single_call", "firm_debate", "multi_round_debate"}
