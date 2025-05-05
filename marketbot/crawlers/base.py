import logging
from abc import ABCMeta, abstractmethod
from typing import Generic, TypeVar
from urllib.error import URLError

from requests.exceptions import RequestException
from urllib3.exceptions import HTTPError

from marketbot.context import ServerContext
from marketbot.utils.retry_session import RetrySession

T = TypeVar('T')


_log = logging.getLogger(__name__)

ScraperErrorGroup = (
    URLError,
    HTTPError,
    RequestException,
)


class Crawler(Generic[T], metaclass=ABCMeta):
    _session: RetrySession

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
