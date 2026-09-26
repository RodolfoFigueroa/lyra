---
name: lyra-metric
description: Adapt an existing Python geospatial calculation into a new Lyra plugin or add it as a metric to an existing plugin, preserving its behavior and validating its contract. Use for metric authoring, not running deployed metrics or deploying plugins.
---

# Adapt a workflow to Lyra

Produce a locally validated plugin around the user's existing calculation. Keep
the calculation independent of Lyra and introduce a small adapter. Read
[the authoring reference](references/authoring.md) before implementing the adapter.
It includes a complete example and the supported SDK contract; no Lyra repository
checkout is required.

## Establish the contract before coding

Inspect the target repository's instructions, calculation, documentation, tests,
dependency declarations, and existing plugin factory. Determine whether the user
wants a new plugin or a metric added to the existing one. Preserve existing
registrations, package layout, build backend, and unrelated configuration.

Identify the target SDK version from the project environment and dependency
configuration. The bundled reference covers the current authoring contract and
**manifest format 5**. Inspect the target's spatial schemas and matching
documentation, not just its version number. Ask about unresolved incompatibilities;
do not upgrade the SDK automatically.

Use the platform facts in the reference before asking the user: `location`
contains Polygon/MultiPolygon features; `bounds` contains exactly one Polygon.
Both carry a declared CRS. The normal Lyra worker initializes Earth Engine from
deployment configuration before loading plugins. These are integration facts,
not choices each metric author must make. A workflow's narrower requirements
still need evidence.

Trace ordinary parameters, spatial requirements, returned values, and external
data or services through the implementation and tests. Establish which facts are
documented, demonstrated, missing, or contradictory. A type annotation or a
successful sample run alone does not establish scientific meaning or validity
across regions and scales.

## Ask rather than guess

Ask the user when missing or conflicting information requires a decision that
affects the calculation, its public contract, or interpretation of results.
First consult the bundled platform contract and inspect workflow evidence.
The following are subjects to investigate, not a questionnaire to ask verbatim:

- Parameter meanings, defaults, valid ranges, and list semantics.
- CRS and reprojection requirements, geometry types, required zone attributes,
  geographic coverage, and valid spatial scales.
- Output meanings, units, column types, nullability, and the correspondence
  between returned rows and input features.
- Required datasets, credentials, services, and intended handling of missing data.

Use the reference's spatial-input and Earth Engine initialization contracts
without asking the user to reconfirm these platform facts.
Keep existing reprojection to EPSG:4326 without asking the user to reconfirm code
that already establishes it. Do not add a restriction requiring incoming geometry
to already be EPSG:4326 when the workflow handles reprojection.

Separate the size or administrative level of an input region from the raster
resolution used in a calculation. Inspect the chosen datasets and any available
matching documentation for actual limitations. Missing documentation of geographic
coverage or valid region sizes does not by itself require a new restriction or
block the adapter. State that applicability is undocumented or unverified; do not
claim universal coverage or scientific validity at all scales. Ask if a concrete
decision remains, such as choosing an unspecified reduction resolution or deciding
how to handle regions without source data. Continue otherwise.

Read available evidence first, but do not silently resolve contradictions between
code, documentation, tests, and user requirements. Explain the conflict and ask.
Do not invent plausible units, descriptions, ranges, defaults, or validation rules
to satisfy Lyra's metadata requirements. Do not replace an unknown unit with
"dimensionless" or an unknown meaning with a generic label.

Group related questions concisely. Say what is unknown and why the answer matters;
cite the relevant code or documentation when available. Suggested choices are
proposals, not decisions. Wait for answers before implementing dependent behavior.
Silence is not confirmation. Continue independent work only where it does not
embed the unresolved choice; do not register a guessed or placeholder contract.
If questions cannot be delivered interactively, report them and leave the
dependent work incomplete.

Routine private helper names and layout choices can follow repository conventions.
Public names should follow the user's terminology and existing project conventions;
ask when their meaning is ambiguous. Obtain agreement before changing the
calculation's scientific or computational semantics. Never silently drop rows,
fill missing results, relabel rows by position, or impose arbitrary output columns.

## Implement the smallest adapter

1. Preserve the existing callable and its defaults. Put ordinary inputs in a
   compatible parameter model, and use the SDK's resolved spatial arguments.
   Convert geometry for the existing callable without changing its CRS or
   attributes unless the workflow explicitly requires that transformation.
2. Declare the evidenced output contract. Table columns are fixed; if the
   calculation produces parameter-dependent columns or lacks a reliable row
   mapping, explain the mismatch and ask how to adapt it. File outputs must use
   the job temporary directory. Unsupported async or generator workflows need
   an agreed adaptation, not a silent algorithm rewrite.
3. Register the adapter in the plugin factory. For a new project, provide package
   metadata and manifest packaging configuration. For an existing project,
   extend its factory and configuration without replacing other metrics.
4. Declare directly imported dependencies using the repository's dependency
   policy. Use `uv` for Python environment and command management. Ask before
   adding runtime dependencies unless the user has authorized them; explain what
   each adds and whether existing dependencies suffice. Do not presume packages
   are available from a public index: use the project's configured package source
   or ask for the intended source.
   For Earth Engine workflows, use the runtime's initialized environment without
   adding authentication, credential discovery, project-ID parameters, or imports
   from `lyra_app`. Keep imports and factories usable without Earth Engine
   initialization; create initialization-dependent objects during execution.
   Preserve standalone initialization in a separate script or test harness when
   needed, rather than invoking it from plugin imports or handlers.
5. Generate `lyra.plugin.json` with `lyra-plugin build-manifest`; never author or
   patch its schema manually. Include installation metadata, but do not publish,
   deploy, commit, or install into a user's agent configuration as part of authoring
   unless separately requested.

## Verify and hand off

Compare the adapted calculation with the original on a small, representative
fixture. Test multiple features with distinct values and nontrivial identifiers
so swapped rows cannot pass accidentally. Exercise parameter preparation when a
parameter model is declared, and result normalization, not just direct function
calls. Verify invalid parameters,
column and row alignment, and file containment where applicable. When extending
a plugin, verify existing metrics remain registered and retain their behavior.

Run manifest generation, inspection, and freshness checks, plus the repository's
required formatting, linting, type checking, and tests. Use the existing project
environment. Do not perform wheel or package-installation smoke checks unless
requested. Do not start production jobs or expensive data retrieval to validate
an adapter without authorization. Local SDK validation does not require platform
services; the underlying workflow may require them.

If data, tools, or services are unavailable, report the exact blocked check and
what is still unverified. Do not fabricate fixtures with scientific assumptions
or describe a mocked calculation as validated against real data.

Finish with the changed files, established contract, tests and their outcomes,
remaining questions or limitations, and an example request using known valid
inputs. Distinguish a locally validated plugin from deployment verification.
