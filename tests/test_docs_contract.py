from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

from jsonschema import validate
from lyra.sdk import PluginDefinition, RunContext
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.job import TableJobResult
from lyra.sdk.plugin_cli import render_manifest

from docs.scripts import generate_docs
from docs.scripts.check_site import api_reference_failures
from docs.scripts.generate_docs import (
    CONTENT_DIR,
    ENV_FIELDS,
    config_rows,
    create_openapi_app,
    navigation_pages,
)
from docs.scripts.versioned_site import (
    Release,
    inject_selector,
    parse_release,
    version_manifest,
)
from lyra_app.config import LyraConfig
from lyra_app.mcp.models import TOOL_CONTRACTS_BY_NAME

if TYPE_CHECKING:
    import pytest

from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR, feature_collection

ROOT = Path(__file__).parents[1]


def test_navigation_is_complete_unique_and_resolvable() -> None:
    navigation = json.loads((ROOT / "docs" / "navigation.json").read_text())
    authored_slugs = [
        slug
        for group in navigation
        for slug in group["items"]
        if slug not in {"reference/generated", "api/lyra"}
    ]

    assert len(authored_slugs) == len(set(authored_slugs))
    assert all(path.is_relative_to(CONTENT_DIR) for path in navigation_pages())


def test_legacy_python_cleanup_preserves_other_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    legacy = tmp_path / "python"
    legacy.mkdir()
    (legacy / "old.md").write_text("obsolete")
    other = tmp_path / "configuration.md"
    other.write_text("keep")

    generate_docs.remove_legacy_python_reference()
    generate_docs.remove_legacy_python_reference()

    assert not legacy.exists()
    assert other.read_text() == "keep"


def test_llm_exports_link_to_versioned_python_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "PUBLIC_DIR", tmp_path)
    monkeypatch.setattr(generate_docs, "navigation_pages", list)
    monkeypatch.setenv("LYRA_DOCS_BASE", "/lyra/versions/test/")

    generate_docs.generate_llm_files()

    for name in ("llms.txt", "llms-full.txt"):
        content = (tmp_path / name).read_text()
        assert "/lyra/versions/test/api/lyra/llms.txt" in content
        assert "reference/generated/python" not in content


def test_api_reference_checker_validates_artifacts_and_anchors(tmp_path: Path) -> None:
    api = tmp_path / "api" / "lyra"
    api.mkdir(parents=True)
    for name in ("objects.inv", "llms.txt"):
        (api / name).write_text("artifact")
    (api / "index.html").write_text('<h2 id="lyra.Example">Example</h2>')
    for suffix in (".md", ".md.txt"):
        (tmp_path / f"api/lyra{suffix}").write_text("# lyra")
    symbols = [
        {"page": "api/lyra", "anchor": "", "kind": "module"},
        {"page": "api/lyra", "anchor": "lyra.Example", "kind": "class"},
    ]
    index = api / "symbols.json"
    index.write_text(json.dumps({"symbols": symbols}))
    assert api_reference_failures(tmp_path) == []

    (api / "index.html").write_text("<h2>Missing anchor</h2>")
    assert api_reference_failures(tmp_path) == [
        "Missing API symbol anchor: api/lyra#lyra.Example"
    ]
    (api / "objects.inv").unlink()
    assert "Missing or empty API artifact: api/lyra/objects.inv" in (
        api_reference_failures(tmp_path)
    )
    index.write_text("invalid json")
    assert "Invalid API symbol index" in api_reference_failures(tmp_path)


def test_api_reference_checker_rejects_missing_and_empty_index(tmp_path: Path) -> None:
    assert "Missing or empty API artifact: api/lyra/symbols.json" in (
        api_reference_failures(tmp_path)
    )
    api = tmp_path / "api" / "lyra"
    api.mkdir(parents=True)
    (api / "symbols.json").write_text('{"symbols": []}')
    assert "API symbol index must contain symbols" in api_reference_failures(tmp_path)


def test_cli_reference_includes_nested_admin_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    generate_docs.generate_cli_reference()
    reference = (tmp_path / "cli.md").read_text()

    assert "## lyra-admin\n" in reference
    assert "## lyra-admin plugins list\n" in reference
    assert "## lyra-admin config validate\n" in reference
    assert "## lyra-admin workers restart\n" not in reference
    assert "--restart-timeout" not in reference
    assert "## lyra-plugin\n" in reference
    assert "lyra-tui" not in reference


def test_generated_openapi_has_explicit_authentication_boundaries() -> None:
    schema = create_openapi_app().openapi()
    schemes = schema["components"]["securitySchemes"]

    assert set(schemes) == {"AdminBearer", "AgentBearer"}
    assert schema["paths"]["/live"]["get"].get("security", []) == []
    assert schema["paths"]["/jobs"]["post"]["security"] == [{"AgentBearer": []}]
    assert schema["paths"]["/admin/jobs"]["get"]["security"] == [{"AdminBearer": []}]
    assert all(
        operation.get("tags")
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    )


