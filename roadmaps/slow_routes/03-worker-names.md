# Worker Names

## Goal

Make configured Lyra worker pools match observed Celery workers in admin
observability responses.

## Background From The Discussion

The measured `/admin/workers` response showed configured workers `interactive`
and `batch` as offline, while observed workers appeared separately as
`celery@<container-id>`. This means Celery inspect is working, but Lyra cannot
cleanly match observed workers to configured worker pools.

## Scope

- Set deterministic Celery worker names from the Lyra worker name when launching
  workers.
- Normalize observed Celery worker names so configured workers can be matched.
- Update worker and queue route tests to assert configured workers are observed
  as online when Celery reports the corresponding named worker.
- Preserve compatibility with existing default Celery names as much as possible.

## Out Of Scope

- No cache or timeout changes in this step beyond relying on previous steps.
- No worker restart behavior changes.
- No TUI changes.
- No migration of historical worker names.

## Files Or Areas Likely Affected

- `lyra_app/worker_launcher.py`
- `lyra_app/routes/admin.py`
- `lyra_app/worker_control.py`, if normalization belongs with inspect data
- `tests/test_worker_launcher.py`
- `tests/test_observability_routes.py`
- `docs/src/content/docs/deployment.md`, if worker names are documented

## Required Behavior

- Workers launched through `python -m lyra_app.worker_launcher interactive`
  should have a deterministic Celery node name derived from `interactive`.
- Admin routes should match observed worker names to configured worker names.
- `interactive` and `batch` should not appear as configured offline when the
  corresponding worker process is actually online.
- Queue consumers should still be reported accurately.

## Implementation Notes

- Add a Celery worker hostname argument in `build_celery_worker_args()`.
- Prefer a name that includes the Lyra worker pool and remains unique enough for
  multiple hosts, for example:

  ```text
  --hostname interactive@%h
  ```

- Add a helper to map Celery node names back to Lyra worker names. It should
  handle:
  - exact configured names
  - `<worker>@<host>` names
  - default `celery@<host>` names as observed-but-unconfigured fallbacks
- Keep observed unknown/default Celery workers visible rather than hiding them.
- Be careful not to break direct Celery behavior or worker logs.

## Tests And Verification

- Update `tests/test_worker_launcher.py` to assert worker args include the
  deterministic hostname.
- Add route tests where inspect returns `interactive@host` and verify the
  configured `interactive` worker is `observed=True` and `status="online"`.
- Add or update queue tests so observed consumers are attributed to the
  configured worker name.
- Run:

  ```bash
  uv run pytest tests/test_worker_launcher.py tests/test_observability_routes.py tests/test_worker_control.py
  uv run ruff format
  uv run ruff check --fix
  uv run ty check --fix
  ```

## Step Exit Checklist

- Worker launcher emits deterministic Celery hostnames.
- Admin worker responses no longer split configured worker pools and observed
  named workers when names match by pool prefix.
- Existing default Celery workers still appear as observed unconfigured workers
  rather than disappearing.
- Focused tests pass:

  ```bash
  uv run pytest tests/test_worker_launcher.py tests/test_observability_routes.py tests/test_worker_control.py
  uv run ruff format
  uv run ruff check --fix
  uv run ty check --fix
  ```

- Route latency has been measured after the inspect timeout, inspect cache, and
  worker-name fixes, or the final response clearly states why live measurement
  could not be run.
- The measurement result has been compared against the step 03 decision gate
  thresholds in `99-validation.md`.

## Decision Gate Before The Next Step

Measure route latency after steps 1-3 using the commands in
`99-validation.md`:

- `/admin/workers`
- `/admin/queues`
- paired `/admin/workers` plus `/admin/queues`

Proceed to `04-background-snapshot.md` only if one of the documented background
snapshot gate conditions is met:

- `/admin/workers` or `/admin/queues` still regularly takes more than `1s` in
  the dev stack.
- Paired worker and queue calls still regularly take more than `1.5s`.
- Live Celery inspect still causes visible API request blocking under normal
  polling.
- The user explicitly chooses the background snapshot approach after reviewing
  the simpler-fix measurements.

If the gate cannot be run, report the blocker, the exact command or service
state needed, and do not implement `04-background-snapshot.md` automatically.

## Next-Step Context

`04-background-snapshot.md` is intentionally optional. It exists for the case
where the conservative fixes are measured and judged insufficient.
