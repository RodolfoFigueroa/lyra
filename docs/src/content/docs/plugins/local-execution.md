---
title: Run Plugins Locally
description: Invoke a plugin directly with no database, a test fake, or managed Postgres access.
---

`LocalRunContext` lets an author call an installed plugin directly, without an
API, worker, Redis, or Celery. It implements the public `RunContext` contract
and keeps accepted progress and message events in memory for inspection.
Direct invocation is an author test tool, not a local reproduction of the
production runner.

## Install the SDK

For plugins that use no database or inject a test fake, install the core SDK:

```bash
uv add lyra-sdk
```

The current `lyra-sdk` package supports Python 3.11 and newer. Its core runtime
dependencies are `jsonschema>=4.26.0`, `pydantic>=2.13.4`, and
`ruamel-yaml>=0.19.1`. PostgreSQL support is deliberately optional:

```bash
uv add 'lyra-sdk[postgres]'
```

The `postgres` extra adds `geopandas>=1.1.3`, `psycopg[binary]>=3.3.2`, and
`sqlalchemy>=2.0.51`. Importing `lyra.sdk.postgres`, or requesting
`LocalRunContext.connect_postgres`, without those packages raises an import
error that says PostgreSQL support requires the `postgres` extra and shows the
supported `uv add` command.

`lyra-sdk` does not depend on `lyra_app` or other application-only modules.
Plugin code should import public contracts from `lyra.sdk`; the optional
PostgreSQL implementation and connector are public from `lyra.sdk.postgres`.

## Invoke an installed plugin

The same direct invocation shape works in all three database modes. The caller
chooses the job metadata and owns the output directory:

```python
from pathlib import Path

from lyra.sdk import JobEnvelope, LocalRunContext
from my_plugin.plugin import create_plugin

output_dir = Path("local-output")
context = LocalRunContext(
    job_id="author-check-1",
    metric="my_metric",
    temp_dir=output_dir,
)
result = create_plugin()(
    JobEnvelope(
        job_id=context.job_id,
        metric=context.metric,
        input={
            "location": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "sample",
                        "properties": {},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [-99.13, 19.43],
                        },
                    }
                ],
            }
        },
    ),
    context,
)

print(result)
print(context.events)
print(list(output_dir.iterdir()))
```

The SDK creates `output_dir` when needed but never deletes it. The caller may
inspect the result, every captured event, logs sent to the configured standard
library logger, and files below `temp_dir` after execution.

## Mode 1: no database

Choose this mode for metrics that never call `context.db`, or when a test should
prove that database access is absent. The default is a fresh `StubLyraDB`; no
external service or database resource exists. The caller owns only `temp_dir`
and any injected logger.

Use the invocation above unchanged. If the metric calls any `LyraDB` method,
that call fails immediately with `DatabaseNotConfiguredError`, whose
`operation` identifies the attempted method. Events accepted before the
failure and files already written remain inspectable.

## Mode 2: a test fake

Choose a plugin- or test-owned fake for deterministic unit tests of
database-dependent behavior. No external service is required. The caller owns
the fake and any resources it uses; `LocalRunContext` neither initializes nor
disposes an injected database.

```python
from pathlib import Path
from unittest.mock import create_autospec

from lyra.sdk import LocalRunContext, LyraDB

fake_db = create_autospec(LyraDB, instance=True)
fake_db.load_mesh_from_bounds.return_value = expected_mesh

context = LocalRunContext(
    job_id="fake-db-check",
    metric="my_metric",
    temp_dir=Path("local-output"),
    db=fake_db,
)

result = create_plugin()(job, context)
fake_db.load_mesh_from_bounds.assert_called_once()
print(result, context.events)
```

Here `expected_mesh`, `job`, and `create_plugin` are test fixtures from the
plugin. Configure every fake return value the metric needs. An unconfigured or
deliberately failing fake produces the behavior chosen by the test, rather than
SDK connection errors.

