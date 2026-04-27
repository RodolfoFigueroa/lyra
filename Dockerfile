FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock .python-version README.md ./

RUN apt update && \
    apt install -y --no-install-recommends libpq-dev build-essential libgdal-dev

RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "--frozen", "lyra-api"]