from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import requests
from lyra.api import admin_cli
from typing_extensions import override

from tests.test_api_client_jobs import FakeSyncResponse

if TYPE_CHECKING:
    from argparse import ArgumentParser
    from collections.abc import Sequence

    from lyra.sdk.types import JsonValue


REFRESH: dict[str, JsonValue] = {
    "refreshed": True,
    "error": None,
    "catalog_changed": False,
    "previous_catalog_fingerprint": "same",
    "catalog_fingerprint": "same",
    "assigned_metric_queues": [],
    "removed_metric_queues": [],
    "workers_restart_recommended": True,
}
REPO: dict[str, JsonValue] = {
    "id": "repo",
    "source": "owner/plugin@main",
    "ref": "main",
    "enabled": True,
    "resolved_ref": None,
}
METADATA: dict[str, JsonValue] = {
    "observed_at": None,
    "age_seconds": None,
    "stale": True,
    "last_error": "inspection unavailable",
}
WORKER: dict[str, JsonValue] = {
    "name": "worker",
    "configured": True,
    "observed": False,
    "status": "unknown",
    "queues": ["batch"],
    "active_count": None,
    "reserved_count": None,
    "scheduled_count": None,
}
READINESS: dict[str, JsonValue] = {
    "status": "ready",
    "api_version": "1.0.0",
    "redis": {"status": "ok"},
    "database": {"status": "ok"},
    "catalog_available": True,
    "catalog_error": None,
}


@dataclass(frozen=True)
class Case:
    command: str
    method: str
    path: str
    payload: dict[str, JsonValue]
    body: dict[str, JsonValue] | None = None
    params: dict[str, JsonValue] | None = None


CASES = (
    Case("health", "GET", "ready", READINESS),
    Case("health --live", "GET", "live", {"status": "ok", "api_version": "1.0.0"}),
    Case(
        "status",
        "GET",
        "admin/status",
        {
            "api_version": "1.0.0",
            "redis": {"status": "ok"},
            "metric_count": 1,
            "allowed_queues": ["batch"],
            "default_queue": "batch",
            "configured_worker_count": 1,
            "job_store_ttl_seconds": 600,
            "catalog_fingerprint": "catalog",
            "catalog_available": True,
            "catalog_error": None,
        },
    ),
    Case(
        "config-summary",
        "GET",
        "admin/config-summary",
        {
            "api_host": "127.0.0.1",
            "api_port": 5219,
            "allowed_queues": ["batch"],
            "default_queue": "batch",
            "workers": [],
            "job_store_ttl_seconds": 600,
            "plugin_catalog_dir": "/lyra_data/plugins/catalog",
            "plugin_runner_base_dir": "/lyra_data/plugins/runners",
        },
    ),
    Case(
        "jobs list --limit 7 --status running --metric metric",
        "GET",
        "admin/jobs",
        {"jobs": []},
        params={"limit": 7, "status": "running", "metric": "metric"},
    ),
    Case(
        "jobs cancel job",
        "POST",
        "admin/jobs/job/cancel",
        {
            "job_id": "job",
            "status": "cancelled",
            "cancellation_requested": True,
            "revoke_requested": True,
        },
    ),
    Case(
        "workers list",
        "GET",
        "admin/workers",
        {"workers": [WORKER], "inspect_available": False, "inspect_metadata": METADATA},
    ),
    Case(
        "workers get worker",
        "GET",
        "admin/workers/worker",
        {**WORKER, "inspect_metadata": METADATA},
    ),
    Case(
        "queues list",
        "GET",
        "admin/queues",
        {
            "catalog_available": True,
            "allowed_queues": ["batch"],
            "default_queue": "batch",
            "inspect_metadata": METADATA,
            "queues": [
                {
                    "name": "batch",
                    "is_default": True,
                    "assigned_metric_count": 1,
                    "configured_workers": ["worker"],
                    "observed_workers": [],
                    "pending_depth": None,
                    "pending_depth_unknown": True,
                }
            ],
        },
    ),
    Case("repos list", "GET", "admin/plugin-repos", {"repos": [REPO]}),
    Case(
        "catalog show",
        "GET",
        "admin/catalog",
        {
            "metric_count": 1,
            "metric_names": ["metric"],
            "catalog_fingerprint": "catalog",
            "catalog_available": True,
            "catalog_error": None,
            "plugin_sources": [],
            "metric_queues": {"metric": "batch"},
        },
    ),
    Case(
        "routing list",
        "GET",
        "admin/plugin-routing",
        {
            "metric_queues": {"metric": "batch"},
            "allowed_queues": ["batch"],
            "default_queue": "batch",
        },
    ),
)
MUTATIONS = tuple(case for case in CASES if case.method != "GET")


