---
title: Testing And Quality
description: Commands for validating Lyra code, clients, and documentation.
---

Run focused checks while developing, then run the broader suite before handing off a change.

## Python Tests

Run the full Python test suite:

```bash
uv run pytest
```

Run a focused test file:

```bash
uv run pytest tests/test_jobs_route.py
```

## Type Checking

Run ty:

```bash
uv run ty check
```

## Linting

Run ruff on touched files when making a narrow change:

```bash
uv run ruff check path/to/file.py tests/test_file.py
```

Run full ruff before a broader handoff:

```bash
uv run ruff check
```

## Docs Checks

Install docs dependencies:

```bash
npm ci --prefix docs
```

Run Starlight and TypeScript checks:

```bash
npm run check --prefix docs
```

Generate the Python API reference without running a full docs build:

```bash
npm run generate:api --prefix docs
```

Build the static site:

```bash
npm run build --prefix docs
```

Check npm advisories for the docs package:

```bash
npm audit --prefix docs
```

## Docs Accuracy Scans

Scan docs for non-current execution terms before publishing a docs-focused change:

```bash
rg -f path/to/local-denylist.txt README.md docs/src/content/docs docs/public/llms.txt
```

Scan documented route paths against current route modules:

```bash
rg "@router\\." lyra_app/routes
rg "`/(data-types|metrics|jobs|lookups/met-zones|admin/)" docs/src/content/docs README.md
```

Use the current route source as the final authority.
