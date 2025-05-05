from dataclasses import asdict
import json
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
    artifact_data = json.dumps(
        [asdict(result) for result in prices],
        indent=4,
    )
    print(artifact_data)

    with open('gold-price.json', 'w', encoding='utf-8') as f:
        f.write(artifact_data)

    message = ctx.gold.build_slack_message(prices)
    print(message['text'])

    result = ctx.gold.send_slack_alert(message)
    print(result)


if __name__ == '__main__':
    main()
