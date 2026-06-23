import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from lyra_app import registry
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from lyra_app.routes import geojson


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [
            {
                "name": "heavy_metric",
                "description": "A heavy metric.",
                "parameters": [
                    {
                        "name": "value",
                        "type": "int",
                        "required": True,
                        "default": None,
                    }
                ],
                "returns_file": False,
                "tavi_hint": "",
                "request_schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer"}},
                },
                "execution": {
                    "profile": "heavy",
                    "queue": "heavy",
                    "timeout_seconds": 120,
                },
                "callable": {"mode": "single", "calculate": "fake_plugin:calculate"},
            }
        ],
    }


def _synced_repo(repo: Path) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        owner="owner",
        repo="repo",
        ref=None,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=False)


class FakeTask:
    id = "task-id"


class FakeCelery:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.control = self

    def send_task(
        self,
        name: str,
        *,
        args: list[dict[str, Any]],
        queue: str,
    ) -> FakeTask:
        self.sent.append({"name": name, "args": args, "queue": queue})
        return FakeTask()

    def revoke(self, task_id: str, *, terminate: bool, signal: str) -> None:
        self.sent.append(
            {"task_id": task_id, "terminate": terminate, "signal": signal},
        )


class FakePubSub:
    async def subscribe(self, channel: str) -> None:  # noqa: ARG002
        return None

    async def listen(self) -> Any:
        yield {"type": "message", "data": json.dumps({"status": "success"})}

    async def unsubscribe(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeRedis:
    async def ping(self) -> bool:
        return True

    def pubsub(self) -> FakePubSub:
        return FakePubSub()


class FakeWebSocket:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.sent: list[dict[str, Any]] = []
        self.closed: list[int | None] = []

    async def accept(self) -> None:
        return None

    async def receive_json(self) -> dict[str, Any]:
        return self.payload

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def receive(self) -> None:
        await asyncio.sleep(60)
        raise WebSocketDisconnect

    async def close(self, code: int | None = None) -> None:
        self.closed.append(code)


@pytest.fixture(autouse=True)
def reset_catalog() -> None:
    registry.reset_catalog()


def test_websocket_dispatches_valid_payload_to_manifest_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])
    registry.refresh_catalog()

    fake_celery = FakeCelery()
    websocket = FakeWebSocket({"value": 3})
    monkeypatch.setattr(geojson, "celery_app", fake_celery)
    monkeypatch.setattr(geojson, "redis_client", FakeRedis())

    asyncio.run(geojson.websocket_route(cast("WebSocket", websocket), "heavy_metric"))

    assert fake_celery.sent == [
        {"name": "heavy_metric", "args": [{"value": 3}], "queue": "heavy"}
    ]
    assert websocket.sent == [
        {"status": "queued", "task_id": "task-id"},
        {"status": "success"},
    ]
