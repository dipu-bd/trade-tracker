from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from tradebot.providers.base import (
    AssetClass,
    Bar,
    Capability,
    CorporateAction,
    CredentialField,
    NewsItem,
    ProviderError,
    Quote,
    RateLimits,
    SplitKind,
    SupportsBars,
    SupportsCorporateActions,
    SupportsNews,
    SupportsQuotes,
    SupportsUniverse,
    UniverseEntry,
)
from tradebot.providers.http import HttpProviderMixin
from tradebot.providers.registry import register

QUOTE_BATCH = 50
MIN_PRICE = Decimal("3")
MIN_DOLLAR_VOLUME = Decimal("5000000")

BATCH_QUOTE_PATH = "/stable/batch-quote"
SCREENER_PATH = "/stable/company-screener"

# Volume-ranked only. A biggest-gainers list selects on one-day return, which is the short-term
# reversal effect rather than the 12-1 momentum the strategy trades.
ACTIVITY_PATHS = ("/stable/most-actives",)

# A liquid large-cap core, so the ranked universe does not depend on a paid screener entitlement
# and does not drift toward whatever is being pumped today.
CORE_STOCKS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "AVGO",
    "TSLA",
    "BRK-B",
    "LLY",
    "JPM",
    "V",
    "UNH",
    "XOM",
    "MA",
    "COST",
    "HD",
    "PG",
    "JNJ",
    "ABBV",
    "WMT",
    "NFLX",
    "BAC",
    "CRM",
    "ORCL",
    "CVX",
    "KO",
    "AMD",
    "PEP",
    "TMO",
    "LIN",
    "ADBE",
    "MRK",
    "CSCO",
    "ACN",
    "MCD",
    "ABT",
    "PM",
    "DHR",
    "TXN",
    "INTU",
    "VZ",
    "IBM",
    "QCOM",
    "CMCSA",
    "NOW",
    "GE",
    "NKE",
    "CAT",
    "AMGN",
    "RTX",
    "UBER",
    "PFE",
    "SPGI",
    "UNP",
    "LOW",
    "BKNG",
    "HON",
    "COP",
    "ELV",
    "DE",
    "PLD",
    "SYK",
    "LMT",
    "BLK",
    "MDT",
    "SBUX",
    "GILD",
    "ADI",
    "MU",
    "INTC",
    "ISRG",
    "TJX",
    "CB",
    "REGN",
    "PGR",
    "VRTX",
    "ETN",
    "SLB",
    "BSX",
    "ZTS",
    "EOG",
    "CI",
    "SO",
    "MMC",
    "PANW",
    "KLAC",
    "CME",
    "DUK",
    "SHW",
    "ITW",
    "AON",
    "APD",
    "CL",
    "MCO",
    "MSI",
    "FDX",
    "NSC",
    "EMR",
]

# Liquid, long-lived core ETFs. Hardcoded because listing ETFs is a paid entitlement and this
# set barely changes; the screener supersedes it when the plan allows.
CORE_ETFS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
    "DIA": "Dow 30",
    "VTI": "Total Market",
    "EFA": "Developed ex-US",
    "EEM": "Emerging Markets",
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "GLD": "Gold",
    "SLV": "Silver",
    "TLT": "20+ Year Treasury",
    "HYG": "High Yield",
}

COMMODITY_SYMBOLS = {
    "GCUSD": "Gold",
    "SIUSD": "Silver",
    "CLUSD": "Crude Oil",
    "NGUSD": "Natural Gas",
}


