import logging
import os
from functools import cached_property

import dotenv


def env(key, default_value=None):
    value = os.getenv(key, default_value)
    if value is None:
        raise Exception(f'Missing required ENV: {key}')
    return value


def env_opt(key, default_value=''):
    """Like `env`, but never raises — returns the default when unset or blank."""
    value = os.getenv(key)
    if value is None or not value.strip():
        return default_value
    return value.strip()


def env_int(key, default_value):
    try:
        return int(env_opt(key, str(default_value)))
    except ValueError:
        return default_value


def env_float(key, default_value):
    try:
        return float(env_opt(key, str(default_value)))
    except ValueError:
        return default_value


def env_bool(key, default_value=False):
    raw = env_opt(key, 'true' if default_value else 'false').lower()
    return raw in ('1', 'true', 'yes', 'on')


class ServerConfig:
    @cached_property
    def api_token(self) -> str:
        return env('SERVER_API_TOKEN')


class GoldConfig:
    @cached_property
    def slack_webhook_url(self) -> str:
        return env('SLACK_WEBHOOK_URL')

    @cached_property
    def slack_signing_secret(self) -> str:
        return env('SLACK_SIGNING_SECRET')

    @cached_property
    def goldapi_token(self) -> str:
        return env('GOLDAPI_TOKEN')

    @cached_property
    def metalprice_token(self) -> str:
        return env('METALPRICE_API_TOKEN')

    @property
    def xau_gram(self) -> float:
        return 31.10347680  # source: wikipedia


class DatabaseConfig:
    @cached_property
    def url(self) -> str:
        return env_opt('MARKETBOT_DB_URL', 'sqlite:///./marketbot.db')

    @cached_property
    def echo(self) -> bool:
        return env_bool('MARKETBOT_DB_ECHO', False)


class MarketConfig:
    @cached_property
    def fmp_api_key(self) -> str:
        return env_opt('FMP_API_KEY')

    @cached_property
    def fmp_daily_budget(self) -> int:
        """Free-tier plans allow ~250 calls/day; stay under it."""
        return env_int('FMP_DAILY_REQUEST_BUDGET', 240)

    @cached_property
    def crypto_quote_currency(self) -> str:
        return env_opt('CRYPTO_QUOTE_CURRENCY', 'USD').upper()

    @cached_property
    def universe_max_symbols(self) -> int:
        return env_int('UNIVERSE_MAX_SYMBOLS', 140)

    @cached_property
    def bar_history_days(self) -> int:
        return env_int('BAR_HISTORY_DAYS', 260)


class MailConfig:
    """SMTP settings, modelled on the lightnovel-crawler mail service.

    Defaults target a locally running ProtonMail Bridge, which listens on
    127.0.0.1:1025 and presents a self-signed certificate — hence
    `tls_verify` defaulting to false.
    """

    @cached_property
    def smtp_enabled(self) -> bool:
        return env_bool('SMTP_ENABLED', False)

    @cached_property
    def smtp_server(self) -> str:
        return env_opt('SMTP_SERVER', '127.0.0.1')

    @cached_property
    def smtp_port(self) -> int:
        return env_int('SMTP_PORT', 1025)

    @cached_property
    def smtp_username(self) -> str:
        return env_opt('SMTP_USERNAME')

    @cached_property
    def smtp_password(self) -> str:
        return env_opt('SMTP_PASSWORD')

    @cached_property
    def smtp_sender(self) -> str:
        return env_opt('SMTP_SENDER') or self.smtp_username

    @cached_property
    def smtp_starttls(self) -> bool:
        return env_bool('SMTP_STARTTLS', True)

    @cached_property
    def smtp_tls_verify(self) -> bool:
        return env_bool('SMTP_TLS_VERIFY', False)

    @cached_property
    def notify_mode(self) -> str:
        """`per_run` (one mail per scan) or `per_event` (one mail per change)."""
        mode = env_opt('NOTIFY_MODE', 'per_run').lower()
        return mode if mode in ('per_run', 'per_event') else 'per_run'

    @cached_property
    def notify_email(self) -> str:
        return env_opt('NOTIFY_EMAIL')


