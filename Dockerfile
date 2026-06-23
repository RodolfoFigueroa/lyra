FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        libgdal-dev \
        libpq-dev \
    && uv sync --frozen --no-dev --no-cache \
    && find /app/.venv -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find /app/.venv -type f -name "*.py[co]" -delete \
    && rm -rf /root/.cache/uv /var/lib/apt/lists/*

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/app/.venv

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
COPY packages ./packages
COPY lyra_app ./lyra_app

# Plugin manifests are cloned into /lyra_plugin_catalog by the API. Plugin code
# is cloned and installed into /lyra_plugins by runner workers.
# Set LYRA_PLUGIN_REPOS to a comma-separated list of GitHub repos to load.
RUN mkdir -p /lyra_plugin_catalog /lyra_plugins
VOLUME /lyra_plugin_catalog
VOLUME /lyra_plugins

EXPOSE 5219

CMD ["python", "-m", "lyra_app.main"]
