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

from app.agent import get_current_time, get_weather, root_agent


def test_get_weather_san_francisco() -> None:
    assert "foggy" in get_weather("sf")
    assert "foggy" in get_weather("San Francisco")


def test_get_weather_other_city() -> None:
    assert get_weather("london") == "It's 90 degrees and sunny."


def test_get_current_time_unknown_city() -> None:
    assert "Sorry" in get_current_time("london")
    assert "london" in get_current_time("london")


def test_get_current_time_san_francisco() -> None:
    result = get_current_time("San Francisco")
    assert "The current time for query San Francisco is" in result
    assert "PDT" in result or "PST" in result


def test_root_agent_has_tools() -> None:
    assert root_agent.name == "root_agent"
    assert {tool.__name__ for tool in root_agent.tools} == {
        "get_weather",
        "get_current_time",
    }
