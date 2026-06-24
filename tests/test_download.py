import asyncio
import json
import math
from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse

from lyra_app import job_store
from lyra_app.routes import download


class FakeRedis:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.get_keys: list[str] = []
        self.deleted: list[str] = []

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str:
        self.get_keys.append(key)
        return self.payload

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


def test_dict_payload_returns_json_with_non_finite_numbers_as_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis(
        json.dumps(
            {
                "status": "success",
                "result": {
                    "score": math.nan,
                    "limits": [math.inf, -math.inf],
                },
            }
        )
    )

    monkeypatch.setattr(download, "redis_client", redis)
    monkeypatch.setattr(download.job_store, "redis_client", redis)
    response = asyncio.run(download.download_result("download-id", BackgroundTasks()))

    assert isinstance(response, JSONResponse)
    assert json.loads(bytes(response.body)) == {
        "status": "success",
        "result": {
            "score": None,
            "limits": [None, None],
        },
    }
    assert redis.get_keys == [job_store.result_key("download-id")]


def test_file_payload_cleanup_deletes_only_job_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.tif"
    output.write_bytes(b"data")
    redis = FakeRedis(
        json.dumps(
            {
                "job_id": "job-1",
                "status": "succeeded",
                "result_type": "file",
                "file_path": str(output),
            }
        )
    )
    background_tasks = BackgroundTasks()

    monkeypatch.setattr(download, "redis_client", redis)
    monkeypatch.setattr(download.job_store, "redis_client", redis)
    response = asyncio.run(download.download_result("job-1", background_tasks))
    asyncio.run(background_tasks())

    assert isinstance(response, FileResponse)
    assert not output.exists()
    assert redis.deleted == [job_store.result_key("job-1")]
