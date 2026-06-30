# Server-Side TOML Configuration Roadmap

This roadmap is the implementation source of truth for moving Lyra from mixed
environment variables, fixed file paths, Docker volume conventions, and
plugin-owned queue declarations to one server-owned TOML configuration file.

The target architecture is:

- One persistent Docker volume named `lyra_data`.
- One shared container mount at `/lyra_data`.
- One main config file at `/lyra_data/config/lyra.toml`.
- Secret values referenced by file path, not stored inline in TOML.
- Plugin metric queues assigned by server configuration, not by plugin authors.
- API and worker processes reading the same typed config contract.

No backward compatibility or migration path is required. Lyra has not been
released publicly, so implementation should prefer the final shape described in
these documents over transitional compatibility.

## Document Order

Read and implement these files in order:

1. [Current State And Goals](01-current-state-and-goals.md)
2. [Config Contract](02-config-contract.md)
3. [Runtime Loading And Validation](03-runtime-loading-and-validation.md)
4. [Plugin Routing And Persistence](04-plugin-routing-and-persistence.md)
5. [Docker And Filesystem Layout](05-docker-and-filesystem-layout.md)
6. [Implementation Checklist](06-implementation-checklist.md)

## Final State

The config file owns app settings that currently live in several places:

- API host and port.
- Redis and Celery broker settings.
- Database connection settings.
- Earth Engine project and service account file reference.
- Admin authentication secret file reference.
- Logging destination and level.
- Job store TTL.
- Plugin repository list and plugin checkout directories.
- Metric-to-queue assignments.
- Worker pools, worker queues, install directories, and temporary file roots.

After implementation, code should not read Lyra app settings directly from
environment variables except for bootstrap-only process selection, such as the
worker name passed to a worker command. Environment variables may still be used
by Docker Compose to mount `/lyra_data` on the host, but the application runtime
configuration comes from TOML.

