# Tradebot

A multi-user paper-trading platform where an AI model manages portfolios. A deterministic
engine screens the market and proposes trades; an LLM acts as a meta-labeller deciding which
bets to take and how strongly; hard risk guardrails clamp anything it returns. No real money
moves.

Design and rationale live in the plan; this file covers running it.

## Status

**M1 — foundation.** Auth, encrypted credential vault, event recording with a live stream,
migrations, Docker, CI. The trading engine, market data, broker, and dashboard land in later
milestones.

## Quick start

```bash
cp .env.example .env
# TRADEBOT_SECRET_KEY must be at least 32 characters
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up --build
```

The API is on `http://localhost:8000`, docs at `/api/docs`.

## Local development

Backend:

```bash
cd backend
uv sync --group dev
uv run alembic upgrade head
uv run uvicorn tradebot.main:create_app --factory --reload
```

Frontend, against a running backend:

```bash
cd frontend
pnpm install
pnpm dev
```

Set `TRADEBOT_DATABASE_URL=sqlite+aiosqlite:///./tradebot.db` for local work; Postgres is used
in production. The same SQLAlchemy models serve both.

## Checks

```bash
cd backend  && uv run ruff check . && uv run mypy src && uv run pytest -q
cd frontend && pnpm run typecheck && pnpm run build
```

`alembic check` in CI fails the build if the models drift from the migrations.

## Market data providers

Providers declare their capabilities (quotes, bars, streaming, news, fundamentals, corporate
actions) and asset classes, so adding one is a new file plus a registration rather than a change
to any caller. A router picks the highest-priority available provider per capability and asset
class, failing over on error and backing off on rate limits.

Crypto symbols are stored canonically as `BASE-USD`. Dollar-pegged quote legs (USDT, USDC,
FDUSD, …) collapse into that single `USD` leg, so the same coin is one instrument no matter which
venue serves it, and venue-specific spellings stay inside their adapter.

**Deployment note:** Binance's public market-data endpoints are geo-blocked from US IP addresses.
This project is intended to run from a non-US host; on a US-hosted server the Binance provider
will fail and the crypto universe falls back to Crypto.com, which lists far fewer dollar pairs.
Swapping in a Kraken or Coinbase adapter would be a new file plus a registration if that changes.

## Configuration

Every setting is an environment variable prefixed `TRADEBOT_`; see `.env.example`.

`TRADEBOT_SECRET_KEY` wraps every stored provider and model credential using envelope
encryption. Rotating it requires rewrapping existing credentials — changing it outright leaves
them undecryptable.

## Deployment

`main` builds an image, publishes it to `ghcr.io/<owner>/<repo>`, and rolls it out over SSH; see
`.github/workflows/deploy.yml`. The server runs `docker-compose.prod.yml`, which **pulls** the
published tag rather than building — nothing is compiled on the host, so what runs is what CI
tested. Rolling back is dispatching the workflow with an earlier `tag`.

Repository secrets it needs:

| Secret | What it is |
|---|---|
| `SSH_SECRET` | Private key authorised on the server |
| `DEPLOY_HOST` | Hostname or IP |
| `DEPLOY_USER` | SSH user, must be in the `docker` group |
| `DEPLOY_PATH` | Directory holding the compose file and `.env` |
| `ENV_FILE` | Full contents of the server's `.env` |

`ENV_FILE` must define `TRADEBOT_SECRET_KEY` and `POSTGRES_PASSWORD`; both are required and the
stack refuses to start without them. Provider keys belong here too if you want the container to
seed them, though entering them in Settings is the normal path.

## Layout

```
backend/src/tradebot/
  core/      settings, logging, clock, money, crypto, security
  db/        models and async session
  obs/       event recorder, SSE bus, metrics
  services/  auth, credential vault
  api/       routers and dependencies
frontend/    vite + react + typescript
```