def test_every_leaf_config_field_is_documentable() -> None:
    schema = LyraConfig.model_json_schema()
    rows = config_rows(schema, root=schema)
    paths = {row.path for row in rows}

    assert rows
    assert all(row.description for row in rows)
    assert set(ENV_FIELDS) <= paths


def test_environment_example_covers_generated_environment_reference() -> None:
    example = (ROOT / ".env.example").read_text()

    assert all(variable in example for variable, _secret in ENV_FIELDS.values())


def test_canonical_plugin_example_is_the_only_documented_example() -> None:
    example = ROOT / "examples" / "lyra-plugin"

    assert (example / "lyra.plugin.json").is_file()
    assert (example / "smoke_plugin" / "metrics.py").is_file()
    assert (example / "smoke_plugin" / "plugin.py").is_file()
    assert not (
        ROOT / "tests" / "fixtures" / "plugins" / "smoke_plugin" / "lyra.plugin.json"
    ).exists()


def test_release_parser_accepts_product_and_legacy_application_tags() -> None:
    assert parse_release("lyra-v0.8.0") == Release((0, 8, 0), "lyra-v0.8.0")
    assert parse_release("lyra-app-v0.7.0") == Release((0, 7, 0), "lyra-app-v0.7.0")
    assert parse_release("lyra-sdk-v9.0.0") is None
    assert parse_release("lyra-api-v9.0.0") is None
    assert parse_release("lyra-app-v0.5.0") is None
    assert parse_release("lyra-v1.0") is None


def test_version_selector_marks_each_tree_and_is_idempotent(tmp_path: Path) -> None:
    releases = [
        Release((0, 7, 0), "lyra-v0.7.0"),
        Release((0, 6, 0), "lyra-app-v0.6.0"),
    ]
    manifest = version_manifest(releases)
    page = tmp_path / "index.html"
    page.write_text("<html><body><main>Lyra</main></body></html>")

    inject_selector(tmp_path, manifest, "/lyra/dev")
    inject_selector(tmp_path, manifest, "/lyra/dev")

    rendered = page.read_text()
    assert rendered.count('class="lyra-version-selector"') == 1
    assert '<option value="/lyra/dev" selected>' in rendered
    assert {item["base"] for item in manifest} == {
        "/lyra",
        "/lyra/dev",
        "/lyra/versions/0.7.0",
        "/lyra/versions/0.6.0",
    }


def test_mcp_workflow_examples_match_serialization_schemas() -> None:
    page = (CONTENT_DIR / "use" / "mcp.md").read_text()
    examples = re.findall(r"```json\n(.*?)\n```", page, re.DOTALL)
    assert len(examples) == 11
    for example in examples:
        payload = json.loads(example)
        if "met_zone_code" in payload:
            validate(payload, TOOL_CONTRACTS_BY_NAME["lyra_run_metric"].input_schema)
            continue
        if "status" not in payload and "error" in payload:
            assert payload["error"]["code"] == "result_not_found"
            continue
        name = (
            "lyra_run_metric"
            if "reused" in payload
            else "lyra_download_result"
            if "format" in payload
            else "lyra_get_job_result"
        )
        validate(payload, TOOL_CONTRACTS_BY_NAME[name].output_schema)


def test_published_requests_validate_against_example_manifest() -> None:
    manifest = json.loads(render_manifest(SMOKE_PLUGIN_DIR))
    schemas = {
        metric["name"]: metric["request_schema"] for metric in manifest["metrics"]
    }
    requests = []
    for relative in ("plugins/authoring.md", "use/rest-api.md"):
        content = (CONTENT_DIR / relative).read_text()
        for block in re.findall(r"```json\n(.*?)\n```", content, re.DOTALL):
            payload = json.loads(block)
            if "metric" in payload and "input" in payload:
                validate(payload["input"], schemas[payload["metric"]])
                requests.append(payload)
    assert {request["metric"] for request in requests} == {
        "smoke_table_metric",
        "smoke_file_metric",
    }


def test_published_authoring_adapter_runs_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = (CONTENT_DIR / "plugins/authoring.md").read_text()
    source = re.findall(r"```python\n(.*?)\n```", content, re.DOTALL)[0]
    path = tmp_path / "documented_plugin.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("documented_plugin", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "documented_plugin", module)
    spec.loader.exec_module(module)

    plugin = PluginDefinition(metrics=[module.run_table])
    location = GeoJSON.model_validate(feature_collection(("a", "b")))
    context = create_autospec(RunContext, instance=True)
    parameters = plugin.prepare_parameters("smoke_table_metric", {"value": 7})
    frame = module.run_table(parameters=parameters, location=location, context=context)
    result = plugin.normalize_result(
        "smoke_table_metric", frame, job_id="local-test", location=location
    )
    assert isinstance(result, TableJobResult)
    assert result.data == [[7], [7]]
    assert result.index == ["a", "b"]
    context.check_cancelled.assert_called_once_with()
