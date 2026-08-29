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

from fastapi.testclient import TestClient

from app.fast_api_app import app


def test_app_metadata() -> None:
    assert app.title == "deriv-test-agent"
    assert "deriv-test-agent" in app.description


def test_lifespan_serves_agent_card() -> None:
    with TestClient(app) as client:
        response = client.get("/a2a/app/.well-known/agent-card.json")
    assert response.status_code == 200
    body = response.json()
    assert "supportedInterfaces" in body
    assert "skills" in body
