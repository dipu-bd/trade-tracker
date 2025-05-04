from abc import ABCMeta, abstractmethod
from typing import Generic, TypeVar

import requests

from marketbot.context import ServerContext
from marketbot.utils.retry_session import RetrySession

T = TypeVar('T')


class Crawler(Generic[T], metaclass=ABCMeta):
    _session: requests.Session

    def __init__(self, ctx: ServerContext):
        self._ctx = ctx
        self._session = RetrySession()

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def run(self) -> T:
        pass
