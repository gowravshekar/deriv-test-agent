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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import AgentCard, AgentInterface
from fastapi import FastAPI
from starlette.requests import Request

from app.app_utils import a2a


def _http_request(*, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers or [],
            "client": ("127.0.0.1", 123),
            "server": ("127.0.0.1", 80),
        }
    )


def test_resolve_app_url_explicit() -> None:
    assert a2a._resolve_app_url("https://example.com") == "https://example.com"


def test_resolve_app_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_URL", "https://from-env.example")
    assert a2a._resolve_app_url(None) == "https://from-env.example"


def test_resolve_app_url_agent_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "eng")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "us-central1")
    url = a2a._resolve_app_url(None)
    assert "us-central1-aiplatform.googleapis.com" in url
    assert "reasoningEngines/eng/api" in url


def test_resolve_app_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)
    assert a2a._resolve_app_url(None) == "http://0.0.0.0:8000"


def test_default_capabilities() -> None:
    caps = a2a._default_capabilities()
    assert caps.streaming is True
    assert any(
        ext.uri == a2a._ADK_AGENT_EXECUTOR_EXTENSION_URI for ext in caps.extensions
    )


@pytest.mark.asyncio
async def test_add_v0_3_compat_interface_appends() -> None:
    card = AgentCard()
    card.supported_interfaces.append(
        AgentInterface(
            protocol_binding="JSONRPC",
            protocol_version="1.0",
            url="http://localhost:8000/a2a/app",
        )
    )
    updated = await a2a._add_v0_3_compat_interface(card)
    versions = [iface.protocol_version for iface in updated.supported_interfaces]
    assert "0.3" in versions


@pytest.mark.asyncio
async def test_add_v0_3_compat_interface_empty() -> None:
    card = AgentCard()
    updated = await a2a._add_v0_3_compat_interface(card)
    assert len(updated.supported_interfaces) == 0


def test_context_builder_keeps_existing_version() -> None:
    request = _http_request(headers=[(b"a2a-version", b"0.3")])
    context = a2a._A2AServerCallContextBuilder().build(request)
    assert context.state["headers"]["A2A-Version"] == "0.3"


def test_context_builder_infers_v0_3_from_method() -> None:
    request = _http_request()
    request._json = {"method": "message/send"}
    context = a2a._A2AServerCallContextBuilder().build(request)
    assert context.state["headers"]["A2A-Version"] == "0.3"


def test_context_builder_defaults_to_v1() -> None:
    request = _http_request()
    request._json = {"method": "SendMessage"}
    context = a2a._A2AServerCallContextBuilder().build(request)
    assert context.state["headers"]["A2A-Version"] == "1.0"


@pytest.mark.asyncio
async def test_attach_a2a_routes() -> None:
    app = FastAPI()
    card = AgentCard()
    builder = MagicMock()
    builder.build = AsyncMock(return_value=card)

    with (
        patch.object(a2a, "AgentCardBuilder", return_value=builder),
        patch.object(a2a, "DefaultRequestHandler") as handler_cls,
        patch.object(a2a, "A2aAgentExecutor"),
        patch.object(a2a, "add_a2a_routes_to_fastapi") as add_routes,
        patch.object(a2a, "create_agent_card_routes", return_value=[]),
        patch.object(a2a, "create_jsonrpc_routes", return_value=[]),
    ):
        await a2a.attach_a2a_routes(
            app,
            agent=MagicMock(),
            runner=MagicMock(),
            task_store=MagicMock(),
            rpc_path="/a2a/app",
            app_url="http://localhost:8000",
        )
        add_routes.assert_called_once()
        handler_cls.assert_called_once()
