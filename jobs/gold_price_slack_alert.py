import logging
import os
import sys

logging.basicConfig(level=logging.DEBUG)

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)


def main():
    from marketbot.context import ServerContext
    ctx = ServerContext()
    prices = ctx.gold.get_latest_gold_prices()
    message = ctx.gold.build_slack_message(prices)
    ctx.gold.send_slack_alert(message)


if __name__ == '__main__':
    main()
