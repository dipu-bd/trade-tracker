import logging
import os
import sys

import uvicorn

logging.basicConfig(level=logging.DEBUG)

PACKAGE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.dirname(PACKAGE_DIR)
sys.path.insert(0, ROOT_DIR)


def run():
    from marketbot import app
    uvicorn.run(
        app,
        host='0.0.0.0',
        port=8000,
        log_level=logging.DEBUG,
    )


def run_dev():
    uvicorn.run(
        'marketbot:app',
        host='0.0.0.0',
        port=8000,
        reload=True,
        log_level=logging.DEBUG,
    )


if __name__ == '__main__':
    if 'dev' in sys.argv:
        run_dev()
    else:
        run()
