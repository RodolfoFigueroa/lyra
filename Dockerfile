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

# Durable Lyra app files live under /lyra_data. Secret files are provided by the
# deployment and are intentionally not generated in the image.
RUN mkdir -p \
        /lyra_data/config \
        /lyra_data/cache/jobs \
        /lyra_data/plugins/catalog \
        /lyra_data/plugins/runners \
        /lyra_data/logs
VOLUME /lyra_data

EXPOSE 5219

CMD ["python", "-m", "lyra_app.main"]
