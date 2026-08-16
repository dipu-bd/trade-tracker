import hashlib
import json
from dataclasses import asdict, fields, is_dataclass
from typing import Any

from tradebot.engine.strategy import StrategyConfig

SECTIONS = ("screen", "sizing", "regime", "exits", "turnover", "costs")


def fingerprint(config: StrategyConfig) -> str:
    """A stable id for one configuration.

    The trial count behind a deflated Sharpe has to be counted by the machine. Asking a human to
    remember how many variants they tried produces the number that flatters the result, which is
    precisely the bias deflation exists to remove.
    """
    payload = json.dumps(_plain(config), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _plain(config: StrategyConfig) -> dict[str, Any]:
    out: dict[str, Any] = {"benchmark": config.benchmark}
    for name in SECTIONS:
        section = getattr(config, name)
        if is_dataclass(section) and not isinstance(section, type):
            out[name] = {
                key: sorted(value) if isinstance(value, frozenset) else value
                for key, value in asdict(section).items()
            }
    return out


def parameter_count(config: StrategyConfig) -> int:
    """Every knob is a degree of freedom, whether or not it was moved."""
    total = 0
    for name in SECTIONS:
        section = getattr(config, name)
        if is_dataclass(section) and not isinstance(section, type):
            total += len(fields(section))
    return total


class TrialLedger:
    """Counts distinct configurations evaluated, so deflation cannot be quietly understated.

    Counts *distinct* rather than total runs: re-running the same configuration is not another
    chance to get lucky, but changing one threshold is.
    """

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def record(self, config: StrategyConfig) -> str:
        key = fingerprint(config)
        self._seen[key] = self._seen.get(key, 0) + 1
        return key

    @property
    def trials(self) -> int:
        return max(1, len(self._seen))

    @property
    def runs(self) -> int:
        return sum(self._seen.values())

    def as_dict(self) -> dict[str, int]:
        return {"distinct_configurations": self.trials, "total_runs": self.runs}
