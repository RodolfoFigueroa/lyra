---
title: Client Generator CLI
description: Pull metric catalogs and generate deterministic typed Python clients.
---

`lyra-client` is installed with `lyra-api`. It snapshots a server's public
metric catalog and turns that snapshot into a typed Python package. Keep both
the catalog and generated package in version control so catalog changes are
reviewable and CI can detect stale output.

## Pull a catalog

```bash
uv run lyra-client catalog pull \
  --host lyra.example.com \
  --output lyra-catalog.json
```

`--host` accepts a hostname or a base URL. A hostname uses HTTPS by default;
pass `--insecure` to use HTTP for a local server:

```bash
uv run lyra-client catalog pull \
  --host localhost:5219 \
  --insecure \
  --output lyra-catalog.json
```

An explicit `http://` or `https://` scheme is preserved. Catalog discovery is
public, so this command does not accept an agent or admin key. The output is
validated and written as deterministic, indented JSON with no timestamp or
machine-specific metadata. If `--output` is omitted, it defaults to
`lyra-catalog.json` in the current directory.

## Generate a package

```bash
uv run lyra-client generate \
  --catalog lyra-catalog.json \
  --package acme_lyra \
  --output src/acme_lyra
```

`--package` must be one valid Python identifier, such as `acme_lyra`. `--output`
is the package directory itself, not its parent. Generation produces typed
request models, sync and async clients, metric resources, contract metadata,
`py.typed`, and `.lyra-client-manifest.json`.

The manifest records the files owned by the generator. A later generation can
replace or remove those files, while unrelated files in the destination are
preserved. Do not edit generated files: make catalog or generator changes and
regenerate instead.

The generator renders and validates the complete package in a temporary
directory before updating the destination. Broken references, invalid names,
and contracts that cannot be emitted fail without leaving partial generated
output. A valid JSON Schema feature that cannot be represented precisely in a
Python annotation emits a warning and uses the narrow `JsonValue` fallback;
the full schema still validates arguments at runtime.

## Check generated files in CI

Use `--check` with the same inputs used for generation:

```bash
uv run lyra-client generate \
  --catalog lyra-catalog.json \
  --package acme_lyra \
  --output src/acme_lyra \
  --check
```

Check mode performs no writes. It prints a concise diff when generated files
are missing, changed, or stale.

| Exit status | Meaning |
| --- | --- |
| `0` | The command succeeded, or check mode found no drift. |
| `1` | Check mode found stale generated output. |
| `2` | Catalog retrieval, validation, or generation failed. |

A typical update is: pull the catalog, inspect its diff, regenerate the
package, run application type checks and tests, then commit both changes.

## Command help

Use built-in help for the exact options supported by the installed version:

```bash
uv run lyra-client --help
uv run lyra-client catalog pull --help
uv run lyra-client generate --help
```

The [generated CLI reference](../../reference/generated/cli/) is produced from
the same argument parser.
