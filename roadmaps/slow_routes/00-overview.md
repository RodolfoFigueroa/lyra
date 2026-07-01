# Slow Admin Routes Optimization Overview

## Feature

Reduce latency and improve correctness for Lyra admin worker and queue observability routes.

The measured problem is not `GET /admin/status`: that route was about `0.015s`.
The slow paths are:

- `GET /admin/workers`: about `5.045s`
- `GET /admin/queues`: about `5.019s`
- sequential TUI-style refresh with both routes: about `10.100s`

Both slow routes call `inspect_workers()`, which currently performs five Celery
remote-control inspect calls. Celery inspect defaults to a `1.0s` timeout, so
missing or slow replies naturally produce a roughly five second route.

## Agreed Decisions

- Focus on the API, not the TUI.
- Keep `/admin/status` fast and simple; do not fold slow worker inspection into it.
- Use a conservative optimization path before introducing a background poller.
- Implement the small fixes first:
  - shorter explicit Celery inspect timeout
  - short in-process TTL cache for worker inspect snapshots
  - deterministic worker hostnames so configured workers match observed workers
- Treat a background worker-status snapshot collector as a gated follow-up, not
  the first implementation.

## Conservative Path

1. Add an explicit short timeout for Celery inspect calls.
2. Add a short TTL cache around `inspect_workers()` so repeated route calls share
   one recent snapshot.
3. Set deterministic worker names in the launcher and normalize matching.
4. Measure again. Only implement a background snapshot collector if route latency
   is still too high or the API still blocks too often under realistic worker
   behavior.

## Non-Goals

- Do not change the public job API.
- Do not make the TUI directly inspect Redis or Celery.
- Do not remove Celery inspect entirely.
- Do not make `/admin/status` depend on worker inspect.
- Do not add a cross-process cache or persistent status store in the first pass.
- Do not build a distributed observability subsystem.

## Rejected Or Deferred Approaches

- Immediate background poller: deferred because it adds API lifecycle, stale-data,
  and multi-process behavior before simpler fixes are tested.
- Synchronous live inspect on every route forever: rejected because it makes
  pollable admin routes wait on Celery remote-control timeouts.
- Combining `/admin/workers` and `/admin/queues` only at the client layer:
  insufficient because API consumers can still call both routes independently.

## Assumptions

- A `0.25s` to `0.5s` inspect timeout is acceptable for observability. If worker
  replies miss that window, the API can return unknown/stale worker data rather
  than blocking.
- A short in-process cache TTL of `1s` to `2s` is enough to collapse paired
  `/admin/workers` and `/admin/queues` calls without making data feel old.
- The API may run with more than one process in deployment. A first in-process
  cache is still useful, but it is per process and does not need cross-process
  consistency.
- Worker identity should be based on Lyra worker pool names such as `interactive`
  and `batch`, not only Celery's default `celery@<hostname>`.

## Risks

- Too-short inspect timeouts can mark healthy but slow workers as unknown.
- TTL caching can briefly return stale worker state after a worker starts,
  exits, or changes queues.
- Deterministic Celery hostnames may affect tests, logs, and operator scripts
  that currently expect default Celery names.
- A background snapshot collector can become too clever if it is introduced
  before the simpler fixes are measured.

## Execution Order

1. Add explicit Celery inspect timeout.
2. Add short worker inspect TTL cache.
3. Fix worker hostnames and worker matching.
4. Add background snapshot collector only if the validation gate says it is
   still needed.
5. Run final validation against unit tests and a live Compose-backed stack.

