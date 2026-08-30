# ruff: noqa
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

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.pipeline.eval_tool import summarize_pair


MODEL = "gemini-3.7-flash"


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are an incident-command assistant. "
        "For incident questions, call summarize_pair with the pair name "
        "(default public) and the pipeline focus requested by the user. "
        "Use focus=overview only when no specific stage is requested. "
        "Ground your answer only in that tool output and follow the requested "
        "response format. "
        "Never claim that actions were executed — recommendations only. "
        "Do not treat trade-execution dependency failures, payments reconciliation "
        "backlog, or rollout-related risk-engine drift as low importance without "
        "strong justification from the tool output."
    ),
    tools=[summarize_pair],
)

app = App(
    root_agent=root_agent,
    name="app",
)
