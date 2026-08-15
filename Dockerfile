FROM node:22-alpine AS frontend
WORKDIR /build
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
# esbuild's platform binary arrives as an optional dependency, so its postinstall is dead weight.
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY frontend/ ./
RUN pnpm run build


FROM python:3.12-slim AS backend
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# Must match the runtime WORKDIR: venv console scripts bake an absolute shebang.
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 tradebot
WORKDIR /app

COPY --from=backend --chown=tradebot:tradebot /app /app
COPY --from=frontend --chown=tradebot:tradebot /build/dist /app/static

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TRADEBOT_STATIC_DIR=/app/static

RUN chmod +x /app/docker-entrypoint.sh

USER tradebot
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "tradebot.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
