FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages

RUN apt update && \
    apt install -y --no-install-recommends libpq-dev build-essential libgdal-dev git

RUN uv sync --frozen --no-dev

COPY . .

# Plugin repos are cloned here at runtime. Mount a persistent volume at this
# path so plugins survive container restarts and are not re-cloned from scratch.
# Set LYRA_PLUGIN_REPOS to a comma-separated list of GitHub repos to load.
# Example: docker run -v lyra_plugins:/lyra_plugins -e LYRA_PLUGIN_REPOS=...
RUN mkdir -p /lyra_plugins
VOLUME /lyra_plugins

EXPOSE 5219

CMD ["uv", "run", "--no-dev", "--frozen", "python", "-m", "lyra_app.main"]
