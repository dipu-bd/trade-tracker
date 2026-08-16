from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from tradebot.ai.analysts import AnalystKind, AnalystPool, AnalystRun
from tradebot.ai.brief import Brief, build
from tradebot.ai.client import AIClient, Completion, ModelTier
from tradebot.ai.schema import Verdict, deliberation_schema, parse_deliberation
from tradebot.analytics.features import Features
from tradebot.engine.strategy import Decision, PortfolioState
from tradebot.providers.base import Capability

DELIBERATION_SYSTEM_SUFFIX = """
Write the bull case and the bear case for every candidate before its verdict. A verdict with no
bear case is worthless — if you cannot argue against a trade you do not understand it well enough
to size it."""

REBUTTAL = """Here is your own first pass. Attack it.

For each verdict you were too confident on, say why. For each you skipped, say what you might
have missed. Then return the corrected verdict list in the same schema."""


@dataclass
class DeliberationResult:
    verdicts: list[Verdict] = field(default_factory=list)
    brief: Brief | None = None
    completions: list[Completion] = field(default_factory=list)
    analysts: AnalystRun | None = None
    strategy: str = ""
    rounds: int = 0
    parse_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.parse_error is None and bool(self.completions)

    @property
    def cost_usd(self) -> float:
        analyst_cost = self.analysts.cost_usd if self.analysts else 0.0
        return analyst_cost + sum(item.cost_usd for item in self.completions)

    @property
    def prompt_tokens(self) -> int:
        return sum(item.prompt_tokens for item in self.completions)

    @property
    def completion_tokens(self) -> int:
        return sum(item.completion_tokens for item in self.completions)

    @property
    def latency_ms(self) -> int:
        return sum(item.latency_ms for item in self.completions)

    @property
    def model(self) -> str:
        return self.completions[-1].model if self.completions else ""

    @property
    def rung(self) -> str:
        return self.completions[-1].rung.value if self.completions else ""


class DeliberationStrategy(ABC):
    """Swappable so the backtester can price each against the others.

    The TradingAgents paper reports a debate architecture without ablating it; making the
    strategy an interface is what turns "is the debate worth it?" into a measured number.
    """

    name: str = "base"

    def __init__(self, client: AIClient, deep: ModelTier) -> None:
        self._client = client
        self._deep = deep

    @abstractmethod
    async def run(
        self,
        as_of: date,
        decision: Decision,
        state: PortfolioState,
        features: dict[str, Features],
        lessons: list[str] | None = None,
        capabilities: frozenset[Capability] = frozenset(),
    ) -> DeliberationResult: ...

    async def _decide(
        self, brief: Brief, extra: str = ""
    ) -> tuple[Completion, list[Verdict], str | None]:
        completion = await self._client.complete(
            self._deep,
            system=brief.system + DELIBERATION_SYSTEM_SUFFIX,
            user=brief.text if not extra else f"{brief.text}\n\n{extra}",
            schema=deliberation_schema(),
            schema_name="deliberation",
            max_tokens=3000,
        )
        if not completion.ok:
            return completion, [], completion.error or "no response"

        parsed, error = parse_deliberation(completion.text)
        return completion, (parsed.verdicts if parsed else []), error


class SingleCall(DeliberationStrategy):
    """No analysts, one decision call. The cheap baseline the others must beat."""

    name = "single_call"

    async def run(
        self,
        as_of: date,
        decision: Decision,
        state: PortfolioState,
        features: dict[str, Features],
        lessons: list[str] | None = None,
        capabilities: frozenset[Capability] = frozenset(),
    ) -> DeliberationResult:
        brief = build(decision, state, features, lessons=lessons)
        if not brief.candidates:
            return DeliberationResult(brief=brief, strategy=self.name)

        completion, verdicts, error = await self._decide(brief)
        return DeliberationResult(
            verdicts=verdicts,
            brief=brief,
            completions=[completion],
            strategy=self.name,
            rounds=1,
            parse_error=error,
        )


class FirmDebate(DeliberationStrategy):
    """Analyst passes on the quick model, then one deep call carrying bull and bear."""

    name = "firm_debate"

    def __init__(
        self,
        client: AIClient,
        deep: ModelTier,
        analysts: AnalystPool,
        kinds: list[AnalystKind] | None = None,
    ) -> None:
        super().__init__(client, deep)
        self._analysts = analysts
        self._kinds = kinds or list(AnalystKind)

    async def run(
        self,
        as_of: date,
        decision: Decision,
        state: PortfolioState,
        features: dict[str, Features],
        lessons: list[str] | None = None,
        capabilities: frozenset[Capability] = frozenset(),
    ) -> DeliberationResult:
        candidates = {
            entry.symbol: features[entry.symbol]
            for entry in decision.entries
            if entry.symbol in features
        }
        if not candidates:
            return DeliberationResult(
                brief=build(decision, state, features, lessons=lessons), strategy=self.name
            )

        analysts = await self._analysts.run(as_of, candidates, self._kinds, capabilities)
        brief = build(decision, state, features, notes=analysts.notes, lessons=lessons)
        completion, verdicts, error = await self._decide(brief)

        return DeliberationResult(
            verdicts=verdicts,
            brief=brief,
            completions=[completion],
            analysts=analysts,
            strategy=self.name,
            rounds=1,
            parse_error=error,
        )


class MultiRoundDebate(FirmDebate):
    """The paper's shape: the model argues against its own first pass over n rounds."""

    name = "multi_round_debate"

    def __init__(
        self,
        client: AIClient,
        deep: ModelTier,
        analysts: AnalystPool,
        kinds: list[AnalystKind] | None = None,
        rounds: int = 2,
    ) -> None:
        super().__init__(client, deep, analysts, kinds)
        self._rounds = max(1, rounds)

    async def run(
        self,
        as_of: date,
        decision: Decision,
        state: PortfolioState,
        features: dict[str, Features],
        lessons: list[str] | None = None,
        capabilities: frozenset[Capability] = frozenset(),
    ) -> DeliberationResult:
        result = await super().run(as_of, decision, state, features, lessons, capabilities)
        result.strategy = self.name
        if not result.ok or result.brief is None:
            return result

        for _ in range(self._rounds - 1):
            previous = _summarise(result.verdicts)
            completion, verdicts, error = await self._decide(
                result.brief, f"{REBUTTAL}\n\n{previous}"
            )
            result.completions.append(completion)
            result.rounds += 1
            if error is not None or not verdicts:
                result.parse_error = error
                break
            result.verdicts = verdicts

        return result


def _summarise(verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "You returned no verdicts."
    return "\n".join(
        f"{item.symbol}: take={item.take} confidence={item.confidence:.2f} — {item.thesis[:160]}"
        for item in verdicts
    )


STRATEGIES: dict[str, type[DeliberationStrategy]] = {
    SingleCall.name: SingleCall,
    FirmDebate.name: FirmDebate,
    MultiRoundDebate.name: MultiRoundDebate,
}
