"""Generate all source-derived Lyra documentation and machine contracts."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from lyra.api.generator import build_parser as build_client_parser
from lyra.sdk.plugin_cli import build_parser as build_plugin_parser
from lyra.tui.__main__ import build_parser as build_tui_parser

from docs.scripts.generate_api_docs import generate_api_docs
from lyra_app.config import LyraConfig
from lyra_app.mcp.models import TOOL_CONTRACTS
from lyra_app.mcp.server import SERVER_INSTRUCTIONS
from lyra_app.routes import admin, data_types, health, jobs, met_zone, metrics
from lyra_app.version import APP_VERSION
from lyra_app.worker_launcher import build_parser as build_worker_parser

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
CONTENT_DIR = DOCS_DIR / "src" / "content" / "docs"
GENERATED_DIR = CONTENT_DIR / "reference" / "generated"
PUBLIC_DIR = DOCS_DIR / "public"
NAVIGATION_PATH = DOCS_DIR / "navigation.json"
SITE = "https://rodolfofigueroa.github.io"
DEFAULT_BASE = "/lyra/dev"

DEFAULT_OVERRIDES: dict[str, object] = {
    "api.forwarded_allow_ips": ["127.0.0.1"],
    "database.api.pool_size": 5,
    "database.api.max_overflow": 0,
    "database.api.pool_timeout_seconds": 2.0,
    "database.api.connect_timeout_seconds": 5,
    "database.api.statement_timeout_ms": 10_000,
    "database.api.pool_recycle_seconds": 900,
    "database.spatial.pool_size": 2,
    "database.spatial.max_overflow": 0,
    "database.spatial.pool_timeout_seconds": 2.0,
    "database.spatial.connect_timeout_seconds": 5,
    "database.spatial.statement_timeout_ms": 25_000,
    "database.spatial.pool_recycle_seconds": 900,
    "database.worker.pool_size": 1,
    "database.worker.max_overflow": 0,
    "database.worker.pool_timeout_seconds": 5.0,
    "database.worker.connect_timeout_seconds": 5,
    "database.worker.statement_timeout_ms": 300_000,
    "database.worker.pool_recycle_seconds": 900,
    "plugins.initial_repos": [],
}

ENV_FIELDS = {
    "database.host": ("LYRA_POSTGRES_HOST", False),
    "database.port": ("LYRA_POSTGRES_PORT", False),
    "database.name": ("LYRA_POSTGRES_DB", False),
    "database.user": ("LYRA_POSTGRES_USER", False),
    "database.password": ("LYRA_POSTGRES_PASSWORD", True),
    "admin.api_key": ("LYRA_ADMIN_API_KEY", True),
    "agent.api_key": ("LYRA_AGENT_API_KEY", True),
}


class DocumentationGenerationError(ValueError):
    """Raised when documentation inputs violate the generation contract."""


@dataclass(frozen=True)
class ConfigRow:
    path: str
    type_name: str
    required: bool
    default: str
    constraints: str
    description: str


def main() -> None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    generate_api_docs()
    openapi = generate_http_reference()
    config_schema = generate_config_reference()
    generate_cli_reference()
    generate_mcp_reference()
    write_json(PUBLIC_DIR / "openapi.json", openapi)
    write_json(PUBLIC_DIR / "config.schema.json", config_schema)
    write_json(
        PUBLIC_DIR / "mcp-tools.json",
        {
            "instructions": SERVER_INSTRUCTIONS,
            "tools": [
                {
                    "name": contract.name,
                    "description": contract.description,
                    "read_only": contract.read_only,
                    "idempotent": contract.idempotent,
                    "open_world": contract.open_world,
                    "input_schema": contract.input_schema,
                    "output_schema": contract.output_schema,
                }
                for contract in TOOL_CONTRACTS
            ],
        },
    )
    generate_llm_files()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{content.rstrip()}\n", encoding="utf-8")


def write_json(path: Path, content: object) -> None:
    write_text(path, json.dumps(content, indent=2, sort_keys=True))


def frontmatter(title: str, description: str) -> str:
    return f"---\ntitle: {title}\ndescription: {description}\n---\n"


def create_openapi_app() -> FastAPI:
    app = FastAPI(title="Lyra API", version=APP_VERSION)
    for router in (
        admin.router,
        health.router,
        jobs.router,
        data_types.router,
        metrics.router,
        met_zone.router,
    ):
        app.include_router(router)
    return app


def generate_http_reference() -> dict[str, Any]:
    schema = create_openapi_app().openapi()
    lines = [
        frontmatter(
            "HTTP API Reference",
            "Generated routes, authentication boundaries, and OpenAPI operations.",
        ),
        "This page is generated from the FastAPI routers. Download the complete ",
        "[`openapi.json`](../../../openapi.json) contract for request and response "
        "schemas.",
        "",
        "| Method | Path | Authentication | Operation |",
        "| --- | --- | --- | --- |",
    ]
    for path, path_item in sorted(schema["paths"].items()):
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            security = operation.get("security", [])
            schemes = sorted(item for entry in security for item in entry)
            authentication = ", ".join(schemes) if schemes else "Public"
            summary = operation.get("summary", operation["operationId"])
            lines.append(
                f"| `{method.upper()}` | `{path}` | {authentication} | {summary} |"
            )
    write_text(GENERATED_DIR / "http.md", "\n".join(lines))
    return schema


def resolve_schema(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    prefix = "#/$defs/"
    if not reference.startswith(prefix):
        message = f"Unsupported schema reference: {reference}"
        raise DocumentationGenerationError(message)
    return root["$defs"][reference.removeprefix(prefix)]


def schema_type(schema: dict[str, Any]) -> str:
    if "type" in schema:
        value = schema["type"]
        return " | ".join(value) if isinstance(value, list) else str(value)
    if "anyOf" in schema:
        return " | ".join(schema_type(item) for item in schema["anyOf"])
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return "object"


def schema_constraints(schema: dict[str, Any]) -> str:
    names = (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
    )
    return ", ".join(f"{name}={schema[name]}" for name in names if name in schema)


def config_rows(
    schema: dict[str, Any],
    *,
    root: dict[str, Any],
    prefix: str = "",
    parent_required: bool = True,
) -> list[ConfigRow]:
    resolved = resolve_schema(schema, root)
    properties = resolved.get("properties", {})
    required = set(resolved.get("required", []))
    rows: list[ConfigRow] = []
    for name, raw_property in properties.items():
        path = f"{prefix}.{name}" if prefix else name
        property_schema = resolve_schema(raw_property, root)
        nested = property_schema.get("properties")
        if nested is not None:
            rows.extend(
                config_rows(
                    raw_property,
                    root=root,
                    prefix=path,
                    parent_required=parent_required and name in required,
                )
            )
            continue
        if path == "workers":
            worker_schema = property_schema.get("additionalProperties", {})
            rows.extend(
                config_rows(
                    worker_schema,
                    root=root,
                    prefix="workers.<name>",
                    parent_required=parent_required and name in required,
                )
            )
            continue
        description = property_schema.get("description", "").strip()
        if not description:
            message = f"Config field lacks a description: {path}"
            raise DocumentationGenerationError(message)
        default_value = property_schema.get("default", DEFAULT_OVERRIDES.get(path, "—"))
        default = (
            json.dumps(default_value, sort_keys=True)
            if default_value != "—"
            else default_value
        )
        rows.append(
            ConfigRow(
                path=path,
                type_name=schema_type(raw_property),
                required=parent_required and name in required,
                default=default,
                constraints=schema_constraints(property_schema) or "—",
                description=description,
            )
        )
    return rows


def generate_config_reference() -> dict[str, Any]:
    schema = LyraConfig.model_json_schema()
    rows = config_rows(schema, root=schema)
    known_paths = {row.path for row in rows}
    missing_env = sorted(set(ENV_FIELDS) - known_paths)
    if missing_env:
        message = f"Unknown environment config fields: {missing_env}"
        raise DocumentationGenerationError(message)

    toml_rows = [row for row in rows if row.path not in ENV_FIELDS]
    lines = [
        frontmatter(
            "Configuration Reference",
            "Generated TOML fields, environment variables, defaults, and constraints.",
        ),
        "Lyra reads TOML from `/lyra_data/config/lyra.toml`. Database connection ",
        "values and API credentials come only from environment variables.",
        "",
        "## TOML fields",
        "",
        "| Field | Type | Required | Default | Constraints | Description |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        (
            f"| `{row.path}` | `{row.type_name}` | {'yes' if row.required else 'no'} "
            f"| `{row.default}` | {row.constraints} | {row.description} |"
        )
        for row in toml_rows
    )
    lines.extend(
        [
            "",
            "## Environment variables",
            "",
            "| Variable | Config value | Secret | Description |",
            "| --- | --- | --- | --- |",
        ]
    )
    by_path = {row.path: row for row in rows}
    for path, (variable, secret) in ENV_FIELDS.items():
        lines.append(
            f"| `{variable}` | `{path}` | {'yes' if secret else 'no'} | "
            f"{by_path[path].description} |"
        )
    write_text(GENERATED_DIR / "configuration.md", "\n".join(lines))
    return schema


def generate_cli_reference() -> None:
    parsers = (
        ("lyra-client", build_client_parser()),
        ("lyra-plugin", build_plugin_parser()),
        ("lyra-tui", build_tui_parser()),
        ("worker launcher", build_worker_parser()),
    )
    lines = [
        frontmatter(
            "CLI Reference",
            "Generated help for Lyra's supported command-line interfaces.",
        )
    ]
    for title, parser in parsers:
        lines.extend(
            [
                f"## {title}",
                "",
                "```text",
                parser.format_help().rstrip(),
                "```",
                "",
            ]
        )
    write_text(GENERATED_DIR / "cli.md", "\n".join(lines))


def generate_mcp_reference() -> None:
    lines = [
        frontmatter(
            "MCP Reference",
            "Generated Lyra MCP server instructions and strict tool contracts.",
        ),
        SERVER_INSTRUCTIONS,
        "",
        "The Streamable HTTP mount uses `LYRA_AGENT_API_KEY` Bearer authentication. ",
        "See the configuration reference for the mount path and public base URL.",
    ]
    for contract in TOOL_CONTRACTS:
        annotations = ["read-only" if contract.read_only else "writes state"]
        if contract.idempotent:
            annotations.append("idempotent")
        if contract.open_world:
            annotations.append("open-world")
        lines.extend(
            [
                "",
                f"## `{contract.name}`",
                "",
                contract.description,
                "",
                f"Annotations: {', '.join(annotations)}.",
                "",
                "### Input schema",
                "",
                "```json",
                json.dumps(contract.input_schema, indent=2, sort_keys=True),
                "```",
                "",
                "### Output schema",
                "",
                "```json",
                json.dumps(contract.output_schema, indent=2, sort_keys=True),
                "```",
            ]
        )
    write_text(GENERATED_DIR / "mcp.md", "\n".join(lines))


def read_frontmatter(path: Path) -> tuple[str, str, str]:
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, flags=re.DOTALL)
    if match is None:
        message = f"Missing frontmatter: {path.relative_to(ROOT)}"
        raise DocumentationGenerationError(message)
    metadata, body = match.groups()
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            message = f"Invalid frontmatter line in {path}: {line}"
            raise DocumentationGenerationError(message)
        parsed_value = value.strip()
        if parsed_value.startswith('"'):
            parsed_value = json.loads(parsed_value)
        fields[key.strip()] = parsed_value
    try:
        return fields["title"], fields["description"], body.strip()
    except KeyError as exc:
        message = f"Incomplete frontmatter: {path.relative_to(ROOT)}"
        raise DocumentationGenerationError(message) from exc


def navigation_pages() -> list[Path]:
    navigation = json.loads(NAVIGATION_PATH.read_text(encoding="utf-8"))
    pages: list[Path] = []
    for group in navigation:
        for slug in group["items"]:
            if slug == "reference/generated":
                pages.extend(sorted(GENERATED_DIR.rglob("*.md")))
                continue
            candidates = (CONTENT_DIR / f"{slug}.md", CONTENT_DIR / f"{slug}.mdx")
            page = next(
                (candidate for candidate in candidates if candidate.is_file()), None
            )
            if page is None:
                message = f"Navigation page does not exist: {slug}"
                raise DocumentationGenerationError(message)
            pages.append(page)
    return pages


def page_slug(path: Path) -> str:
    relative = path.relative_to(CONTENT_DIR).with_suffix("")
    value = relative.as_posix()
    if value == "index":
        return ""
    return value.removesuffix("/index")


def normalize_mdx(body: str) -> str:
    body = re.sub(r"^import .*?;\n", "", body, flags=re.MULTILINE)

    def expand_code(match: re.Match[str]) -> str:
        attributes = dict(re.findall(r'(\w+)="([^"]+)"', match.group(0)))
        source = ROOT / attributes["path"]
        code = source.read_text(encoding="utf-8")
        region = attributes.get("region")
        if region:
            start_marker = f"# docs:start {region}"
            end_marker = f"# docs:end {region}"
            start = code.index(start_marker)
            end = code.index(end_marker)
            code = code[code.index("\n", start) + 1 : end]
        return f"```{attributes.get('lang', 'text')}\n{code.strip()}\n```"

    return re.sub(r"<CodeExample\b.*?\s*/>", expand_code, body, flags=re.DOTALL)


def generate_llm_files() -> None:
    base = os.environ.get("LYRA_DOCS_BASE", DEFAULT_BASE).rstrip("/")
    pages = navigation_pages()
    index_lines = [
        "# Lyra Documentation",
        "",
        "Canonical documentation and generated contracts for Lyra.",
        "",
    ]
    full_lines = ["# Lyra Documentation", ""]
    for path in pages:
        title, description, body = read_frontmatter(path)
        slug = page_slug(path)
        url = f"{SITE}{base}/{slug}/" if slug else f"{SITE}{base}/"
        index_lines.append(f"- [{title}]({url}): {description}")
        full_lines.extend([f"# {title}", "", description, "", normalize_mdx(body), ""])
    write_text(PUBLIC_DIR / "llms.txt", "\n".join(index_lines))
    write_text(PUBLIC_DIR / "llms-full.txt", "\n".join(full_lines))


if __name__ == "__main__":
    main()
