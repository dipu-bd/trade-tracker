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

## Configuration

Every setting is an environment variable prefixed `TRADEBOT_`; see `.env.example`.

`TRADEBOT_SECRET_KEY` wraps every stored provider and model credential using envelope
encryption. Rotating it requires rewrapping existing credentials — changing it outright leaves
them undecryptable.

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
