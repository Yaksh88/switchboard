# syntax=docker/dockerfile:1

# --- Build the admin dashboard -------------------------------------------------------------
FROM node:26-alpine AS dashboard
WORKDIR /dashboard
RUN corepack enable
COPY dashboard/package.json dashboard/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY dashboard/ ./
RUN pnpm build

# --- Install the gateway -------------------------------------------------------------------
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Dependencies first, so source changes don't invalidate this layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY config/switchboard.example.yaml ./config/switchboard.yaml
RUN uv sync --frozen --no-dev

COPY --from=dashboard /dashboard/dist ./dashboard/dist

RUN useradd --system --uid 10001 --no-create-home switchboard
USER switchboard

ENV SWITCHBOARD_HOST=0.0.0.0 \
    SWITCHBOARD_PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"

CMD ["switchboard", "serve"]