@register
class FmpProvider(
    HttpProviderMixin,
    SupportsUniverse,
    SupportsQuotes,
    SupportsBars,
    SupportsNews,
    SupportsCorporateActions,
):
    """Financial Modeling Prep. The broadest single source here: equities, ETFs, commodities,
    fundamentals-driven screening, news, and corporate actions."""

    key = "fmp"
    label = "Financial Modeling Prep"
    base_url = "https://financialmodelingprep.com"
    capabilities = frozenset(
        {
            Capability.UNIVERSE,
            Capability.QUOTES,
            Capability.BARS,
            Capability.NEWS,
            Capability.CORPORATE_ACTIONS,
        }
    )
    asset_classes = frozenset({AssetClass.STOCK, AssetClass.ETF, AssetClass.COMMODITY})
    credential_fields = (CredentialField(name="api_key", label="API key"),)
    rate_limits = RateLimits(requests_per_minute=300, requests_per_day=0, max_concurrency=4)
    default_priority = 20

    async def _get(self, path: str, **params: Any) -> Any:
        params["apikey"] = self.config.credentials.get("api_key", "")
        return await self.get_json(path, params=params)

    async def company_names(self, symbols: Iterable[str]) -> dict[str, str]:
        """One request per symbol: this plan has no batch profile endpoint."""
        found: dict[str, str] = {}
        for symbol in symbols:
            try:
                rows = await self._get("/stable/profile", symbol=symbol.upper())
            except ProviderError:
                continue
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                name = str(rows[0].get("companyName") or "").strip()
                if name:
                    found[symbol.upper()] = name
        return found

    async def list_universe(self, asset_class: AssetClass) -> list[UniverseEntry]:
        if asset_class is AssetClass.COMMODITY:
            return [
                UniverseEntry(
                    symbol=symbol,
                    asset_class=AssetClass.COMMODITY,
                    name=name,
                    exchange="COMMODITY",
                    sector="commodity",
                )
                for symbol, name in COMMODITY_SYMBOLS.items()
            ]

        if asset_class is AssetClass.ETF:
            return await self._etf_universe()
        if asset_class is AssetClass.STOCK:
            return await self._stock_universe()
        return []

    async def _stock_universe(self) -> list[UniverseEntry]:
        if self.denied(SCREENER_PATH):
            return await self._activity_universe()
        try:
            rows = await self._get(
                SCREENER_PATH,
                marketCapMoreThan=2_000_000_000,
                priceMoreThan=int(MIN_PRICE),
                volumeMoreThan=500_000,
                isActivelyTrading="true",
                isEtf="false",
                isFund="false",
                exchange="NASDAQ,NYSE",
                limit=1000,
            )
        except ProviderError:
            # Screening is a higher-tier entitlement; the activity lists are on every plan.
            return await self._activity_universe()

        if not isinstance(rows, list):
            raise ProviderError(self.key, "unexpected screener shape")

        entries: list[UniverseEntry] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).upper()
            price = _decimal(row.get("price"))
            volume = _decimal(row.get("volume"))
            if not symbol or price is None or volume is None:
                continue
            dollar_volume = price * volume
            if dollar_volume < MIN_DOLLAR_VOLUME:
                continue
            entries.append(
                UniverseEntry(
                    symbol=symbol,
                    asset_class=AssetClass.STOCK,
                    name=str(row.get("companyName", "")),
                    exchange=str(row.get("exchangeShortName", "")),
                    sector=str(row.get("sector", "")),
                    dollar_volume=dollar_volume,
                )
            )
        entries.sort(key=lambda e: e.dollar_volume or Decimal(0), reverse=True)
        return entries

    async def _activity_universe(self) -> list[UniverseEntry]:
        """The large-cap core, plus whatever is genuinely most-traded today."""
        seen: dict[str, UniverseEntry] = {
            symbol: UniverseEntry(symbol=symbol, asset_class=AssetClass.STOCK)
            for symbol in CORE_STOCKS
        }
        for path in ACTIVITY_PATHS:
            try:
                rows = await self._get(path)
            except ProviderError:
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol", "")).upper()
                price = _decimal(row.get("price"))
                if not symbol or symbol in seen or price is None or price < MIN_PRICE:
                    continue
                seen[symbol] = UniverseEntry(
                    symbol=symbol,
                    asset_class=AssetClass.STOCK,
                    name=str(row.get("name", "")),
                    exchange=str(row.get("exchange", "")),
                )
        return list(seen.values())

    async def _etf_universe(self) -> list[UniverseEntry]:
        if self.denied(SCREENER_PATH):
            return _core_etf_universe()
        try:
            rows = await self._get(
                SCREENER_PATH,
                priceMoreThan=int(MIN_PRICE),
                volumeMoreThan=500_000,
                isActivelyTrading="true",
                isEtf="true",
                limit=500,
            )
        except ProviderError:
            return _core_etf_universe()

        if not isinstance(rows, list):
            raise ProviderError(self.key, "unexpected ETF screener shape")

        entries: list[UniverseEntry] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).upper()
            price = _decimal(row.get("price"))
            volume = _decimal(row.get("volume"))
            if not symbol or price is None or volume is None:
                continue
            entries.append(
                UniverseEntry(
                    symbol=symbol,
                    asset_class=AssetClass.ETF,
                    name=str(row.get("companyName", "")),
                    exchange=str(row.get("exchangeShortName", "")),
                    sector="etf",
                    dollar_volume=price * volume,
                )
            )
        entries.sort(key=lambda e: e.dollar_volume or Decimal(0), reverse=True)
        return entries

    async def get_quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        wanted = [s.upper() for s in symbols]
        if not wanted:
            return {}

        if not self.denied(BATCH_QUOTE_PATH):
            try:
                return await self._batch_quotes(wanted)
            except ProviderError:
                # Batch quoting is a higher-tier entitlement; per-symbol works on every plan.
                pass
        return await self._single_quotes(wanted)

    async def _batch_quotes(self, wanted: list[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        for start in range(0, len(wanted), QUOTE_BATCH):
            batch = wanted[start : start + QUOTE_BATCH]
            rows = await self._get(BATCH_QUOTE_PATH, symbols=",".join(batch))
            if not isinstance(rows, list):
                raise ProviderError(self.key, "unexpected batch-quote shape")
            for row in rows:
                quote = _parse_quote(row)
                if quote is not None:
                    quotes[quote.symbol] = quote
        return quotes

    async def _single_quotes(self, wanted: list[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        for symbol in wanted:
            rows = await self._get("/stable/quote", symbol=symbol)
            if not isinstance(rows, list):
                continue
            for row in rows:
                quote = _parse_quote(row)
                if quote is not None:
                    quotes[quote.symbol] = quote
        return quotes

    async def get_bars(self, symbol: str, *, days: int = 260, end: date | None = None) -> list[Bar]:
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if end is not None:
            params["to"] = end.isoformat()

        rows = await self._get("/stable/historical-price-eod/full", **params)
        if not isinstance(rows, list):
            raise ProviderError(self.key, f"unexpected historical shape for {symbol}")

        bars: list[Bar] = []
        for row in rows:
            bar = _parse_bar(symbol.upper(), row)
            if bar is not None and (end is None or bar.bar_date <= end):
                bars.append(bar)
        bars.sort(key=lambda b: b.bar_date)
        return bars[-days:]

    async def get_news(self, symbols: Iterable[str], *, limit: int = 20) -> list[NewsItem]:
        wanted = [s.upper() for s in symbols]
        if not wanted:
            return []

        rows = await self._get(
            "/stable/news/stock", symbols=",".join(wanted[:QUOTE_BATCH]), limit=limit
        )
        if not isinstance(rows, list):
            raise ProviderError(self.key, "unexpected news shape")

        items: list[NewsItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            published = _timestamp(row.get("publishedDate"))
            headline = str(row.get("title", "")).strip()
            if published is None or not headline:
                continue
            items.append(
                NewsItem(
                    symbol=str(row.get("symbol", "")).upper(),
                    headline=headline,
                    published_at=published,
                    source=str(row.get("publisher", "")),
                    url=str(row.get("url", "")),
                    summary=str(row.get("text", ""))[:1000],
                )
            )
        return items

    async def get_corporate_actions(
        self, symbol: str, *, since: date | None = None
    ) -> list[CorporateAction]:
        actions: list[CorporateAction] = []
        actions.extend(await self._splits(symbol.upper(), since))
        actions.extend(await self._dividends(symbol.upper(), since))
        actions.sort(key=lambda a: a.effective_date)
        return actions

    async def _splits(self, symbol: str, since: date | None) -> list[CorporateAction]:
        rows = await self._get("/stable/splits", symbol=symbol)
        if not isinstance(rows, list):
            return []

        actions: list[CorporateAction] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            effective = _parse_date(row.get("date"))
            numerator = _decimal(row.get("numerator"))
            denominator = _decimal(row.get("denominator"))
            if effective is None or not numerator or not denominator or denominator == 0:
                continue
            if since is not None and effective < since:
                continue
            actions.append(
                CorporateAction(
                    symbol=symbol,
                    effective_date=effective,
                    kind=SplitKind.SPLIT,
                    split_ratio=numerator / denominator,
                )
            )
        return actions

    async def _dividends(self, symbol: str, since: date | None) -> list[CorporateAction]:
        rows = await self._get("/stable/dividends", symbol=symbol)
        if not isinstance(rows, list):
            return []

        actions: list[CorporateAction] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            effective = _parse_date(row.get("date"))
            amount = _decimal(row.get("adjDividend") or row.get("dividend"))
            if effective is None or amount is None or amount <= 0:
                continue
            if since is not None and effective < since:
                continue
            actions.append(
                CorporateAction(
                    symbol=symbol,
                    effective_date=effective,
                    kind=SplitKind.DIVIDEND,
                    cash_amount=amount,
                )
            )
        return actions


def _core_etf_universe() -> list[UniverseEntry]:
    return [
        UniverseEntry(
            symbol=symbol,
            asset_class=AssetClass.ETF,
            name=name,
            exchange="ARCA",
            sector="etf",
        )
        for symbol, name in CORE_ETFS.items()
    ]


def _parse_quote(row: Any) -> Quote | None:
    if not isinstance(row, dict):
        return None
    symbol = str(row.get("symbol", "")).upper()
    price = _decimal(row.get("price"))
    if not symbol or price is None or price <= 0:
        return None

    change = _decimal(row.get("change"))
    previous = _decimal(row.get("previousClose"))
    if previous is None and change is not None:
        previous = price - change

    return Quote(
        symbol=symbol,
        price=price,
        at=_timestamp(row.get("timestamp")) or datetime.now(UTC),
        previous_close=previous if previous and previous > 0 else None,
        volume=_decimal(row.get("volume")),
    )


def _parse_bar(symbol: str, row: Any) -> Bar | None:
    if not isinstance(row, dict):
        return None
    bar_date = _parse_date(row.get("date"))
    values = [_decimal(row.get(field)) for field in ("open", "high", "low", "close", "volume")]
    if bar_date is None or any(value is None for value in values):
        return None
    open_, high, low, close, volume = values
    return Bar(
        symbol=symbol,
        bar_date=bar_date,
        open=open_,  # type: ignore[arg-type]
        high=high,  # type: ignore[arg-type]
        low=low,  # type: ignore[arg-type]
        close=close,  # type: ignore[arg-type]
        volume=volume,  # type: ignore[arg-type]
    )


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=UTC)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None