@dataclass
class HTTPStub:
    payload: JsonValue = field(default_factory=dict)
    status: int = 200
    error: BaseException | None = None
    calls: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: object) -> FakeSyncResponse:
        self.calls.append((method.upper(), url, kwargs))
        if self.error is not None:
            raise self.error
        return FakeSyncResponse(
            status_code=self.status,
            payload=self.payload,
            text=json.dumps(self.payload)
            if self.status in {200, 503}
            else "request failed",
        )


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> HTTPStub:
    stub = HTTPStub()
    monkeypatch.setattr(requests.Session, "request", stub.request)
    monkeypatch.setenv("LYRA_ADMIN_API_KEY", "test-admin")
    return stub


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.command)
@pytest.mark.parametrize("json_output", [False, True])
def test_commands_use_real_client_and_render_response(
    case: Case, *, json_output: bool, http: HTTPStub, capsys: pytest.CaptureFixture[str]
) -> None:
    http.payload = case.payload
    args = ["--json"] if json_output else []
    args.extend(case.command.split())
    if case.method != "GET":
        args.append("--yes")
    assert admin_cli.main(args) == 0
    output = capsys.readouterr()
    assert not output.err
    if json_output:
        data = json.loads(output.out)
        assert all(data[key] == value for key, value in case.payload.items())
    else:
        assert output.out.strip()
    assert len(http.calls) == 1
    method, url, options = http.calls[0]
    assert method == case.method
    assert url == f"http://localhost:5219/{case.path}"
    assert options.get("json") == case.body
    assert options.get("params") == case.params
    assert options["timeout"] == pytest.approx(30.0)
    assert options["headers"] == (
        {} if case.path in {"live", "ready"} else {"Authorization": "Bearer test-admin"}
    )


