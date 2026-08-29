# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_utils.reasoning_engine_adapter import (
    _no_op_instrumentor_builder,
    attach_reasoning_engine_routes,
)


def test_no_op_instrumentor_builder() -> None:
    assert _no_op_instrumentor_builder("unused-project") is None


def _post_with_mocked_runtime(
    runtime: MagicMock, path: str, payload: dict[str, object]
):
    app = FastAPI()
    with patch("app.app_utils.reasoning_engine_adapter.AdkApp", return_value=runtime):
        attach_reasoning_engine_routes(app)
        with TestClient(app) as client:
            return client.post(path, json=payload)


def test_stream_reasoning_engine() -> None:
    runtime = MagicMock()
    runtime.register_operations.return_value = {
        "stream": [],
        "async_stream": ["async_stream_query"],
        "": ["query"],
        "async": [],
    }

    async def async_stream_query(**kwargs: object) -> AsyncIterator[dict[str, object]]:
        yield {"content": {"parts": [{"text": "hello"}]}}

    runtime.async_stream_query = async_stream_query

    response = _post_with_mocked_runtime(
        runtime,
        "/api/stream_reasoning_engine",
        {"class_method": "async_stream_query", "input": {"message": "Hi"}},
    )
    assert response.status_code == 200
    assert "hello" in response.text
    runtime.set_up.assert_called_once()


def test_reasoning_engine_sync_query() -> None:
    runtime = MagicMock()
    runtime.register_operations.return_value = {
        "stream": [],
        "async_stream": [],
        "": ["query"],
        "async": [],
    }
    runtime.query = lambda **kwargs: {"ok": True}

    response = _post_with_mocked_runtime(
        runtime, "/api/reasoning_engine", {"class_method": "query"}
    )
    assert response.status_code == 200
    assert response.json() == {"output": {"ok": True}}


def test_reasoning_engine_async_query() -> None:
    runtime = MagicMock()
    runtime.register_operations.return_value = {
        "stream": [],
        "async_stream": [],
        "": [],
        "async": ["async_query"],
    }

    async def async_query(**kwargs: object) -> dict[str, bool]:
        return {"async": True}

    runtime.async_query = async_query

    response = _post_with_mocked_runtime(
        runtime,
        "/api/reasoning_engine",
        {"class_method": "async_query", "input": {}},
    )
    assert response.status_code == 200
    assert response.json() == {"output": {"async": True}}


def test_reasoning_engine_unknown_method() -> None:
    runtime = MagicMock()
    runtime.register_operations.return_value = {
        "stream": ["async_stream_query"],
        "async_stream": [],
        "": [],
        "async": [],
    }

    response = _post_with_mocked_runtime(
        runtime,
        "/api/reasoning_engine",
        {"class_method": "async_stream_query"},
    )
    assert response.status_code == 404
