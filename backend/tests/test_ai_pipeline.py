from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from tests.test_engine_cycle import NOW, seed
from tradebot.ai.analysts import AnalystCache
from tradebot.ai.client import AIClient
from tradebot.ai.pipeline import AIPipeline, tiers_for
from tradebot.ai.reflection import ClosedTrade, Reflection, recall
from tradebot.broker.ledger import Ledger
from tradebot.broker.service import BrokerService
from tradebot.context import AppContext
from tradebot.core.clock import FrozenClock
from tradebot.db.models import AICall, DecisionRun, Instrument, Lesson, Order, Portfolio, User
from tradebot.engine.cycle import DecisionCycle

DEEP = "https://deep.test/v1"
QUICK = "https://quick.test/v1"

MODELS = {
    "quick": {"base_url": QUICK, "model": "gpt-5-mini", "credential": "openrouter"},
    "deep": {"base_url": DEEP, "model": "gpt-5", "credential": "openrouter"},
}
KEYS = {"openrouter": "sk-test-key"}


def reply(content: str, model: str = "gpt-5") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 150},
        },
    )


def verdicts(*rows: str) -> str:
    return '{"verdicts": [' + ",".join(rows) + "]}"


def take(symbol: str, confidence: float = 0.8) -> str:
    return (
        f'{{"symbol": "{symbol}", "take": true, "confidence": {confidence}, '
        f'"bull": "trend intact", "bear": "could reverse", "thesis": "take it"}}'
    )


def skip(symbol: str) -> str:
    return (
        f'{{"symbol": "{symbol}", "take": false, "confidence": 0.0, '
        f'"bull": "b", "bear": "r", "thesis": "no"}}'
    )


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
async def portfolio_id(context: AppContext, clock: FrozenClock) -> int:
    broker = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    async with context.db.session() as session:
        user = User(email="ai@example.com", password_hash="x", display_name="AI")
        session.add(user)
        await session.flush()
        portfolio = await broker.create_portfolio(
            session,
            user_id=user.id,
            name="AI Momentum",
            initial_capital=Decimal(100_000),
            allow_fractional=True,
        )
        portfolio.ai_enabled = True
        portfolio.deliberation = "single_call"
        portfolio.models = MODELS
        return int(portfolio.id)


def cycle(context: AppContext, clock: FrozenClock, http: httpx.AsyncClient) -> DecisionCycle:
    broker = BrokerService(Ledger(clock=clock), context.events, clock=clock)
    pipeline = AIPipeline(AIClient(http, clock=clock), clock, AnalystCache())
    return DecisionCycle(broker, context.events, clock, ai=pipeline, keys=KEYS)


async def run_cycle(context: AppContext, clock: FrozenClock, portfolio_id: int, http):  # type: ignore[no-untyped-def]
    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        return await cycle(context, clock, http).run(session, portfolio, trigger="manual")


async def universe(context: AppContext) -> None:
    await seed(context, "SPY", daily=0.0008, count=700, asset_class="index")
    await seed(context, "AAA", daily=0.002)
    await seed(context, "BBB", daily=0.0015)


