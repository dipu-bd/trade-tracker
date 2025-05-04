from abc import ABCMeta, abstractmethod
from functools import cached_property

import requests

from marketbot.context import ServerContext
from marketbot.utils.retry_session import RetrySession


class Crawler(metaclass=ABCMeta):
    _session: requests.Session

    def __init__(self, ctx: ServerContext):
        self._ctx = ctx
        self._session = RetrySession()

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def link(self) -> str:
        pass

    @abstractmethod
    def run(self) -> 'Result':
        pass


class Result:
    def __init__(self, price, change):
        self._price = price
        self._change = change

    @cached_property
    def price(self) -> str:
        price = float(self._price)
        return f"AED {price:,.2f}"

    @cached_property
    def change(self) -> str:
        if self._change is None:
            return ''
        change = float(self._change)
        text = f"{change:,.2f}"
        if change < 0:
            return text
        elif change > 0:
            return f'+{text}'
        else:
            return ''
