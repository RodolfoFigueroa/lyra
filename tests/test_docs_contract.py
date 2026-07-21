from __future__ import annotations

import json
from pathlib import Path

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

ROOT = Path(__file__).parents[1]


def test_navigation_is_complete_unique_and_resolvable() -> None:
    navigation = json.loads((ROOT / "docs" / "navigation.json").read_text())
    authored_slugs = [
        slug
        for group in navigation
        for slug in group["items"]
        if slug != "reference/generated"
    ]

    assert len(authored_slugs) == len(set(authored_slugs))
    assert all(path.is_relative_to(CONTENT_DIR) for path in navigation_pages())


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
