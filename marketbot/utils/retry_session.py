import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup


class RetrySession(requests.Session):
    def __init__(
        self,
        retries=3,
        timeout=30,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 504),
        allowed_methods=["HEAD", "GET", "POST", "PUT"],
    ):
        super().__init__()
        self._timeout = timeout

        # Set up retry strategy
        retry = Retry(
            total=retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
            allowed_methods=allowed_methods,
        )

        adapter = HTTPAdapter(max_retries=retry)
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        resp = super().request(method, url, **kwargs)
        resp.raise_for_status()
        return resp

    def get_soup(self, url, **kwargs):
        resp = self.request('GET', url, **kwargs)
        return BeautifulSoup(resp.content, 'lxml')
