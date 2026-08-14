"""The tradable universe.

Individual names are a curated liquid list rather than a full exchange dump:
on a free data plan the request budget is the binding constraint, and a few
dozen liquid names give the scanner more than enough to choose from. Screener
results are merged on top when the data plan exposes them.
"""

from typing import Dict, List

from marketbot.dto.market import UniverseEntry

# --------------------------------------------------------------------------- #
# ETFs — the slower trend core. Broad market, sectors, and a few thematics.
# --------------------------------------------------------------------------- #

CORE_ETFS: Dict[str, str] = {
    'SPY': 'Broad Market',
    'QQQ': 'Broad Market',
    'IWM': 'Broad Market',
    'DIA': 'Broad Market',
    'MDY': 'Broad Market',
    'XLK': 'Technology',
    'XLF': 'Financials',
    'XLE': 'Energy',
    'XLV': 'Healthcare',
    'XLI': 'Industrials',
    'XLY': 'Consumer Discretionary',
    'XLP': 'Consumer Staples',
    'XLU': 'Utilities',
    'XLB': 'Materials',
    'XLRE': 'Real Estate',
    'XLC': 'Communications',
    'SMH': 'Semiconductors',
    'SOXX': 'Semiconductors',
    'ARKK': 'Thematic',
    'IBIT': 'Crypto Proxy',
    'GLD': 'Commodities',
    'SLV': 'Commodities',
    'TLT': 'Bonds',
    'HYG': 'Bonds',
    'EEM': 'International',
    'EFA': 'International',
    'VNQ': 'Real Estate',
    'IYT': 'Transport',
}

# --------------------------------------------------------------------------- #
# Individual names — liquid, optionable, and volatile enough to pay.
# --------------------------------------------------------------------------- #

LIQUID_STOCKS: Dict[str, str] = {
    # Technology
    'AAPL': 'Technology', 'MSFT': 'Technology', 'GOOGL': 'Technology',
    'META': 'Technology', 'ORCL': 'Technology', 'CRM': 'Technology',
    'ADBE': 'Technology', 'NOW': 'Technology', 'PLTR': 'Technology',
    'SNOW': 'Technology', 'PANW': 'Technology', 'CRWD': 'Technology',
    'DDOG': 'Technology', 'NET': 'Technology', 'SHOP': 'Technology',
    'UBER': 'Technology', 'ABNB': 'Technology', 'SQ': 'Technology',
    'COIN': 'Technology', 'HOOD': 'Technology', 'SNAP': 'Technology',
    'RBLX': 'Technology', 'ZS': 'Technology', 'MDB': 'Technology',
    # Semiconductors
    'NVDA': 'Semiconductors', 'AMD': 'Semiconductors', 'AVGO': 'Semiconductors',
    'INTC': 'Semiconductors', 'MU': 'Semiconductors', 'QCOM': 'Semiconductors',
    'TSM': 'Semiconductors', 'SMCI': 'Semiconductors', 'ARM': 'Semiconductors',
    'MRVL': 'Semiconductors', 'LRCX': 'Semiconductors', 'AMAT': 'Semiconductors',
    'TXN': 'Semiconductors', 'ON': 'Semiconductors',
    # Consumer
    'AMZN': 'Consumer Discretionary', 'TSLA': 'Consumer Discretionary',
    'HD': 'Consumer Discretionary', 'NKE': 'Consumer Discretionary',
    'SBUX': 'Consumer Discretionary', 'MCD': 'Consumer Discretionary',
    'LULU': 'Consumer Discretionary', 'CMG': 'Consumer Discretionary',
    'DKNG': 'Consumer Discretionary', 'CVNA': 'Consumer Discretionary',
    'WMT': 'Consumer Staples', 'COST': 'Consumer Staples',
    'PG': 'Consumer Staples', 'KO': 'Consumer Staples', 'PEP': 'Consumer Staples',
    # Financials
    'JPM': 'Financials', 'BAC': 'Financials', 'GS': 'Financials',
    'MS': 'Financials', 'WFC': 'Financials', 'SCHW': 'Financials',
    'V': 'Financials', 'MA': 'Financials', 'PYPL': 'Financials',
    'AXP': 'Financials', 'BLK': 'Financials',
    # Healthcare
    'UNH': 'Healthcare', 'JNJ': 'Healthcare', 'LLY': 'Healthcare',
    'PFE': 'Healthcare', 'MRK': 'Healthcare', 'ABBV': 'Healthcare',
    'TMO': 'Healthcare', 'ISRG': 'Healthcare', 'VRTX': 'Healthcare',
    'MRNA': 'Healthcare',
    # Energy & industrials
    'XOM': 'Energy', 'CVX': 'Energy', 'COP': 'Energy', 'SLB': 'Energy',
    'OXY': 'Energy', 'FSLR': 'Energy', 'ENPH': 'Energy',
    'CAT': 'Industrials', 'DE': 'Industrials', 'BA': 'Industrials',
    'GE': 'Industrials', 'LMT': 'Industrials', 'RTX': 'Industrials',
    'UPS': 'Industrials', 'UNP': 'Industrials',
    # Communications & other
    'NFLX': 'Communications', 'DIS': 'Communications', 'T': 'Communications',
    'VZ': 'Communications', 'CMCSA': 'Communications',
    'LIN': 'Materials', 'FCX': 'Materials', 'NEM': 'Materials',
    'NEE': 'Utilities', 'DUK': 'Utilities',
}


def static_equity_universe(
    include_stocks: bool = True,
    include_etfs: bool = True,
) -> List[UniverseEntry]:
    entries: List[UniverseEntry] = []
    if include_stocks:
        entries.extend(
            UniverseEntry(symbol=symbol, asset_class='STOCK', sector=sector)
            for symbol, sector in LIQUID_STOCKS.items()
        )
    if include_etfs:
        entries.extend(
            UniverseEntry(symbol=symbol, asset_class='ETF', sector=sector)
            for symbol, sector in CORE_ETFS.items()
        )
    return entries


def merge_universes(*groups: List[UniverseEntry]) -> List[UniverseEntry]:
    """Combine entries, first occurrence of a symbol wins."""
    merged: Dict[str, UniverseEntry] = {}
    for group in groups:
        for entry in group:
            key = entry.symbol.upper()
            if key in merged:
                # Keep whatever sector we already know; screeners rarely say.
                if not merged[key].sector and entry.sector:
                    merged[key].sector = entry.sector
                continue
            merged[key] = entry
    return list(merged.values())


def sector_for(symbol: str) -> str:
    symbol = symbol.upper()
    return LIQUID_STOCKS.get(symbol) or CORE_ETFS.get(symbol) or ''
