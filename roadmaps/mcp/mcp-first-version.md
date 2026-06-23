# MCP First Version

## Objective

Implement the smallest useful MCP adapter over Lyra's existing registry and job
system. This should be simple, robust, and deployable without changing the core
indicator execution model.

## Scope

The first version should add MCP alongside the current API. It should not remove
or substantially change:

- `/metrics`
- `/metrics/{metric_name}`
- `/models`
- `/models/{model_name}`
- `/ws/{metric}`
- `/download_result/{download_id}`

## Recommended Endpoint

Expose a single remote MCP endpoint:

- `/mcp`

The endpoint should sit behind the same reverse proxy and institutional OAuth
gate as the rest of the application.

## Minimal MCP Capabilities

### Resources

Expose read-only resources for discovery:

- `lyra://metrics`
- `lyra://metrics/{metric_name}`
- `lyra://data-types`
- `lyra://models`
- `lyra://models/{metric_name}`

These can initially mirror existing API data, but the content should gradually
become more agent-oriented than human/API-oriented.

### Tools

Start with a conservative tool set:

- `list_metrics`
- `describe_metric`
- `submit_metric`
- `get_metric_result`
- `cancel_metric_job` if cancellation can be wired safely

If the metric catalog is small enough, also consider generated per-metric tools:

- `run_tree_coverage`
- `run_temperature`
- `run_accessibility_jobs`

The generic tools are easier to keep stable. The generated tools are easier for
agents to choose correctly. Supporting both is reasonable if maintenance stays
simple.

## Execution Model

The first version should preserve the current Celery and Redis flow:

1. Validate the MCP tool input using the same Pydantic model used by
   `/ws/{metric}`.
2. Submit the task with `celery_app.send_task`.
3. Return a job or result handle.
4. Let the client retrieve the result through an MCP tool or resource link.

Avoid creating a second execution path that calls plugin functions directly from
the MCP server.

## Long-Running Jobs

Do not depend exclusively on experimental or unevenly supported MCP task
features in the first version.

Instead, provide stable fallback tools:

- `submit_metric`
- `get_metric_status`
- `get_metric_result`
- `cancel_metric_job`

MCP task support can be added later as a compatibility layer over the same job
state.

## Result Handling

For JSON results:

- Return a compact structured summary when possible.
- Include the full result only if it is reasonably small.
- Include `download_id` and a resource link for full retrieval.

For file results:

- Return `download_id`, `mime_type`, and file metadata.
- Do not inline binary data as tool text.

For large GeoJSON or tabular results:

- Prefer handles, resource links, or preview summaries.
- Avoid forcing agents to parse huge payloads inside a tool response.

## Plugin Reloads

When plugins are reloaded:

- Refresh the registry.
- Refresh generated MCP resources and tools.
- Notify MCP clients if the server supports list-changed notifications.
- Include schema/version identifiers so clients can detect stale tool metadata.

## Error Handling

Use predictable structured errors:

- Unknown metric.
- Input validation failure.
- Redis unavailable.
- Worker failure.
- Task cancelled.
- Result expired.
- Result file missing.

Include concise human-readable messages, but also include machine-readable error
types.

## Observability

Log enough to debug agent behavior:

- Authenticated user identity if available from the reverse proxy.
- MCP client name/version if provided.
- Tool name.
- Metric name.
- Job ID.
- Validation failures.
- Worker failures.
- Result retrievals.
- Cancellation requests.

## Initial Tests

Add focused tests for:

- Resource listing.
- Metric description generation.
- Tool schema generation.
- Input validation through the MCP path.
- Successful task submission with Celery mocked or isolated.
- Result retrieval from Redis.
- File-result handle behavior.
- Unknown metric errors.
- Plugin reload schema refresh.

## Exit Criteria

The first version is complete when:

- An MCP client can discover metrics.
- An MCP client can describe a metric.
- An MCP client can submit a valid metric job.
- An MCP client can retrieve a completed JSON result.
- File-producing metrics return a handle instead of inline binary content.
- Validation errors are clear and structured.
- Existing REST and WebSocket behavior continues to work.