@pytest.mark.parametrize("case", MUTATIONS, ids=lambda case: case.command)
@pytest.mark.parametrize("json_output", [False, True])
def test_every_mutation_requires_confirmation(
    case: Case,
    *,
    json_output: bool,
    http: HTTPStub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(admin_cli.sys, "stdin", io.StringIO("yes\n"))
    args = ["--json"] if json_output else []
    assert admin_cli.main([*args, *case.command.split()]) == 3
    assert http.calls == []
    output = capsys.readouterr()
    assert not output.out
    if json_output:
        assert json.loads(output.err)["error"]["kind"] == "confirmation"
    else:
        assert "--yes" in output.err


class Terminal(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("answer", "code"), [("yes\n", 0), ("Y\n", 0), ("no\n", 3), ("\n", 3), ("", 3)]
)
def test_interactive_confirmation(
    answer: str,
    code: int,
    http: HTTPStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http.payload = {
        "job_id": "metric",
        "status": "cancelled",
        "cancellation_requested": True,
        "revoke_requested": False,
    }
    prompt = Terminal()
    monkeypatch.setattr(admin_cli.sys, "stdin", Terminal(answer))
    monkeypatch.setattr(admin_cli.sys, "stderr", prompt)
    assert admin_cli.main(["jobs", "cancel", "metric"]) == code
    assert len(http.calls) == (1 if code == 0 else 0)
    assert "metric" in prompt.getvalue()
    assert "localhost:5219" in prompt.getvalue()


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["jobs"],
        ["--timeout", "0", "health"],
        ["--timeout", "nan", "health"],
        ["--timeout", "oops", "health"],
        ["--host", "https://example.test", "health"],
        ["--host", "name:bad", "health"],
        ["--host", "user:pass@example.test", "health"],
        ["jobs", "list", "--limit", "101"],
        ["jobs", "list", "--limit", "x"],
        ["jobs", "list", "--status", "invalid"],
        ["repos", "update", "repo"],
        ["workers", "restart", "--restart-timeout", "-1", "--yes"],
        ["routing", "set", "", "batch", "--yes"],
    ],
)
def test_invalid_arguments_make_no_request(
    arguments: list[str],
    http: HTTPStub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert admin_cli.main(["--json", *arguments]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error"]["kind"] == "usage"
    assert http.calls == []


def test_credentials_and_connection_options(
    http: HTTPStub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http.payload = {"repos": []}
    assert (
        admin_cli.main(
            [
                "--secure",
                "--host",
                "example.test:444/prefix/",
                "--timeout",
                "8",
                "--admin-api-key",
                "explicit",
                "repos",
                "list",
            ]
        )
        == 0
    )
    _, url, options = http.calls.pop()
    assert url == "https://example.test:444/prefix/admin/plugin-repos"
    assert options["headers"] == {"Authorization": "Bearer explicit"}
    assert options["timeout"] == pytest.approx(8.0)
    monkeypatch.delenv("LYRA_ADMIN_API_KEY")
    assert admin_cli.main(["repos", "list"]) == 2
    assert http.calls == []
    http.payload = READINESS
    assert admin_cli.main(["health"]) == 0
    assert "explicit" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "parser", admin_cli.build_parsers(), ids=lambda parser: parser.prog
)
def test_all_help_is_available_without_credentials(
    parser: ArgumentParser,
    http: HTTPStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LYRA_ADMIN_API_KEY")
    arguments: Sequence[str] = parser.prog.split()[1:]
    assert admin_cli.main([*arguments, "--help"]) == 0
    assert http.calls == []


@pytest.mark.parametrize("status", [401, 403, 404, 409, 500, 503])
def test_http_failures_are_reported_once(
    status: int,
    http: HTTPStub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    http.status = status
    assert admin_cli.main(["--json", "repos", "list"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error"]["kind"] == "request"
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (requests.ConnectionError("offline"), 1),
        (requests.Timeout("timeout"), 1),
        (json.JSONDecodeError("invalid", "?", 0), 1),
        (KeyboardInterrupt(), 130),
    ],
)
def test_request_exceptions_do_not_retry_mutations(
    error: BaseException,
    code: int,
    http: HTTPStub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    http.error = error
    assert admin_cli.main(["--json", "jobs", "cancel", "metric", "--yes"]) == code
    assert len(http.calls) == 1
    assert not capsys.readouterr().out


def test_malformed_response(http: HTTPStub, capsys: pytest.CaptureFixture[str]) -> None:
    http.payload = {"unexpected": "data"}
    assert admin_cli.main(["--json", "repos", "list"]) == 1
    assert json.loads(capsys.readouterr().err)["error"]["kind"] == "request"


def test_unhealthy_readiness_preserves_response(
    http: HTTPStub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    http.status = 503
    http.payload = {
        **READINESS,
        "status": "not_ready",
        "redis": {"status": "unavailable"},
    }
    assert admin_cli.main(["--json", "health"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == http.payload
    assert json.loads(output.err)["error"]["kind"] == "operation"


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (
            ["jobs", "cancel", "job"],
            {
                "job_id": "job",
                "status": "cancelled",
                "cancellation_requested": False,
                "revoke_requested": False,
            },
        ),
    ],
)
def test_unaccepted_operations_fail(
    command: list[str],
    payload: dict[str, JsonValue],
    http: HTTPStub,
) -> None:
    http.payload = payload
    assert admin_cli.main([*command, "--yes"]) == 1


def test_human_output_preserves_jobs_and_order(
    http: HTTPStub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    job_id = "job-with-a-long-identifier-that-must-not-be-truncated"
    http.payload = {
        "jobs": [
            {
                "job_id": job_id,
                "status": "running",
                "updated_at": "2026-09-22T12:00:00Z",
                "metric": "metric",
            },
            {
                "job_id": "older-job",
                "status": "failed",
                "updated_at": "2026-09-22T11:00:00Z",
                "error": {"message": "failed\nwith details"},
            },
        ]
    }
    assert admin_cli.main(["jobs", "list"]) == 0
    output = capsys.readouterr().out
    assert output.index(job_id) < output.index("older-job")
    assert "failed\\nwith details" in output
    assert http.calls[0][2]["params"] == {"limit": 50}


def test_human_output_shows_unknown_depth_and_stale_inspection(
    http: HTTPStub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    http.payload = next(case.payload for case in CASES if case.command == "queues list")
    assert admin_cli.main(["queues", "list"]) == 0
    output = capsys.readouterr().out
    assert "unknown" in output
    assert "Stale: true" in output
    assert "inspection unavailable" in output
    assert "PENDING DEPTH" in output


def test_empty_collection_is_explicit(
    http: HTTPStub, capsys: pytest.CaptureFixture[str]
) -> None:
    http.payload = {"repos": []}
    assert admin_cli.main(["repos", "list"]) == 0
    assert capsys.readouterr().out == "Repos: (none)\n"
