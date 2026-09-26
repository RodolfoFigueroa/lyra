from __future__ import annotations

import builtins
import importlib.util
import runpy
import shutil
import socket
import sys
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pydantic
import pytest
from lyra import sdk
from lyra.sdk.models.plugin import PluginManifest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import ModuleType

SCRIPT = Path(__file__).parents[1] / "skills/lyra-metric/scripts/check_compatibility.py"


@pytest.fixture
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compatibility_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_sdk_passes(
    checker: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checker.main() == 0
    output = capsys.readouterr().out
    assert checker.CONTRACT_REVISION in output
    assert "Installed lyra-sdk:" in output
    assert "PASS:" in output
    assert len(output.splitlines()) == 3


@pytest.mark.parametrize("installed_version", ["0.0.0", "999.0.0"])
def test_version_is_diagnostic_only(
    checker: ModuleType, monkeypatch: pytest.MonkeyPatch, installed_version: str
) -> None:
    monkeypatch.setattr(checker, "version", lambda _: installed_version)
    assert checker.main() == 0


def test_missing_export_is_mismatch(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delattr(sdk, "FileOutput")
    assert checker.main() == 1
    assert "public authoring exports missing: FileOutput" in capsys.readouterr().out


@pytest.mark.parametrize("failure", [ModuleNotFoundError, RuntimeError])
def test_unavailable_environment(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: type[Exception],
) -> None:
    def fail_import(name: str) -> ModuleType:
        raise failure(name)

    monkeypatch.setattr(checker.importlib, "import_module", fail_import)
    assert checker.main() == 2
    output = capsys.readouterr().out
    assert "UNAVAILABLE:" in output
    assert "Traceback" not in output


@pytest.mark.parametrize("defect", ["geometry", "count", "crs"])
def test_spatial_schema_mismatch(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    defect: str,
) -> None:
    schema = deepcopy(sdk.LocationInput.model_json_schema())
    if defect == "geometry":
        schema["$defs"]["Feature"]["properties"]["geometry"] = {
            "$ref": "#/$defs/PolygonGeometry"
        }
    elif defect == "count":
        schema["properties"]["features"]["minItems"] = 0
    else:
        schema["required"].remove("crs")
    monkeypatch.setattr(sdk.LocationInput, "model_json_schema", lambda: schema)
    assert checker.main() == 1
    output = capsys.readouterr().out
    assert "MISMATCH: LocationInput:" in output
    assert len(output) < 700
    assert "$defs" not in output


def test_spatial_validation_drift_is_detected(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sdk.BoundsInput, "model_validate", lambda payload: payload)
    assert checker.main() == 1


def test_parameter_validation_drift_is_detected(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sdk.PluginDefinition,
        "prepare_parameters",
        lambda *_: pydantic.create_model("Prepared", value=(int, 2))(),
    )
    assert checker.main() == 1


def test_manifest_mismatch(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        PluginManifest,
        "model_dump",
        lambda *_args, **_kwargs: {
            "schema_version": 999,
            "metrics": [],
        },
    )
    assert checker.main() == 1
    assert "expected format 5, observed 999" in capsys.readouterr().out


def test_baseline_definition_rejected(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(**_kwargs: object) -> None:
        msg = "Definition rejected"
        raise sdk.PluginDefinitionError(msg)

    monkeypatch.setattr(sdk, "metric", reject)
    assert checker.main() == 1


def test_unexpected_execution_error(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_args: object) -> None:
        msg = "Internal check failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(checker, "spatial_checks", fail)
    assert checker.main() == 2
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "Traceback" not in output


def test_copied_skill_without_checkout_or_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    copied = tmp_path / "installed skill"
    shutil.copytree(SCRIPT.parents[1], copied)
    target = tmp_path / "target"
    target.mkdir()
    (target / "workflow.py").write_text("raise RuntimeError('workflow imported')\n")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        namespace: Mapping[str, object] | None = None,
        local_namespace: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        assert name.split(".", maxsplit=1)[0] not in {"lyra_app", "ee", "workflow"}
        return original_import(name, namespace, local_namespace, fromlist, level)

    def forbidden_connection(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Compatibility check attempted network access")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(socket.socket, "connect", forbidden_connection)
    monkeypatch.setattr(socket, "create_connection", forbidden_connection)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.chdir(target)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(
            str(copied / "scripts/check_compatibility.py"), run_name="__main__"
        )
    assert result.value.code == 0
    assert "PASS:" in capsys.readouterr().out
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_unit_contract_mismatch(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    schema = sdk.TableColumn.model_json_schema()
    schema["required"].remove("unit")
    monkeypatch.setattr(sdk.TableColumn, "model_json_schema", lambda: schema)
    assert checker.main() == 1
    assert "units: expected a required unit field" in capsys.readouterr().out
