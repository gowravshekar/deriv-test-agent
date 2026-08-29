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

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions.in_memory_session_service import InMemorySessionService

from app.app_utils import services


@pytest.fixture(autouse=True)
def clear_service_caches() -> Iterator[None]:
    services.get_session_service.cache_clear()
    services.get_artifact_service.cache_clear()
    yield
    services.get_session_service.cache_clear()
    services.get_artifact_service.cache_clear()


def test_get_session_service_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)
    assert isinstance(services.get_session_service(), InMemorySessionService)


def test_get_session_service_from_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SERVICE_URI", "sqlite://sessions.db")
    fake = MagicMock()
    with patch.object(
        services, "create_session_service_from_options", return_value=fake
    ) as factory:
        assert services.get_session_service() is fake
        factory.assert_called_once()
        assert factory.call_args.kwargs["session_service_uri"] == "sqlite://sessions.db"


def test_get_session_service_vertex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "engine-1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "us-central1")
    fake = MagicMock()
    with patch(
        "google.adk.sessions.vertex_ai_session_service.VertexAiSessionService",
        return_value=fake,
    ) as cls:
        assert services.get_session_service() is fake
        cls.assert_called_once_with(
            project="proj",
            location="us-central1",
            agent_engine_id="engine-1",
        )


def test_get_artifact_service_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOGS_BUCKET_NAME", raising=False)
    assert isinstance(services.get_artifact_service(), InMemoryArtifactService)


def test_get_artifact_service_gcs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGS_BUCKET_NAME", "logs-bucket")
    fake = MagicMock()
    with patch.object(services, "GcsArtifactService", return_value=fake) as gcs:
        assert services.get_artifact_service() is fake
        gcs.assert_called_once_with(bucket_name="logs-bucket")
