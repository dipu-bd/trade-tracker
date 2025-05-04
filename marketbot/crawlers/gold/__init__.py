from typing import List, Type

from marketbot.crawlers import Crawler
from marketbot.dto.gold_price import GoldPriceResult

from .goldapi_io import GoldAPI
from .goldprice_org import GoldPrice
from .gulfnews import GulfNews
from .igold import IGoldAE
from .metalpriceapi import MetalpriceAPI

crawlers: List[Type[Crawler[GoldPriceResult]]] = [
    GoldAPI,
    GoldPrice,
    GulfNews,
    IGoldAE,
    MetalpriceAPI,
]
