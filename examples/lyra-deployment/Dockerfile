FROM python:3.11-slim AS sources
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git libgdal-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/*
# Named contexts allow independent plugin checkouts outside the Lyra repository.
COPY --from=lyra . /workspace/lyra
COPY --from=smoke . /workspace/lyra/examples/lyra-plugin
COPY . /workspace/lyra/examples/lyra-deployment

FROM sources AS production-builder
WORKDIR /workspace/lyra/examples/lyra-deployment
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM sources AS development
WORKDIR /workspace/lyra/examples/lyra-deployment/dev
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
ENV VIRTUAL_ENV=/app/.venv PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "lyra_app.main"]

FROM python:3.11-slim AS production
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv PATH="/app/.venv/bin:$PATH"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=production-builder /app/.venv /app/.venv
COPY --from=lyra LICENSE /app/LICENSE
WORKDIR /app
RUN mkdir -p /lyra_data/config /lyra_data/secrets /lyra_data/cache/jobs /lyra_data/logs
VOLUME /lyra_data
EXPOSE 5219
CMD ["python", "-m", "lyra_app.main"]
