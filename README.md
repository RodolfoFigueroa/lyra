# Lyra

Lyra is a REST API for computing accessibility and land-use metrics for spatial
units in Mexico. It discovers metrics from typed Python plugins, validates jobs
against generated schemas, and executes them in queue-specific Celery workers.

## Start locally

Lyra requires a reachable PostGIS database and a Google Earth Engine service
account. Copy the checked-in configuration templates, fill in the placeholders,
then start the development stack:

```bash
mkdir -p lyra_data/config secrets
cp config.example.toml lyra_data/config/lyra.toml
cp .env.example .env
docker compose -f docker/docker-compose-dev.yml up --build
```

See the [quickstart](https://rodolfofigueroa.github.io/lyra/quickstart/)
for prerequisites, configuration, authentication, and a complete smoke test.

## Documentation

- [Use the REST API](https://rodolfofigueroa.github.io/lyra/dev/use/rest-api/)
- [Use the Python client](https://rodolfofigueroa.github.io/lyra/dev/use/python-client/)
- [Build a plugin](https://rodolfofigueroa.github.io/lyra/dev/plugins/quickstart/)
- [Deploy and operate Lyra](https://rodolfofigueroa.github.io/lyra/dev/operate/deployment/)
- [Generated reference](https://rodolfofigueroa.github.io/lyra/dev/reference/)
- [Contribute to Lyra](CONTRIBUTING.md)

When a server is running, its exact OpenAPI contract is also available at
`/openapi.json`, `/docs`, and `/redoc`.
