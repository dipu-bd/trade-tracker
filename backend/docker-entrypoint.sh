#!/bin/sh
set -e

alembic upgrade head
python -m tradebot.cli seed || echo "seeding skipped; enter keys in Settings instead"
exec "$@"
