---
title: Terminal Console
description: Operate a running Lyra API from the first-party terminal interface.
---

The TUI connects to an existing API; it does not start Redis, PostGIS, the API,
or workers. It shows readiness without credentials and unlocks administrative
views and actions with the admin key.

```bash
LYRA_ADMIN_API_KEY=... uv run lyra-tui \
  --host localhost:5219 \
  --no-secure
```

Pass only a host and optional port to `--host`; choose the scheme with
`--secure` or `--no-secure`. Use the generated [CLI
reference](../../reference/generated/cli/) for exact options.

The console covers health, retained jobs, workers, queues, catalog state,
plugin repositories, and metric routing. Mutating or disruptive actions ask for
confirmation before calling admin routes.

If the admin key is absent, admin views remain locked. If readiness fails, fix
Redis or PostGIS before diagnosing higher-level behavior. Unknown worker state
or queue depth means Celery inspection is unavailable or stale, not necessarily
that routing is absent.