@respx.mock
async def test_the_ai_scales_the_rules_position_rather_than_replacing_it(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    await universe(context)
    respx.post(f"{DEEP}/chat/completions").mock(
        return_value=reply(verdicts(take("AAA", 0.5), take("BBB", 1.0)))
    )

    async with httpx.AsyncClient() as http:
        report = await run_cycle(context, clock, portfolio_id, http)

    assert report.ok, report.error
    assert report.ai is not None and report.ai.used
    assert report.orders

    async with context.db.session() as session:
        run = await session.scalar(select(DecisionRun))

    weights = {row["symbol"]: row["target_weight"] for row in run.detail["entries"]}
    assert weights["AAA"] < weights["BBB"], "lower confidence must buy less"


@respx.mock
async def test_a_skip_verdict_stops_the_order_the_rules_wanted(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    await universe(context)
    respx.post(f"{DEEP}/chat/completions").mock(
        return_value=reply(verdicts(skip("AAA"), take("BBB", 0.9)))
    )

    async with httpx.AsyncClient() as http:
        await run_cycle(context, clock, portfolio_id, http)

    async with context.db.session() as session:
        bought = list(
            await session.scalars(
                select(Instrument.symbol).join(Order, Order.instrument_id == Instrument.id)
            )
        )

    assert "AAA" not in bought
    assert "BBB" in bought


@respx.mock
async def test_a_verdict_for_an_unproposed_symbol_never_reaches_the_broker(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    """The meta-labeling boundary, asserted through the real cycle rather than in isolation."""
    await universe(context)
    await seed(context, "ZZZ", daily=-0.003)
    respx.post(f"{DEEP}/chat/completions").mock(
        return_value=reply(verdicts(take("AAA"), take("ZZZ", 1.0), take("NVDA", 1.0)))
    )

    async with httpx.AsyncClient() as http:
        await run_cycle(context, clock, portfolio_id, http)

    async with context.db.session() as session:
        bought = list(
            await session.scalars(
                select(Instrument.symbol).join(Order, Order.instrument_id == Instrument.id)
            )
        )
        run = await session.scalar(select(DecisionRun))

    assert "ZZZ" not in bought
    assert "NVDA" not in bought
    clamped = {row["symbol"] for row in run.detail["ai"]["guardrail_diff"]}
    assert {"ZZZ", "NVDA"} <= clamped


@respx.mock
async def test_an_unreachable_model_leaves_the_cycle_running_on_the_rules(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    """The AI is an overlay on a working strategy, never a dependency of it."""
    await universe(context)
    respx.post(f"{DEEP}/chat/completions").mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as http:
        report = await run_cycle(context, clock, portfolio_id, http)

    assert report.ok
    assert report.orders, "rules-only orders must still be placed"
    assert report.ai is not None and not report.ai.used


@respx.mock
async def test_an_unparseable_response_degrades_to_rules_only(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    await universe(context)
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply("I refuse."))

    async with httpx.AsyncClient() as http:
        report = await run_cycle(context, clock, portfolio_id, http)

    assert report.ok
    assert report.orders
    async with context.db.session() as session:
        run = await session.scalar(select(DecisionRun))
    assert run.detail["ai"]["used"] is False


@respx.mock
async def test_a_portfolio_with_ai_disabled_never_calls_a_model(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    await universe(context)
    route = respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(verdicts(take("AAA"))))

    async with context.db.session() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        portfolio.ai_enabled = False

    async with httpx.AsyncClient() as http:
        report = await run_cycle(context, clock, portfolio_id, http)

    assert route.call_count == 0
    assert report.orders
    assert report.ai is not None and "disabled" in report.ai.reason


@respx.mock
async def test_every_call_is_audited_with_its_prompt_and_raw_response(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    """ "Why did it buy this?" is unanswerable from a summary, so the full text is stored."""
    await universe(context)
    raw = verdicts(take("AAA", 0.7))
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(raw))

    async with httpx.AsyncClient() as http:
        report = await run_cycle(context, clock, portfolio_id, http)

    async with context.db.session() as session:
        call = await session.scalar(select(AICall))

    assert call.stage == "deliberation"
    assert call.decision_run_id == report.run_id
    assert call.correlation_id == report.correlation_id
    assert call.model == "gpt-5"
    assert call.response == raw
    assert "CANDIDATE AAA" in call.user_prompt
    assert call.brief_hash
    assert call.prompt_tokens == 800
    assert call.cost_usd > 0


@respx.mock
async def test_the_stored_prompt_carries_no_absolute_date(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    await universe(context)
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(verdicts(take("AAA"))))

    async with httpx.AsyncClient() as http:
        await run_cycle(context, clock, portfolio_id, http)

    async with context.db.session() as session:
        call = await session.scalar(select(AICall))

    from tradebot.ai.brief import ABSOLUTE_DATE

    assert ABSOLUTE_DATE.search(call.user_prompt) is None
    assert ABSOLUTE_DATE.search(call.system_prompt) is None


@respx.mock
async def test_the_api_key_never_reaches_the_audit_row(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    await universe(context)
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(verdicts(take("AAA"))))

    async with httpx.AsyncClient() as http:
        await run_cycle(context, clock, portfolio_id, http)

    async with context.db.session() as session:
        rows = list(await session.scalars(select(AICall)))

    for row in rows:
        blob = f"{row.system_prompt}{row.user_prompt}{row.response}{row.endpoint}{row.attempts}"
        assert KEYS["openrouter"] not in blob


def test_a_portfolio_without_model_config_yields_no_tiers() -> None:
    portfolio = Portfolio(name="x", initial_capital=Decimal(1), models={})

    quick, deep = tiers_for(portfolio, {})

    assert quick is None and deep is None


def test_a_fallback_endpoint_is_built_when_configured() -> None:
    portfolio = Portfolio(
        name="x",
        initial_capital=Decimal(1),
        models={
            "deep": {"base_url": DEEP, "model": "gpt-5", "credential": "a"},
            "deep_fallback": {"base_url": QUICK, "model": "gemini-2.0-flash", "credential": "b"},
        },
    )

    _, deep = tiers_for(portfolio, {"a": "k1", "b": "k2"})

    assert deep is not None
    assert deep.fallback is not None
    assert deep.fallback.model == "gemini-2.0-flash"


@respx.mock
async def test_a_closed_trade_produces_one_lesson_and_only_one(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    respx.post(f"{QUICK}/chat/completions").mock(
        return_value=reply("Momentum was intact but it lagged the benchmark; demand more edge.")
    )
    trade = ClosedTrade(portfolio_id, 1, "AAA", NOW, 30, 0.08, 0.12)

    async with httpx.AsyncClient() as http:
        client = AIClient(http, clock=clock)
        from tradebot.ai.client import Endpoint, ModelTier

        tier = ModelTier(Endpoint(base_url=QUICK, api_key="k", model="gpt-5-mini"))
        reflection = Reflection(client, tier, clock)

        async with context.db.session() as session:
            await reflection.record(session, trade)
        async with context.db.session() as session:
            await reflection.record(session, trade)

    async with context.db.session() as session:
        rows = list(await session.scalars(select(Lesson)))

    assert len(rows) == 1
    assert rows[0].alpha == Decimal("-0.04")
    assert "lagged" in rows[0].text


async def test_recall_prefers_lessons_about_the_same_symbol(
    context: AppContext, portfolio_id: int
) -> None:
    async with context.db.session() as session:
        for index, symbol in enumerate(["OLD", "OLD", "AAA"]):
            session.add(
                Lesson(
                    portfolio_id=portfolio_id,
                    position_id=index,
                    symbol=symbol,
                    closed_at=NOW,
                    holding_days=10,
                    text=f"lesson about {symbol}",
                )
            )

    async with context.db.session() as session:
        lessons = await recall(session, portfolio_id, ["AAA"])

    assert "AAA" in lessons[0]


async def test_recall_is_bounded(context: AppContext, portfolio_id: int) -> None:
    """An unbounded memory would grow the brief until it costs more than the decision."""
    async with context.db.session() as session:
        for index in range(30):
            session.add(
                Lesson(
                    portfolio_id=portfolio_id,
                    position_id=index,
                    symbol=f"S{index}",
                    closed_at=NOW,
                    holding_days=5,
                    text=f"lesson {index}",
                )
            )

    async with context.db.session() as session:
        assert len(await recall(session, portfolio_id, ["S1"])) <= 6


@respx.mock
async def test_a_position_closed_by_trading_produces_a_lesson_on_the_next_cycle(
    context: AppContext, clock: FrozenClock, portfolio_id: int
) -> None:
    """The memory only fills if closing a position actually triggers a reflection."""
    await universe(context)
    respx.post(f"{DEEP}/chat/completions").mock(return_value=reply(verdicts(take("AAA"))))
    respx.post(f"{QUICK}/chat/completions").mock(
        return_value=reply("It trended then stalled; demand stronger confirmation.", "gpt-5-mini")
    )

    from tradebot.ai.client import Endpoint, ModelTier
    from tradebot.db.models import Lot, Position, PositionStatus

    async with context.db.session() as session:
        instrument = await session.scalar(select(Instrument).where(Instrument.symbol == "AAA"))
        position = Position(
            portfolio_id=portfolio_id,
            instrument_id=instrument.id,
            status=PositionStatus.CLOSED,
            qty=Decimal(0),
            avg_cost=Decimal(100),
            realized_pnl=Decimal(250),
            opened_at=NOW - timedelta(days=40),
            closed_at=NOW - timedelta(days=1),
        )
        session.add(position)
        await session.flush()
        session.add(
            Lot(
                position_id=position.id,
                qty_original=Decimal(50),
                qty_open=Decimal(0),
                cost_basis=Decimal(100),
                opened_at=NOW - timedelta(days=40),
            )
        )

    async with httpx.AsyncClient() as http:
        client = AIClient(http, clock=clock)
        quick_tier = ModelTier(Endpoint(base_url=QUICK, api_key="k", model="gpt-5-mini"))
        broker = BrokerService(Ledger(clock=clock), context.events, clock=clock)
        engine = DecisionCycle(
            broker,
            context.events,
            clock,
            ai=AIPipeline(client, clock, AnalystCache()),
            reflection=Reflection(client, quick_tier, clock),
            keys=KEYS,
        )
        async with context.db.session() as session:
            portfolio = await session.get(Portfolio, portfolio_id)
            await engine.run(session, portfolio)

    async with context.db.session() as session:
        lesson = await session.scalar(select(Lesson))

    assert lesson is not None
    assert lesson.symbol == "AAA"
    assert lesson.realized_return == Decimal("0.05")
    assert "confirmation" in lesson.text
