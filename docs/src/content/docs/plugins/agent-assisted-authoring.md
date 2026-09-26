---
title: Agent-assisted Authoring
description: Adapt an existing calculation with the portable Lyra metric skill.
---

The `lyra-metric` agent skill guides a coding agent through adapting an existing
Python workflow to Lyra. It supports creating a new plugin package and adding a
metric to an existing plugin. It preserves the calculation behind a small adapter,
declares its input and output contracts, and validates the integration locally.

An agent skill is a folder of instructions and references used by your coding
agent. It is separate from a Lyra plugin, which is the Python package exposing
metrics to the platform.

## Obtain and install the skill

The complete skill is stored in
[`skills/lyra-metric/`](https://github.com/RodolfoFigueroa/lyra/tree/main/skills/lyra-metric).
Choose a product release tag (`lyra-vX.Y.Z`) or commit containing the skill,
download or check out that repository revision, and retain the entire directory:

```text
lyra-metric/
  SKILL.md
  references/
    authoring.md
  scripts/
    __init__.py
    check_compatibility.py
```

Follow your coding agent's documented skill installation mechanism to install
that directory. Keep the references and scripts alongside `SKILL.md`; installing only the
instruction file leaves the bundled guidance and compatibility check unavailable.
The skill works from an external workflow or plugin repository without a Lyra checkout.

The skill is versioned with this repository. Installed copies do not update
automatically. Record the source revision and replace the complete directory
when upgrading. The bundled **lyra-authoring-1** reference teaches the public
contract, including **manifest format 5**. From the target project, the agent runs:

```sh
uv run python <skill-directory>/scripts/check_compatibility.py
```

Replace `<skill-directory>` with the installed skill's directory. The helper uses
the existing environment and reports its SDK version plus a concise compatibility
result. Exit codes are 0 for passing checks, 1 for a contract mismatch, and 2 when
the check cannot complete. It does not import the target plugin, execute metrics,
write files, or connect to services. Version numbers do not determine acceptance.

Agents use the reference and helper instead of routinely reading installed Lyra
source or loading entire schemas. A specific unresolved error may justify targeted
source inspection. Unresolved incompatibilities require clarification rather than
automatic upgrades. Passing checks cover the tested baseline; actual adapter tests,
manifest generation, metric inspection, and freshness checks remain required.
The helper cannot verify application responsibilities or deployment readiness.

## Prepare the development environment

Provide a Python development environment managed with `uv`, the target SDK,
and the workflow's dependencies. The skill does not install its own runtime or
provide access to external datasets and services. Supply existing documentation,
tests, and a small input fixture with known results when available.

Local parameter preparation and result normalization do not require a running
API, Redis, or PostGIS. The calculation itself may still require data, credentials,
or services. Tell the agent about those requirements and the checks it can run.
See the [authoring guide](../authoring/) for the SDK contracts and local helpers.

Normal Lyra workers initialize Earth Engine before loading plugins, using
`earth_engine.project` and `earth_engine.service_account_file` from deployment
configuration. The skill explains this responsibility; the agent should not ask
you to choose authentication ownership or add credential/project parameters to a
metric. Standalone live tests require separate setup, while offline tests use
mocks. Imports and factories must remain usable without initializing Earth Engine.

## Create a new plugin

In the repository containing your workflow, give the agent a request such as:

> Use the lyra-metric skill to adapt calculate_flood_risk in flood_risk.py into
> a new Lyra plugin. Preserve its calculation. Read the existing documentation
> and tests, and ask me about missing scientific or contract details before
> implementing behavior that depends on them.

The agent creates the adapter, parameter model, output declaration, plugin
factory, package configuration, generated manifest, and relevant tests. It uses
the original calculation's results to check that the adapter preserves behavior.

## Extend an existing plugin

In a plugin repository that already exposes metrics, give a request such as:

> Use the lyra-metric skill to add calculate_flood_risk from flood_risk.py to this
> plugin. Follow its existing conventions, retain the existing metrics, and ask
> me about any missing information needed to define the new metric.

The agent adds the adapter and tests, extends the existing factory, and
regenerates the manifest. Existing registrations and behavior are retained.

## Resolve uncertainty and review the result

The skill requires the agent to use code, documentation, tests, and your answers
as evidence. It must ask when a choice could change the calculation, its public
contract, or the interpretation of results. It first uses the bundled platform
contract: locations contain Polygon/MultiPolygon features and bounds contain one
Polygon, both with a declared CRS. It then inspects the workflow for parameter
semantics, units, required properties, reprojection, datasets, resolution, and
output-to-feature mapping. It must ask when a material decision remains, sources
disagree, or a workflow does not fit Lyra's contracts, such as producing
parameter-dependent table columns.

Existing reprojection to EPSG:4326 does not need reconfirmation and does not mean
incoming geometry must already use that CRS. Missing documentation about coverage
or region sizes can be reported as an unverified limitation without blocking the
adapter or asserting universal scientific validity. Choosing an unspecified
raster reduction resolution or missing-data behavior still requires evidence or
a user decision. The agent should ask targeted questions, not repeat a checklist
of platform facts and hypothetical uncertainties.

Dependent work stays pending until you answer. Silence does not authorize a
guess. The agent may continue independent work and make routine internal naming
or layout choices that do not affect behavior. Skill instructions guide behavior;
they do not mechanically guarantee that an agent will comply.

The handoff should identify changed files, validation commands and results,
unresolved questions, and checks blocked by unavailable data or services. Review
the adapter's spatial assumptions and result semantics alongside its tests.
Publishing and deployment are separate steps; see
[Publish and debug](../publish-and-debug/) when the plugin is ready.
