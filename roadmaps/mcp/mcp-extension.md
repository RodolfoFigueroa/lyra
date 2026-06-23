# MCP Extension

## Objective

After the first MCP version is working and validated, expand the interface so
arbitrary agents can use Lyra more accurately, safely, and efficiently.

## Candidate Extensions

### Per-Metric Tools

Generate one MCP tool per indicator when metadata quality is high enough.

Benefits:

- Agents can choose tools more reliably.
- Each tool can have a tailored description and schema.
- Tool names become meaningful in model planning.

Risks:

- Large catalogs can create tool-list noise.
- Plugin reloads require careful schema refresh.
- Tool descriptions need strong quality control.

### MCP Task Integration

If client support is adequate, map Lyra jobs onto MCP task semantics.

Keep fallback tools such as `submit_metric`, `get_metric_status`, and
`get_metric_result` for clients that do not support task features consistently.

### Progress Updates

Expose coarse progress states:

- Queued.
- Running.
- Preparing.
- Processing items.
- Aggregating.
- Writing result.
- Complete.

This may require plugin authors to optionally report progress from long-running
batched jobs.

### Cancellation

Improve cancellation support beyond connection disconnects.

Consider:

- Explicit `cancel_metric_job`.
- User-scoped cancellation permissions.
- Worker-side cleanup.
- Clear cancelled result state.

### Rich Resources

Add more useful resources:

- `lyra://metrics/{name}/examples`
- `lyra://metrics/{name}/output-schema`
- `lyra://metrics/{name}/limitations`
- `lyra://metrics/{name}/data-sources`
- `lyra://jobs/{job_id}`
- `lyra://results/{download_id}`

### Better Output Previews

For large results, provide structured previews:

- Row count.
- Bounds.
- Column summary.
- Primary value summary.
- Sample rows.
- Download handle.
- File metadata.

### Agent Evaluation Harness

Create a small set of natural-language evaluation prompts for agents:

- Choose the right indicator for a question.
- Ask for missing parameters instead of guessing.
- Use the correct geography type.
- Avoid expensive metrics when a cheaper one answers the question.
- Interpret output units correctly.
- Handle validation errors and retry.

This should help measure whether metadata and tool design are actually working.

### Plugin Author Tooling

Add checks for plugin repositories:

- Metadata contract validation.
- Input schema quality checks.
- Output schema validation.
- Example payload validation.
- Tool name collision checks.
- Missing unit or data source warnings.

These checks can run in plugin CI before a plugin is approved for the executor.

## Extension Priorities

Recommended order:

1. Output schemas.
2. Per-metric tools.
3. Result previews.
4. Plugin metadata validation tooling.
5. Progress and cancellation.
6. MCP task integration.
7. Agent evaluation harness.

## Things To Avoid

- Returning large files or huge GeoJSON directly in tool responses.
- Letting every plugin invent its own metadata shape.
- Treating free-form descriptions as a substitute for schemas.
- Creating a second execution path that bypasses Celery.
- Extending MCP before validating the first version through the real proxy.

## Exit Criteria

The extension phase is healthy when:

- Agents can choose indicators with less prompting.
- Agents can inspect outputs without loading oversized payloads.
- Plugin authors have clear validation feedback.
- Job progress and cancellation work predictably.
- The MCP interface remains stable as the plugin catalog grows.

