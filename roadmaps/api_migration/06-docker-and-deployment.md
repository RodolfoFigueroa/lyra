# Docker And Deployment

## Mount Strategy

Docker deployments use:

- one writable named volume for Lyra runtime data,
- one read-only file mount for `lyra.toml`,
- one read-only file mount per secret.

`/lyra_data/state/plugins.toml` is not mounted from the host. Lyra creates and
writes it inside the `lyra_data` volume.

## Compose Environment Variables

Use environment variables for host file locations only. They do not configure
Lyra at runtime.

Example `.env`:

```env
LYRA_CONFIG_FILE=./lyra_data/config/lyra.toml
LYRA_POSTGRES_PASSWORD_FILE=./secrets/postgres_password
LYRA_ADMIN_API_KEY_FILE=./secrets/admin_api_key
LYRA_SERVICE_ACCOUNT_FILE=./secrets/service-account.json
```

Example service mount shape:

```yaml
volumes:
  - lyra_data:/lyra_data
  - ${LYRA_CONFIG_FILE}:/lyra_data/config/lyra.toml:ro
  - ${LYRA_POSTGRES_PASSWORD_FILE}:/lyra_data/secrets/postgres_password:ro
  - ${LYRA_ADMIN_API_KEY_FILE}:/lyra_data/secrets/admin_api_key:ro
  - ${LYRA_SERVICE_ACCOUNT_FILE}:/lyra_data/secrets/service-account.json:ro
```

Apply the same mounts to the API service and every worker service.

## Runtime Layout

The container layout is:

```text
/lyra_data/
  config/lyra.toml              # read-only file mount
  secrets/postgres_password     # read-only file mount
  secrets/admin_api_key         # read-only file mount
  secrets/service-account.json  # read-only file mount
  state/plugins.toml            # Lyra-owned writable state
  plugins/catalog/              # Lyra-created catalog checkouts
  plugins/runners/              # Lyra-created worker installs
  cache/jobs/                   # Lyra-created job temp data
  logs/                         # optional Lyra-created logs
```

## Operator Workflow

1. Create `lyra.toml` from `lyra.toml.example`.
2. Create local secret files.
3. Set Compose mount environment variables.
4. Start the stack.
5. Add plugin repos through `POST /admin/plugin-repos`.
6. Refresh with `POST /admin/plugin-catalog/refresh`.
7. Review or edit routing with `/admin/plugin-routing`.
8. Restart workers when plugin code or routing changes need to be observed by
   running workers.

## Example Config Changes

`lyra.toml.example` must not include plugin repo inventory or metric queue
assignments.

It may include:

```toml
[plugins]
default_queue = "interactive"
allowed_queues = ["interactive", "batch"]
# catalog_dir = "/lyra_data/plugins/catalog"
# runner_base_dir = "/lyra_data/plugins/runners"
```

The docs must not tell users to add plugin repos or metric routing by editing
`lyra.toml`.

