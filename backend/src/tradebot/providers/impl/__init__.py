"""Importing this package is what registers the adapters, so it must import every one."""

from tradebot.providers.impl.alpaca import AlpacaProvider
from tradebot.providers.impl.alphavantage import AlphaVantageProvider
from tradebot.providers.impl.binance import BinanceProvider
from tradebot.providers.impl.cryptocom import CryptoComProvider
from tradebot.providers.impl.finnhub import FinnhubProvider
from tradebot.providers.impl.fmp import FmpProvider
from tradebot.providers.impl.polygon import PolygonProvider

__all__ = [
    "AlpacaProvider",
    "AlphaVantageProvider",
    "BinanceProvider",
    "CryptoComProvider",
    "FinnhubProvider",
    "FmpProvider",
    "PolygonProvider",
]
