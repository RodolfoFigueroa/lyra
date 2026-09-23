---
title: Reference
description: Generated HTTP, Python, configuration, CLI, and MCP contracts.
---

Reference pages are generated from source:

- [HTTP API](./generated/http/) from FastAPI routers and OpenAPI models;
- [Python APIs](../api/lyra/) automatically discovered by `starlight-pydocs`
  from signatures, types, docstrings, and Pydantic fields during Astro builds;
- [Configuration](./generated/configuration/) from validated config models;
- [Command line](./generated/cli/) from the real argument parsers;
- [MCP](./generated/mcp/) from registered tool contracts.

Machine-readable artifacts are published beside the site:

- `openapi.json`
- `config.schema.json`
- `mcp-tools.json`
- `llms.txt`
- `llms-full.txt`
- `api/lyra/llms.txt` (complete Python API text)
- `api/lyra/symbols.json` and `api/lyra/objects.inv`

The site-wide LLM exports link to the separate Python API export. Each Python
module page also offers a Markdown download. The configuration reference retains
schema constraints and defaults that the Python API renderer does not display.

`npm run generate` refreshes the HTTP, configuration, CLI, MCP, and site-wide LLM
exports. It runs before `npm run dev`, `npm run check`, and `npm run build`.
The Python API plugin reads all three package source roots through the locked uv
development environment; adding public modules or exports needs no documentation
symbol list. Standard visibility rules determine the documented members, including
public Pydantic validators.

Do not edit generated pages. Update the owning source contract or its metadata.
