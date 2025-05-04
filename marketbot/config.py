import logging
import os
from functools import cached_property

import dotenv


def env(key, default_value=None):
    value = os.getenv(key, default_value)
    if value is None:
        raise Exception(f'Missing required ENV: {key}')
    return value


class ServerConfig:
    @cached_property
    def api_token(self) -> str:
        return env('SERVER_API_TOKEN')


class GoldConfig:
    @cached_property
    def slack_webhook_url(self) -> str:
        return env('SLACK_WEBHOOK_URL')

    @cached_property
    def slack_signing_secret(self) -> str:
        return env('SLACK_SIGNING_SECRET')

    @cached_property
    def goldapi_token(self) -> str:
        return env('GOLDAPI_TOKEN')

    @cached_property
    def metalprice_token(self) -> str:
        return env('METALPRICE_API_TOKEN')

    @property
    def xau_gram(self) -> float:
        return 31.10347680  # source: wikipedia


class Config:
    def __init__(self) -> None:
        dotenv.load_dotenv()
        logging.basicConfig(level=logging.INFO)

    @cached_property
    def server(self):
        return ServerConfig()

    @cached_property
    def gold(self):
        return GoldConfig()
