import math
import os
import sys
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from marketbot.dto.market import Bar, Quote, UniverseEntry  # noqa: E402
from marketbot.providers.base import MarketDataProvider  # noqa: E402


# --------------------------------------------------------------------------- #
# Synthetic price series
# --------------------------------------------------------------------------- #

def make_bars(
    count: int = 260,
    start: float = 100.0,
    drift: float = 0.004,
    amplitude: float = 0.02,
    wave_period: float = 9.0,
    base_volume: float = 2_000_000,
    volume_ramp: float = 0.0,
) -> List[Bar]:
    """A trending series with a gentle oscillation.

    The wave keeps RSI in a realistic band; a perfectly smooth ramp would pin
    it at 100 and make every test look overbought.
    """
    bars: List[Bar] = []
    first_day = date.today() - timedelta(days=count)
    for i in range(count):
        trend = start * ((1 + drift) ** i)
        close = trend * (1 + amplitude * math.sin(i / wave_period))
        span = close * 0.012
        volume = base_volume * (1 + volume_ramp * i / max(count - 1, 1))
        bars.append(Bar(
            bar_date=first_day + timedelta(days=i),
            open=close - span * 0.3,
            high=close + span,
            low=close - span,
            close=close,
            volume=volume,
        ))
    return bars


def downtrend_bars(count: int = 260, start: float = 200.0) -> List[Bar]:
    return make_bars(count=count, start=start, drift=-0.004)


def flat_bars(count: int = 260, start: float = 50.0) -> List[Bar]:
    return make_bars(count=count, start=start, drift=0.0, amplitude=0.001)


# --------------------------------------------------------------------------- #
# Fake provider
# --------------------------------------------------------------------------- #

class FakeProvider(MarketDataProvider):
    """Serves canned bars, so no test ever touches the network."""

    def __init__(self, series: Dict[str, List[Bar]], asset_classes: Sequence[str]):
        self.series = series
        self._asset_classes = tuple(asset_classes)
        self.bar_calls: List[str] = []
        self.quote_calls = 0

    @property
    def name(self) -> str:
        return 'fake'

    @property
    def asset_classes(self) -> Sequence[str]:
        return self._asset_classes

    def list_universe(self) -> List[UniverseEntry]:
        return []

    def get_quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        self.quote_calls += 1
        quotes: Dict[str, Quote] = {}
        for symbol in symbols:
            bars = self.series.get(symbol)
            if not bars:
                continue
            quotes[symbol] = Quote(
                symbol=symbol,
                price=bars[-1].close,
                previous_close=bars[-2].close if len(bars) > 1 else bars[-1].close,
                volume=bars[-1].volume,
                avg_volume=sum(b.volume for b in bars[-20:]) / min(len(bars), 20),
            )
        return quotes

    def get_daily_bars(self, symbol: str, days: int = 260) -> List[Bar]:
        self.bar_calls.append(symbol)
        return list(self.series.get(symbol, []))[-days:]


class FakeAdvisor:
    """Scriptable stand-in for the LLM reviewer."""

    def __init__(self, result=None, raises: Optional[Exception] = None):
        self.result = result
        self.raises = raises
        self.briefs: List[dict] = []

    @property
    def provider(self) -> str:
        return 'fake'

    @property
    def available(self) -> bool:
        return True

    def review(self, brief):
        self.briefs.append(brief)
        if self.raises is not None:
            raise self.raises
        return self.result


class FakeMailService:
    def __init__(self):
        self.sent: List[dict] = []

    @property
    def enabled(self) -> bool:
        return True

    def send(self, to: str, subject: str, html_body: str) -> bool:
        self.sent.append({'to': to, 'subject': subject, 'body': html_body})
        return True

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #

class TestContext:
    """A ServerContext work-alike wired to fakes."""

    def __init__(self, db_path: str):
        from marketbot.config import Config
        from marketbot.db import Database
        from marketbot.services import (
            EngineService,
            MarketDataService,
            NotifierService,
            PortfolioService,
        )

        self.config = Config()
        self.db = Database(f'sqlite:///{db_path}')
        self.market_data = MarketDataService(self)
        self.portfolios = PortfolioService(self)
        self.engine = EngineService(self)
        self.mail = FakeMailService()
        self.notifier = NotifierService(self)
        self.advisor = None

    def use_providers(self, equity=None, crypto=None):
        if equity is not None:
            self.market_data._equity = equity
        if crypto is not None:
            self.market_data._crypto = crypto

    def close(self):
        self.db.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep tests deterministic regardless of the developer's own .env."""
    for key in (
        'FMP_API_KEY', 'SMTP_ENABLED', 'LLM_ADVISOR_MODE',
        'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'NOTIFY_EMAIL',
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SMTP_ENABLED', 'false')
    monkeypatch.setenv('LLM_ADVISOR_MODE', 'off')
    monkeypatch.setenv('NOTIFY_EMAIL', 'owner@example.com')


@pytest.fixture
def ctx(tmp_path):
    context = TestContext(str(tmp_path / 'test.db'))
    yield context
    context.close()


@pytest.fixture
def portfolio(ctx):
    with ctx.db.session() as session:
        created = ctx.portfolios.create(
            session,
            name='Test Book',
            initial_capital=100_000.0,
            risk_pct_per_trade=1.0,
            max_positions=5,
            max_position_pct=30.0,
            entry_score=20.0,
            etf_entry_score=20.0,
            exit_score=10.0,
            crypto_max_pct=30.0,
            notify_email='owner@example.com',
        )
        return created.id
