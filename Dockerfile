FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        libgdal-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock .python-version ./
COPY packages/lyra_api/pyproject.toml ./packages/lyra_api/pyproject.toml
COPY packages/lyra_sdk/pyproject.toml ./packages/lyra_sdk/pyproject.toml
COPY packages/lyra_tui/pyproject.toml ./packages/lyra_tui/pyproject.toml
COPY packages/lyra_utils/pyproject.toml ./packages/lyra_utils/pyproject.toml

# Keep third-party dependencies cached when application or workspace source changes.
RUN uv sync --frozen --no-dev --no-cache --no-install-workspace

COPY packages/lyra_sdk ./packages/lyra_sdk
COPY packages/lyra_utils ./packages/lyra_utils

RUN uv sync --frozen --no-dev --no-cache \
    && find /app/.venv -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find /app/.venv -type f -name "*.py[co]" -delete \
    && rm -rf /root/.cache/uv

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/app/.venv \
    PYTHONPATH=/app/packages/lyra_sdk/src:/app/packages/lyra_utils/src

ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml uv.lock .python-version ./
COPY LICENSE ./LICENSE
COPY packages/lyra_sdk ./packages/lyra_sdk
COPY packages/lyra_utils ./packages/lyra_utils
COPY lyra_app ./lyra_app

# Durable Lyra app files live under /lyra_data. The Earth Engine service account
# file is provided by the deployment and intentionally not generated here.
RUN mkdir -p \
        /lyra_data/config \
        /lyra_data/secrets \
        /lyra_data/state \
        /lyra_data/cache/jobs \
        /lyra_data/plugins/catalog \
        /lyra_data/plugins/runners \
        /lyra_data/logs
VOLUME /lyra_data

EXPOSE 5219

CMD ["python", "-m", "lyra_app.main"]
