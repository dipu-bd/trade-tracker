from typing import List

from marketbot.context import ServerContext
from marketbot.crawlers import Crawler
from marketbot.dto.gold_price import GoldPriceResult

from .dubaicityofgold import DubaiCityOfGold
from .goldapi_io import GoldAPI
from .goldprice_org import GoldPrice
from .gulfnews import GulfNews
from .igold import IGoldAE
from .mashreq import Mashreq
from .metalpriceapi import MetalpriceAPI

__all__ = [
    'DubaiCityOfGold',
    'GoldAPI',
    'GoldPrice',
    'GulfNews',
    'IGoldAE',
    'Mashreq',
    'MetalpriceAPI',
]

__crawlers: List[Crawler[GoldPriceResult]] = []


def get_crawlers(ctx: ServerContext):
    global __crawlers
    if not __crawlers:
        __crawlers = [
            Mashreq(ctx),
            GulfNews(ctx),
            GoldPrice(ctx),
            IGoldAE(ctx),
            # DubaiCityOfGold(ctx),
            GoldAPI(ctx),
            # MetalpriceAPI(ctx),
        ]
    return __crawlers
