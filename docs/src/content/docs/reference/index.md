---
title: Reference
description: Generated HTTP, Python, configuration, CLI, and MCP contracts.
---

Reference pages are regenerated from source before every docs check and build:

- [HTTP API](./generated/http/) from FastAPI routers and OpenAPI models;
- [Python APIs](./generated/python/) from signatures, types, docstrings, and
  Pydantic fields;
- [Configuration](./generated/configuration/) from validated config models;
- [Command line](./generated/cli/) from the real argument parsers;
- [MCP](./generated/mcp/) from registered tool contracts.

Machine-readable artifacts are published beside the site:

- `openapi.json`
- `config.schema.json`
- `mcp-tools.json`
- `llms.txt`
- `llms-full.txt`

Do not edit generated pages. Update the owning source contract or its metadata.
