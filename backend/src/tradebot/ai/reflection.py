from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradebot.ai.brief import scrub_dates
from tradebot.ai.client import AIClient, ModelTier
from tradebot.core.clock import Clock
from tradebot.db.models import Lesson

REFLECTION_SYSTEM = """You review one closed trade and write the lesson.

One paragraph, at most three sentences, addressed to the system that made the trade. Say what
the setup was, what actually happened, and what to weigh differently next time. Alpha is the
return above the benchmark over the same holding period — a winning trade that lagged the
benchmark is not a good trade. Do not restate the numbers; interpret them. Times are relative;
you are not told the calendar date."""

MAX_LESSONS = 6
SAME_SYMBOL = 3


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    portfolio_id: int
    position_id: int
    symbol: str
    closed_at: datetime
    holding_days: int
    realized_return: float
    benchmark_return: float

    @property
    def alpha(self) -> float:
        return self.realized_return - self.benchmark_return


class Reflection:
    """Turns a closed position into a lesson the next deliberation can read.

    Computed once per closed position rather than per cycle: the outcome does not change, and
    re-deriving it would spend rate-limit headroom the decision call needs.
    """

    def __init__(self, client: AIClient, quick: ModelTier, clock: Clock) -> None:
        self._client = client
        self._quick = quick
        self._clock = clock

    async def record(self, session: AsyncSession, trade: ClosedTrade) -> Lesson | None:
        existing = await session.scalar(
            select(Lesson).where(
                Lesson.portfolio_id == trade.portfolio_id,
                Lesson.position_id == trade.position_id,
            )
        )
        if existing is not None:
            return existing

        completion = await self._client.complete(
            self._quick,
            system=REFLECTION_SYSTEM,
            user=_prompt(trade),
            max_tokens=220,
        )
        text = scrub_dates(completion.text.strip())[:1200] if completion.ok else ""

        lesson = Lesson(
            portfolio_id=trade.portfolio_id,
            position_id=trade.position_id,
            symbol=trade.symbol,
            closed_at=trade.closed_at,
            holding_days=trade.holding_days,
            realized_return=Decimal(str(round(trade.realized_return, 8))),
            benchmark_return=Decimal(str(round(trade.benchmark_return, 8))),
            alpha=Decimal(str(round(trade.alpha, 8))),
            text=text,
        )
        session.add(lesson)
        await session.flush()
        return lesson


async def recall(
    session: AsyncSession, portfolio_id: int, symbols: list[str], limit: int = MAX_LESSONS
) -> list[str]:
    """Recent same-symbol lessons first, then cross-symbol ones.

    Bounded deliberately: an unbounded memory would grow the brief every cycle until the prompt
    cost more than the decision is worth, and the oldest lessons are the least relevant.
    """
    wanted = [symbol.upper() for symbol in symbols]
    picked: list[str] = []
    seen: set[int] = set()

    if wanted:
        rows = await session.scalars(
            select(Lesson)
            .where(Lesson.portfolio_id == portfolio_id, Lesson.symbol.in_(wanted))
            .order_by(Lesson.closed_at.desc())
            .limit(SAME_SYMBOL)
        )
        for lesson in rows:
            if lesson.text:
                picked.append(_render(lesson))
                seen.add(lesson.id)

    remaining = limit - len(picked)
    if remaining > 0:
        rows = await session.scalars(
            select(Lesson)
            .where(Lesson.portfolio_id == portfolio_id)
            .order_by(Lesson.closed_at.desc())
            .limit(limit * 2)
        )
        for lesson in rows:
            if lesson.id in seen or not lesson.text:
                continue
            picked.append(_render(lesson))
            if len(picked) >= limit:
                break

    return picked


def _render(lesson: Lesson) -> str:
    return (
        f"{lesson.symbol} held {lesson.holding_days}d, "
        f"return {float(lesson.realized_return) * 100:+.1f}%, "
        f"alpha {float(lesson.alpha) * 100:+.1f}%: {lesson.text}"
    )


def _prompt(trade: ClosedTrade) -> str:
    return "\n".join(
        [
            f"Instrument {trade.symbol}.",
            f"Held {trade.holding_days} sessions.",
            f"Realized return {trade.realized_return * 100:+.2f}%.",
            f"Benchmark over the same period {trade.benchmark_return * 100:+.2f}%.",
            f"Alpha {trade.alpha * 100:+.2f}%.",
            "Write the lesson.",
        ]
    )
