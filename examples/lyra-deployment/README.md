# A deployment with installed plugins

This uv project owns the application's dependency set, including its plugins.
Its production image contains non-editable installations; the development target
uses editable installations and source mounts. Both install dependencies at build
time. API and workers use the same image and can start independently.

The example uses the Lyra checkout and `examples/lyra-plugin`. From this directory:

```sh
cp .env.example .env
mkdir -p secrets
```

Set `.env` secrets, supply `secrets/service-account.json`, and edit `lyra.toml` and
`lyra.dev.toml` for your Earth Engine project and reachable PostGIS database.
The provided Redis endpoint is the Compose service `redis`. Then:

```sh
docker compose up -d --build
```

Production selects `lyra-smoke-plugin` from the installed distributions. Its
manifest is installed below `/app/.venv/share/lyra/plugins/lyra-smoke-plugin/`.
The framework's root Dockerfile remains a framework-only image; selecting a
plugin in TOML does not install it.

## Everyday development

```sh
docker compose -f compose.yml -f compose.dev.yml up -d --build
```

The override mounts application source, SDK/utilities source, and the plugin
checkout at their editable installation paths. Container dependencies remain in
`/app/.venv`; host virtual environments never replace that environment. Plugin
`manifest_path` points at the mounted root manifest. No watcher is enabled.

After an implementation edit:

```sh
docker compose -f compose.yml -f compose.dev.yml restart interactive batch
```

After changing a metric contract, regenerate and check its manifest from the
Lyra repository root:

```sh
uv run lyra-plugin build-manifest --project examples/lyra-plugin
uv run lyra-plugin check-manifest --project examples/lyra-plugin
```

Then restart `api interactive batch` using the same Compose files. The worker
rejects a manifest that differs from the imported definition. Application code
changes need the affected processes restarted as well.

Dependency, package version, or packaging changes need both lockfiles updated and
an image rebuild. From this directory:

```sh
uv lock
uv lock --project dev
docker compose -f compose.yml -f compose.dev.yml up -d --build --force-recreate
```

The production build uses `uv sync --locked --no-dev --no-editable`; development
uses its own lock with editable sources. Keep both locks committed. Local path
locks fix dependencies but not checkout contents: commit source changes too.
See [uv installation modes](https://docs.astral.sh/uv/concepts/projects/sync/#editable-installation).

## Multiple independent plugin checkouts

`LYRA_SOURCE` and `SMOKE_PLUGIN_SOURCE` set host checkouts used by named build
contexts and development mounts. The corresponding uv source paths must resolve
when updating locks locally. The checked-in project assumes this layout:

```text
lyra/
  packages/
  examples/
    lyra-plugin/
    lyra-deployment/
      dev/
```

To add a second plugin checkout without putting it inside Lyra:

1. Add its distribution to both deployment dependency lists and a local path to
   each `[tool.uv.sources]`; set `editable = true` in the development project.
2. Add a Compose build `additional_contexts` entry pointing to that checkout.
3. Add `COPY --from=<context> . <container-path>` in the Dockerfile's `sources`
   stage. Choose the container path so the relative uv source paths resolve as
   they do on the host. Add a matching development bind mount for all app services.
4. Add `[[plugins.installed]]` entries to both TOML files; only the development
   entry needs `manifest_path` pointing into the new mount.
5. Regenerate that plugin's manifest, update both deployment locks, and rebuild.

For example, a checkout beside Lyra can use production source `../../../other-plugin`,
development source `../../../../other-plugin`, container path `/workspace/other-plugin`,
and a mount from the host checkout to `/workspace/other-plugin:ro`. Each additional
plugin follows the same standard uv/Compose configuration. See
[Compose named build contexts](https://docs.docker.com/reference/compose-file/build/#additional_contexts).

For an independent deployment repository, adapt the source paths and container
layout together. For production Git dependencies, replace local sources with uv
Git sources pinned to full commit IDs and regenerate the lock. Lyra's SDK and
utilities live in repository subdirectories; declare their sources explicitly,
using the same Lyra commit. Private dependency credentials belong to the build
configuration, not runtime containers. Native libraries required by plugins must
also be included in the image's runtime stage.

## Updating a running deployment

Drain work before changing contracts, routing, or plugin versions. Recreate the
API and all workers with the same image and configuration, wait for `/ready`, and
inspect workers/queues before reopening submissions. Preserve the data volume.
No API registration, catalog synchronization, or runtime package installation is
required. `/admin/plugins` and `lyra-admin plugins list` report configured
distributions and installed versions.
