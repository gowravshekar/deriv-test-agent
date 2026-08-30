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

from app.agent import root_agent
from app.pipeline.eval_tool import EVAL_FOCUSES, summarize_pair


def test_root_agent_has_summarize_pair_only() -> None:
    assert root_agent.name == "root_agent"
    assert {tool.__name__ for tool in root_agent.tools} == {"summarize_pair"}


def test_summarize_pair_missing_folder() -> None:
    text = summarize_pair("does-not-exist")
    assert "not found" in text.lower()


def test_summarize_pair_rejects_unknown_focus() -> None:
    text = summarize_pair("public", "unknown-stage")
    assert "unknown focus" in text.lower()
    assert "safety_guardrails" in text


def test_eval_focuses_cover_pipeline_contracts() -> None:
    assert EVAL_FOCUSES == {
        "overview",
        "inputs_and_lifecycle",
        "incident_grouping",
        "severity_assessment",
        "action_proposals",
        "safety_guardrails",
        "stakeholder_drafting",
        "operator_review",
        "feedback_and_redecision",
        "comparison",
        "analytics",
        "prior_feedback",
        "escalation",
        "llm_audit",
    }