## Mode 3: live Postgres/PostGIS

Choose live mode for an integration check against the real spatial schema.
Install `lyra-sdk[postgres]`, use a disposable integration database or an
explicitly approved author account, and keep the context inside its manager:

```python
import os
from pathlib import Path

from lyra.sdk import JobEnvelope, LocalRunContext
from my_plugin.plugin import create_plugin

job = JobEnvelope(
    job_id="live-db-check",
    metric="my_metric",
    input={
        "bounds": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-99.14, 19.42],
                    [-99.12, 19.42],
                    [-99.12, 19.44],
                    [-99.14, 19.44],
                    [-99.14, 19.42],
                ]
            ],
        }
    },
)

with LocalRunContext.connect_postgres(
    os.environ["LYRA_AUTHOR_POSTGRES_URL"],
    job_id=job.job_id,
    metric=job.metric,
    temp_dir=Path("local-output"),
    schema="lyra_data",
) as context:
    result = create_plugin()(job, context)
    print(result, context.events)
```

The manager owns its SQLAlchemy engine, performs an eager `SELECT 1` probe,
yields the context, and disposes the engine on every exit path. The context and
database adapter must not be used after the block. Connection and capacity
failures raise `DatabaseUnavailableError`, statement deadlines raise
`DatabaseQueryTimeoutError`, and authentication, permission, schema, column, or
other deterministic query failures raise `DatabaseQueryError`. The connector
does not retry.

Set `schema` explicitly to the operator-approved data schema. The probe checks
connectivity only; it does not check required tables, extensions, or schema
versions.

## Live-data trust and operations

This first iteration gives vetted plugin authors direct database connectivity.
Trusted authors, read-only session settings, and `LocalRunContext` do not form a security boundary.
PostgreSQL permissions are authoritative, and the shared database must be
treated as real data even when the plugin runs on an author's machine.

Operators should provision a dedicated login role for author access, never the
application owner or migration role. Grant only database `CONNECT`, approved
schema `USAGE`, required table or view `SELECT`, and required function
`EXECUTE`. Set `default_transaction_read_only=on` on that role or its connection
profile, configure the allowed schema explicitly, and audit inherited as well
as direct privileges.

Store `LYRA_AUTHOR_POSTGRES_URL` in an approved secret store, protect and rotate
it, and revoke the login or grants when access is no longer needed. Never put a
password-bearing URL in source control, shell command history, logs,
screenshots, or issue reports. Local SDK sessions identify themselves to
PostgreSQL as `lyra-sdk-local`; operators can use that low-cardinality
`application_name` for observation, not authorization.

## Differences from production

- `LocalRunContext` captures every accepted event immediately. It does not
  reproduce worker throttling, coalescing, persistence, or event-size policy.
- Cancellation is cooperative. Calling `cancel()` only causes a later
  `check_cancelled()` call to raise `RunCancelledError`.
- The caller owns and retains `temp_dir` and all files written below it.
- Direct `PluginDefinition` invocation prepares typed arguments, but it does
  not automatically perform the worker's production result validation or
  normalization. Calling a decorated handler directly omits that worker
  behavior as well.
- Tests and authors must supply backend-resolved spatial references and every
  other production input. Local execution does not resolve CVEGEO or
  metropolitan-zone references.
- Each `LyraDB` method opens its own read transaction. Multiple calls do not
  share one database snapshot.
- Database writes are unsupported. Automatic retries, streaming, and
  result-size controls are not part of the local workflow.
- Live connection setup performs only an eager connectivity probe, not a
  schema/version compatibility check.

See the [generated runtime reference](../../reference/generated/python/lyra-sdk-runtime/)
for `RunContext`, `RunCancelledError`, `LocalRunContext`, `LyraDB`,
`StubLyraDB`, the database error hierarchy, `PostgresLyraDB`, and
`connect_postgres`.
