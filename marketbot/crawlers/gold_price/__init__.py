from typing import List

from marketbot.context import ServerContext
from marketbot.crawlers import Crawler
from marketbot.dto.gold_price import GoldPriceResult

from .goldapi_io import GoldAPI
from .goldprice_org import GoldPrice
from .gulfnews import GulfNews
from .igold import IGoldAE
from .metalpriceapi import MetalpriceAPI

__crawlers: List[Crawler[GoldPriceResult]] = []


def get_crawlers(ctx: ServerContext):
    global __crawlers
    if not __crawlers:
        __crawlers = [
            GoldAPI(ctx),
            GoldPrice(ctx),
            GulfNews(ctx),
            IGoldAE(ctx),
            MetalpriceAPI(ctx),
        ]
    return __crawlers