class SchedulerConfig:
    @cached_property
    def enabled(self) -> bool:
        return env_bool('SCHEDULER_ENABLED', True)

    @cached_property
    def preopen_cron(self) -> str:
        return env_opt('SCAN_CRON_PREOPEN', '15 13 * * 1-5')

    @cached_property
    def main_cron(self) -> str:
        return env_opt('SCAN_CRON_MAIN', '45 19 * * 1-5')

    @cached_property
    def crypto_cron(self) -> str:
        return env_opt('SCAN_CRON_CRYPTO', '0 */4 * * *')

    @cached_property
    def digest_cron(self) -> str:
        return env_opt('DIGEST_CRON', '30 21 * * *')


class AdvisorConfig:
    """Optional LLM second opinion on the deterministic action set."""

    @cached_property
    def mode(self) -> str:
        mode = env_opt('LLM_ADVISOR_MODE', 'off').lower()
        return mode if mode in ('off', 'annotate', 'veto') else 'off'

    @property
    def enabled(self) -> bool:
        return self.mode != 'off'

    @cached_property
    def provider(self) -> str:
        provider = env_opt('LLM_PROVIDER', 'anthropic').lower()
        return provider if provider in ('anthropic', 'openai') else 'anthropic'

    @cached_property
    def model(self) -> str:
        configured = env_opt('LLM_MODEL')
        if configured:
            return configured
        return 'claude-opus-5' if self.provider == 'anthropic' else 'gpt-4o-mini'

    @cached_property
    def base_url(self) -> str:
        """Any OpenAI-compatible endpoint (OpenRouter, Ollama, vLLM, Groq)."""
        return env_opt('LLM_BASE_URL')

    @cached_property
    def api_key(self) -> str:
        if self.provider == 'anthropic':
            return env_opt('ANTHROPIC_API_KEY')
        return env_opt('OPENAI_API_KEY')

    @cached_property
    def timeout(self) -> float:
        return env_float('LLM_TIMEOUT', 60)


class TradingConfig:
    """Defaults for a newly created portfolio.

    Every one of these is stored per-portfolio in the database; these values
    only seed the form when a field is omitted at creation time.
    """

    @cached_property
    def initial_capital(self) -> float:
        return env_float('DEFAULT_INITIAL_CAPITAL', 10_000)

    @cached_property
    def risk_pct_per_trade(self) -> float:
        return env_float('DEFAULT_RISK_PCT_PER_TRADE', 1.0)

    @cached_property
    def max_positions(self) -> int:
        return env_int('DEFAULT_MAX_POSITIONS', 8)

    @cached_property
    def max_position_pct(self) -> float:
        return env_float('DEFAULT_MAX_POSITION_PCT', 25.0)

    @cached_property
    def daily_loss_pct(self) -> float:
        return env_float('DEFAULT_DAILY_LOSS_PCT', 6.0)

    @cached_property
    def crypto_max_pct(self) -> float:
        return env_float('DEFAULT_CRYPTO_MAX_PCT', 30.0)


class Config:
    def __init__(self) -> None:
        dotenv.load_dotenv()
        logging.basicConfig(level=logging.INFO)

    @cached_property
    def server(self):
        return ServerConfig()

    @cached_property
    def gold(self):
        return GoldConfig()

    @cached_property
    def database(self):
        return DatabaseConfig()

    @cached_property
    def market(self):
        return MarketConfig()

    @cached_property
    def mail(self):
        return MailConfig()

    @cached_property
    def scheduler(self):
        return SchedulerConfig()

    @cached_property
    def advisor(self):
        return AdvisorConfig()

    @cached_property
    def trading(self):
        return TradingConfig()
