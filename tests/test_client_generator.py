import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from lyra.api.generator import (
    ClientGenerationError,
    ClientGenerationWarning,
    canonical_catalog_json,
    generate_client,
    render_package,
)
from lyra.sdk import CVEGEOList, MetZoneCode, TableJobResult
from lyra.sdk.models.metric import MetricCatalogResponse

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "generated_client"
CATALOG_PATH = FIXTURE_ROOT / "lyra-catalog.json"


def _catalog() -> MetricCatalogResponse:
    return MetricCatalogResponse.model_validate_json(CATALOG_PATH.read_text())


def _import_generated(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    for name in list(sys.modules):
        if name == "representative_lyra" or name.startswith("representative_lyra."):
            del sys.modules[name]
    return importlib.import_module("representative_lyra")


def test_catalog_fixture_is_canonical_and_round_trippable() -> None:
    catalog = _catalog()
    canonical = canonical_catalog_json(catalog)

    parsed = MetricCatalogResponse.model_validate_json(canonical)
    assert canonical_catalog_json(parsed) == canonical


def test_generation_is_deterministic_and_check_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    catalog = _catalog()

    assert generate_client(catalog, package="sample_lyra", output=output)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    assert generate_client(
        catalog,
        package="sample_lyra",
        output=output,
        check=True,
    )
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before

    (output / "_contract.py").write_text("stale\n")
    stale_before = (output / "_contract.py").read_bytes()
    assert not generate_client(
        catalog,
        package="sample_lyra",
        output=output,
        check=True,
    )
    assert (output / "_contract.py").read_bytes() == stale_before


def test_generation_removes_stale_owned_files_and_preserves_unrelated(
    tmp_path: Path,
) -> None:
    output = tmp_path / "generated"
    output.mkdir()
    (output / "unrelated.py").write_text("VALUE = 1\n")
    (output / "old_generated.py").write_text("old\n")
    (output / ".lyra-client-manifest.json").write_text(
        json.dumps(
            {
                "generated_files": [
                    ".lyra-client-manifest.json",
                    "old_generated.py",
                ]
            }
        )
    )

    generate_client(_catalog(), package="sample_lyra", output=output)

    assert not (output / "old_generated.py").exists()
    assert (output / "unrelated.py").read_text() == "VALUE = 1\n"


def test_checked_in_fixture_exposes_typed_models_and_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = _import_generated(monkeypatch)

    request = generated.JobAccessibilityRequest(
        location=MetZoneCode(value="09.01"),
        limit=25,
        threshold=None,
    )

    assert request.model_dump(mode="json", exclude_unset=True) == {
        "location": {"data_type": "met_zone_code", "value": "09.01"},
        "limit": 25,
        "threshold": None,
    }
    assert hasattr(
        generated.Client("example.test", verify_catalog="off").metrics, "advanced_score"
    )
    assert hasattr(
        generated.Client("example.test", verify_catalog="off").metrics.raster_export,
        "run_to_file",
    )
    assert not hasattr(
        generated.Client(
            "example.test", verify_catalog="off"
        ).metrics.job_accessibility,
        "run_to_file",
    )


def test_generated_metric_validates_before_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = _import_generated(monkeypatch)
    metrics = importlib.import_module("representative_lyra._metrics")

    class NeverVerify:
        def verify(self) -> None:
            msg = "verification must follow local argument validation"
            raise AssertionError(msg)

    resource = metrics.JobAccessibilityMetric(object(), NeverVerify())

    with pytest.raises(generated.MetricArgumentsError):
        resource.run(location=CVEGEOList(value=["09"]), limit=0)


def test_unsupported_valid_schema_warns_and_uses_json_value(tmp_path: Path) -> None:
    catalog = _catalog()
    metric = catalog.metrics[0]
    properties = metric.request_schema["properties"]
    assert isinstance(properties, dict)
    schema = metric.request_schema | {
        "properties": properties | {"conditional": {"allOf": [{"type": "string"}]}},
    }
    changed = catalog.model_copy(
        update={
            "metrics": [
                metric.model_copy(update={"request_schema": schema}),
                *catalog.metrics[1:],
            ]
        }
    )

    with pytest.warns(ClientGenerationWarning, match="using JsonValue"):
        generate_client(changed, package="sample_lyra", output=tmp_path)

    files, warnings_found = render_package(changed)
    assert warnings_found
    assert "conditional: JsonValue" in files["_models.py"]


def test_broken_reference_fails_without_partial_output(tmp_path: Path) -> None:
    catalog = _catalog()
    metric = catalog.metrics[0]
    properties = metric.request_schema["properties"]
    assert isinstance(properties, dict)
    schema = metric.request_schema | {
        "properties": properties | {"broken": {"$ref": "#/$defs/Missing"}},
    }
    changed = catalog.model_copy(
        update={"metrics": [metric.model_copy(update={"request_schema": schema})]}
    )
    output = tmp_path / "generated"

    with pytest.raises(ClientGenerationError, match="broken reference"):
        generate_client(changed, package="sample_lyra", output=output)

    assert not output.exists()


def test_fixture_table_result_type_is_runtime_available() -> None:
    result = TableJobResult(
        job_id="job-1",
        index=["09"],
        columns=["jobs"],
        data=[[1.0]],
    )

    assert result.kind == "table"
