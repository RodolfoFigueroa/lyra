import asyncio
import json
import math

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

from lyra_app.routes import download


class FakeRedis:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str:  # noqa: ARG002
        return self.payload


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
    response = asyncio.run(download.download_result("download-id", BackgroundTasks()))

    assert isinstance(response, JSONResponse)
    assert json.loads(bytes(response.body)) == {
        "status": "success",
        "result": {
            "score": None,
            "limits": [None, None],
        },
    }
