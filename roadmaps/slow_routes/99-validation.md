# Final Validation

## Checklist

- Inspect timeout is explicit and shorter than Celery's default.
- Unknown inspect state still returns a valid API response.
- Worker inspect snapshots are cached briefly in process.
- `/admin/workers` and `/admin/queues` share recent inspect data when called
  close together.
- Workers launched through `worker_launcher` have deterministic names.
- Configured worker pools match observed Celery workers when workers are online.
- Background snapshot collector was either:
  - intentionally not implemented because the conservative fixes met the target
  - implemented only after the documented validation gate showed it was needed
- Documentation was updated if operator-visible behavior or response metadata
  changed.

## Repository Commands

Run these from `/home/lain/Documents/lyra`:

```bash
uv run ruff format
uv run ruff check --fix
uv run ty check --fix
uv run pytest
```

If docs were changed:

```bash
npm run build --prefix docs
```

## Services And Cleanup

Use the existing Compose dev stack for end-to-end validation unless the user
explicitly asks to validate against already-running services.

Start services from `/home/lain/Documents/lyra`:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

Record these handles during validation:

- Compose file: `docker/docker-compose-dev.yml`
- API port: `5219`
- Expected dev containers:
  - `lyra-api-dev`
  - `lyra-redis-dev`
  - `lyra-celery-worker-interactive-dev`
  - `lyra-celery-worker-batch-dev`

Readiness checks:

```bash
curl -sS http://localhost:5219/health
curl -sS -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  http://localhost:5219/admin/status
```

Teardown after validation:

```bash
docker compose -f docker/docker-compose-dev.yml down
```

Cleanup verification:

```bash
docker compose -f docker/docker-compose-dev.yml ps
```

Validation is not complete until every service, container, watcher, or
background process started for validation has been stopped, unless the user
explicitly asks to leave it running.

## End-To-End Route Timing

With the Compose stack running and `.env` loaded, time the admin routes that
were slow:

```bash
uv run --env-file .env python -c 'import os,time,urllib.request
base="http://localhost:5219"
key=os.environ["LYRA_ADMIN_API_KEY"]
for path in ["/admin/status","/admin/workers","/admin/queues"]:
    request=urllib.request.Request(
        base+path,
        headers={"Authorization":"Bearer "+key},
    )
    started=time.perf_counter()
    urllib.request.urlopen(request,timeout=35).read()
    print(path, round(time.perf_counter()-started,3), "s")'
```

Also time paired worker and queue calls in one process:

```bash
uv run --env-file .env python -c 'import os,time,urllib.request
base="http://localhost:5219"
headers={"Authorization":"Bearer "+os.environ["LYRA_ADMIN_API_KEY"]}
started=time.perf_counter()
for path in ["/admin/workers","/admin/queues"]:
    urllib.request.urlopen(
        urllib.request.Request(base+path,headers=headers),
        timeout=35,
    ).read()
print("workers+queues", round(time.perf_counter()-started,3), "s")'
```

Expected after steps 1-3:

- `/admin/status` remains fast and does not depend on worker inspect.
- `/admin/workers` and `/admin/queues` are much faster than the original `~5s`
  each when workers are missing or slow.
- Paired `/admin/workers` plus `/admin/queues` is faster than two independent
  inspect passes because of the TTL cache.
- Observed workers match configured `interactive` and `batch` pools when the
  dev workers are online.

## Step 03 Decision Gate: Background Snapshot

This is the same gate referenced by `03-worker-names.md`. Do not implement
`04-background-snapshot.md` unless one of these is true after steps 1-3:

- `/admin/workers` or `/admin/queues` still regularly takes more than `1s` in
  the dev stack.
- Paired worker and queue calls still regularly take more than `1.5s`.
- Live Celery inspect still causes visible API request blocking under normal
  polling.
- The user explicitly chooses the background snapshot approach after reviewing
  the simpler-fix measurements.

If the gate is not met, record that the background snapshot was intentionally
deferred and proceed to final validation without implementing
`04-background-snapshot.md`.

## Regression Checks

- Stop one worker container and verify routes return quickly with unknown or
  offline state.
- Restart the worker and verify configured worker status returns to online after
  cache expiry.
- Verify default Celery worker names, if any are present, are still visible as
  observed unconfigured workers rather than hidden.
- Verify `POST /admin/workers/restart` still performs live active-task checks
  and is not accidentally served from cached inspect data.
- Verify `/admin/status` still does not call `inspect_workers()`.

## Pass/Fail Criteria

Pass if:

- All repository checks pass.
- Live route timings meet the expectations for the implemented steps.
- Worker identity is correct for configured dev workers.
- Any stale or unknown worker state is explicit and non-blocking.
- All services started for validation are stopped or intentionally left running
  at the user's explicit request.

Fail if:

- Worker/queue routes still spend seconds waiting on repeated Celery inspect
  calls after conservative fixes.
- Configured workers still appear offline while the corresponding named worker
  is online.
- Route behavior depends on the TUI or any non-API component.
- Validation services are left running accidentally.
