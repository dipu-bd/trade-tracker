import os
import json
import dotenv
from concurrent.futures import ThreadPoolExecutor
from requests import Session, post
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

XAU_GRAM = 31.10347680  # source: wikipedia

dotenv.load_dotenv()
GOLDAPI_TOKEN = os.getenv('GOLDAPI_TOKEN')
METALPRICE_API_TOKEN = os.getenv('METALPRICE_API_TOKEN')
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')


#####################################################################
#                              Crawlers                             #
#####################################################################

session = Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=3)))


def goldapi_crawler():
    resp = session.get(
        'https://www.goldapi.io/api/XAU/AED',
        headers={
            'x-access-token': GOLDAPI_TOKEN,
        }
    )
    data = resp.json()
    return (data['price'], data['ch'])


def goldprice_crawler():
    resp = session.get(
        'https://data-asg.goldprice.org/dbXRates/AED',
        headers={
            'referer': 'https://goldprice.org/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
        }
    )
    data = resp.json()
    item = data['items'][0]
    return (item['xauPrice'], item['chgXau'])


def metalpriceapi_crawler():
    resp = session.get(
        'https://api.metalpriceapi.com/v1/latest',
        params={
            'api_key': METALPRICE_API_TOKEN,
            'base': 'AED',
            'currencies': 'XAU'
        }
    )
    data = resp.json()
    return (data['rates']['AEDXAU'], None)


def gulfnews_crawler():
    resp = session.get(
        'https://gulfnews.com/gold-forex/historical-gold-rates'
    )
    soup = BeautifulSoup(resp.content, 'lxml')

    tds = [td.text for td in soup.select('#container table tr td')]
    today = float(tds[1]) * XAU_GRAM
    yesterday = float(tds[6]) * XAU_GRAM
    change = today - yesterday

    return (today, change)


def igold_ae_crawler():
    resp = session.get(
        'https://igold.ae/prices/ajax',
        params={
            'url': 'get-graph-points',
            'metal': 'XAU',
            'currency': 'AED',
            'range': '10m',
        }
    )
    data = resp.json()

    last = data['last'][1]
    first = data['data'][0][1]
    change = last - first

    return (last, change)


# -------------------------------------------------------------------#
crawlers = [
    {
        'name': 'GoldPrice.org',
        'crawler': goldprice_crawler,
        'link': 'https://goldprice.org/',
    },
    {
        'name': 'Gulf News',
        'crawler': gulfnews_crawler,
        'link': 'https://gulfnews.com/gold-forex/historical-gold-rates',
    },
    {
        'name': 'GoldAPI.io',
        'crawler': goldapi_crawler,
        'link': 'https://www.goldapi.io/',
    },
    {
        'name': 'MetalpriceAPI',
        'crawler': metalpriceapi_crawler,
        'link': 'https://metalpriceapi.com/',
    },
    {
        'name': 'iGold',
        'crawler': igold_ae_crawler,
        'link': 'https://igold.ae/gold-rate/24-carat/',
    },
]


#####################################################################
#                             Task Runners                          #
#####################################################################


def run_crawler(item):
    price, change = item['crawler']()

    price = round(float(price), 0)

    if change is not None:
        change = round(float(change), 2)
        if change < 0:
            change = f':arrow_down_small: *{change}*'
        elif change > 0:
            change = f':arrow_up_small: *+{change}*'
        else:
            change = ':wavy_dash: *0.00*'
    else:
        change = ''

    link = f"<{item['link']}|{item['name']}>"
    text = f':coin: *1 XAU* = *{price} AED* {change} _({link})_'

    return text


def send_slack_alert(data):
    post(
        SLACK_WEBHOOK_URL,
        data=json.dumps(data),
        headers={
            'Content-type': 'application/json',
        }
    )


def main():
    executor = ThreadPoolExecutor(10)
    futures = {
        executor.submit(run_crawler, item): item['name']
        for item in crawlers
    }

    lines = []
    for future, name in futures.items():
        try:
            text = future.result()
            lines.append(text)
        except BaseException as e:
            print(f'[{name}] Failed to get data. {e}')

    message = '\n'.join(lines)
    print(message)

    send_slack_alert({
        "type": "mrkdwn",
        "text": message
    })


if __name__ == '__main__':
    main()
