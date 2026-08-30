"""Unit tests for first-pass pipeline, guardrails, and comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline.compare import compute_comparison
from app.pipeline.guardrails import canonicalize_action, review_action, safest_action
from app.pipeline.io import PIPELINE_FILES_ROOT
from app.pipeline.llm import FakeJsonClient
from app.pipeline.models import (
    ActionProposal,
    IncidentGroup,
    OperatorFeedback,
    Runbook,
    SafetyReview,
    ServiceMeta,
    SeverityAssessment,
)
from app.pipeline.run import (
    _align_regrouped,
    _dedupe_incident_alerts,
    _ensure_runbook_action,
    default_fake_client,
    run_pipeline,
)
from app.pipeline.validate_artifacts import (
    normalize_update,
    score_pair,
    validate_pair,
)


def test_confidence_flags_human_review() -> None:
    inc = IncidentGroup(
        incident_id="inc-x",
        title="t",
        summary="s",
        alert_ids=["a-1"],
        primary_service="auth-session",
        suspected_cause="unknown",
        blast_radius="single_service",
        confidence=0.65,
        needs_human_review=False,
    )
    assert inc.needs_human_review is True


def test_dedupe_alert_ids_across_incidents() -> None:
    groups = [
        IncidentGroup(
            incident_id="inc-a",
            title="t",
            summary="s",
            alert_ids=["a-1", "a-2"],
            primary_service="auth-session",
            suspected_cause="x",
            blast_radius="single_service",
            confidence=0.9,
        ),
        IncidentGroup(
            incident_id="inc-b",
            title="t",
            summary="s",
            alert_ids=["a-2", "a-3"],
            primary_service="auth-session",
            suspected_cause="x",
            blast_radius="single_service",
            confidence=0.9,
        ),
    ]
    out = _dedupe_incident_alerts(groups)
    assert out[0].alert_ids == ["a-1", "a-2"]
    assert out[1].alert_ids == ["a-3"]
    assert out[1].needs_human_review is True


def test_align_regrouped_keeps_original_ids() -> None:
    original = [
        IncidentGroup(
            incident_id="inc-001",
            title="old",
            summary="old",
            alert_ids=["a-1", "a-2"],
            primary_service="auth-session",
            suspected_cause="x",
            blast_radius="single_service",
            confidence=0.9,
        )
    ]
    regrouped = [
        IncidentGroup(
            incident_id="inc-999",
            title="new",
            summary="new",
            alert_ids=["a-2", "a-1"],
            primary_service="auth-session",
            suspected_cause="y",
            blast_radius="regional",
            confidence=0.95,
        )
    ]
    aligned = _align_regrouped(original, regrouped)
    assert aligned[0].incident_id == "inc-001"
    assert aligned[0].title == "new"


def test_ensure_runbook_action_appends_fallback() -> None:
    incident = IncidentGroup(
        incident_id="inc-1",
        title="t",
        summary="s",
        alert_ids=["a-1"],
        primary_service="auth-session",
        suspected_cause="x",
        blast_radius="single_service",
        confidence=0.9,
    )
    proposals = [
        ActionProposal(
            incident_id="inc-1",
            action="not_in_runbook",
            why="x",
            expected_effect="y",
            risk="z",
        )
    ]
    out = _ensure_runbook_action(
        [incident],
        proposals,
        [
            Runbook(
                service="auth-session",
                allowed_actions=["roll_back_recent_deploy"],
            )
        ],
    )
    assert any(p.action == "roll_back_recent_deploy" for p in out)
    assert incident.needs_human_review is True


def test_forbidden_runbook_action() -> None:
    proposal = ActionProposal(
        incident_id="inc-1",
        action="bypass_risk_checks",
        why="x",
        expected_effect="y",
        risk="z",
        safety_level="safe",
    )
    result = review_action(
        proposal,
        runbook=Runbook(
            service="trade-execution",
            allowed_actions=["pause_low_priority_orders"],
            forbidden_actions=["bypass_risk_checks"],
        ),
        service=ServiceMeta(
            service="trade-execution",
            tier="critical",
            owner_team="Core Trading",
            customer_impact="x",
            change_freeze_required=True,
        ),
    )
    assert result.final_safety_level == "forbidden"
    assert result.model_safety_level == "safe"


def test_change_freeze_escalates_non_rollback() -> None:
    proposal = ActionProposal(
        incident_id="inc-1",
        action="scale_workers",
        why="x",
        expected_effect="y",
        risk="z",
        safety_level="safe",
    )
    result = review_action(
        proposal,
        runbook=Runbook(
            service="payments-ledger",
            allowed_actions=["scale_workers"],
            forbidden_actions=[],
        ),
        service=ServiceMeta(
            service="payments-ledger",
            tier="critical",
            owner_team="Payments",
            customer_impact="x",
            change_freeze_required=True,
        ),
    )
    assert result.final_safety_level == "needs_approval"


def test_hybrid_semantic_forbidden() -> None:
    proposal = ActionProposal(
        incident_id="inc-1",
        action="auto_approve_everything_now",
        why="x",
        expected_effect="y",
        risk="z",
        safety_level="safe",
    )
    result = review_action(
        proposal,
        runbook=Runbook(
            service="risk-engine", allowed_actions=[], forbidden_actions=[]
        ),
        service=None,
    )
    assert result.final_safety_level == "forbidden"


def test_alias_canonicalize() -> None:
    assert canonicalize_action("bypass risk checks") == "bypass_risk_checks"


def test_safest_action_prefers_safe() -> None:
    proposals = [
        ActionProposal(
            incident_id="inc-1",
            action="bad",
            why="",
            expected_effect="",
            risk="",
            safety_level="forbidden",
        ),
        ActionProposal(
            incident_id="inc-1",
            action="good",
            why="",
            expected_effect="",
            risk="",
            safety_level="safe",
        ),
    ]
    safety = [
        SafetyReview(
            incident_id="inc-1",
            action="bad",
            model_safety_level="forbidden",
            final_safety_level="forbidden",
        ),
        SafetyReview(
            incident_id="inc-1",
            action="good",
            model_safety_level="safe",
            final_safety_level="safe",
        ),
    ]
    assert safest_action("inc-1", proposals, safety) == "good"


def test_safest_action_none_when_top_is_forbidden() -> None:
    proposals = [
        ActionProposal(
            incident_id="inc-1",
            action="bad",
            why="",
            expected_effect="",
            risk="",
            safety_level="forbidden",
        )
    ]
    safety = [
        SafetyReview(
            incident_id="inc-1",
            action="bad",
            model_safety_level="forbidden",
            final_safety_level="forbidden",
        )
    ]
    assert safest_action("inc-1", proposals, safety) is None


def test_agreement_delta_count_and_rate() -> None:
    feedback = [
        OperatorFeedback(
            incident_id="inc-1",
            original_severity="sev2",
            corrected_severity="sev1",
            original_action="a",
            corrected_action="b",
            action_status="corrected",
            severity_status="corrected",
            timestamp="2026-01-01T00:00:00Z",
        ),
        OperatorFeedback(
            incident_id="inc-2",
            original_severity="sev3",
            corrected_severity=None,
            original_action="c",
            corrected_action=None,
            action_status="accepted",
            severity_status="accepted",
            timestamp="2026-01-01T00:00:00Z",
        ),
    ]
    original_severities = [
        SeverityAssessment(
            incident_id="inc-1",
            severity="sev2",
            business_impact="",
            technical_impact="",
            reasoning="",
        ),
        SeverityAssessment(
            incident_id="inc-2",
            severity="sev3",
            business_impact="",
            technical_impact="",
            reasoning="",
        ),
    ]
    redecided = [
        SeverityAssessment(
            incident_id="inc-1",
            severity="sev1",
            business_impact="",
            technical_impact="",
            reasoning="",
        ),
        SeverityAssessment(
            incident_id="inc-2",
            severity="sev3",
            business_impact="",
            technical_impact="",
            reasoning="",
        ),
    ]
    orig_props = [
        ActionProposal(
            incident_id="inc-1", action="a", why="", expected_effect="", risk=""
        ),
        ActionProposal(
            incident_id="inc-2", action="c", why="", expected_effect="", risk=""
        ),
    ]
    re_props = [
        ActionProposal(
            incident_id="inc-1", action="b", why="", expected_effect="", risk=""
        ),
        ActionProposal(
            incident_id="inc-2", action="c", why="", expected_effect="", risk=""
        ),
    ]
    orig_safety = [
        SafetyReview(
            incident_id="inc-1",
            action="a",
            model_safety_level="safe",
            final_safety_level="safe",
        ),
        SafetyReview(
            incident_id="inc-2",
            action="c",
            model_safety_level="safe",
            final_safety_level="safe",
        ),
    ]
    re_safety = [
        SafetyReview(
            incident_id="inc-1",
            action="b",
            model_safety_level="safe",
            final_safety_level="safe",
        ),
        SafetyReview(
            incident_id="inc-2",
            action="c",
            model_safety_level="safe",
            final_safety_level="safe",
        ),
    ]
    rows, delta = compute_comparison(
        feedback=feedback,
        original_severities=original_severities,
        redecided_severities=redecided,
        original_proposals=orig_props,
        original_safety=orig_safety,
        redecided_proposals=re_props,
        redecided_safety=re_safety,
    )
    assert len(rows) == 2
    assert rows[0].moved_toward_operator is True
    assert rows[1].moved_toward_operator is False
    assert delta.severity_count_delta == 1  # 0 before -> 1 after for corrected
    assert delta.action_count_delta == 1


def test_pipeline_fake_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Copy public inputs into temp pair via symlink-style by pointing PIPELINE_FILES_ROOT
    src = PIPELINE_FILES_ROOT / "public"
    dest = tmp_path / "public"
    dest.mkdir()
    for name in ("alerts.json", "services.json", "runbooks.json"):
        (dest / name).write_text(
            (src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    import app.pipeline.io as io_mod

    monkeypatch.setattr(io_mod, "PIPELINE_FILES_ROOT", tmp_path)

    answers = iter(["y", "y", "y"] * 20)

    def prompt_fn(_msg: str) -> str:
        return next(answers)

    path = run_pipeline(
        "public",
        client=default_fake_client(),
        prompt_fn=prompt_fn,
    )
    assert (path / "incident_groups.json").is_file()
    assert (path / "incident_command_output.json").is_file()
    groups = json.loads((path / "incident_groups.json").read_text(encoding="utf-8"))
    assigned = [aid for g in groups for aid in g["alert_ids"]]
    assert len(assigned) == len(set(assigned))
    assert set(assigned) == {
        "a-101",
        "a-102",
        "a-103",
        "a-104",
        "a-105",
        "a-106",
        "a-107",
        "a-108",
    }
    calls = (path / "llm_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    stages = {json.loads(line)["stage"] for line in calls}
    assert "incident_grouping" in stages
    assert "severity_assessment" in stages
    assert "action_proposal" in stages
    assert "stakeholder_drafting" in stages
    assert "severity_redecision" in stages
    assert any(
        json.loads(line).get("few_shot_examples_included")
        for line in calls
        if json.loads(line)["stage"] in {"severity_redecision", "action_redecision"}
    )
    errors = validate_pair("public", root=tmp_path)
    assert errors == [], errors
    assert score_pair("public", root=tmp_path)["score"] == 1.0

    updates = json.loads(
        (path / "stakeholder_updates.json").read_text(encoding="utf-8")
    )
    services = {g["incident_id"]: g["primary_service"] for g in groups}
    for row in updates:
        assert services[row["incident_id"]] in row["engineering_update"]
        assert services[row["incident_id"]] in row["executive_update"]
    for field in ("engineering_update", "executive_update"):
        texts = [normalize_update(row[field]) for row in updates]
        assert len(set(texts)) == len(texts), f"{field} repeated across incidents"


def test_duplicate_stakeholder_drafts_use_distinct_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = PIPELINE_FILES_ROOT / "public"
    dest = tmp_path / "public"
    dest.mkdir()
    for name in ("alerts.json", "services.json", "runbooks.json"):
        (dest / name).write_text(
            (src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

        import app.pipeline.io as io_mod

        monkeypatch.setattr(io_mod, "PIPELINE_FILES_ROOT", tmp_path)

    inner = default_fake_client()

    def handler(prompt: str):
        payload = inner.generate_json(prompt)
        if "Draft stakeholder" in prompt:
            same = "Incident is under investigation. Containment is in progress."
            iid = payload.get("incident_id") if isinstance(payload, dict) else "inc-001"
            return {
                "incident_id": iid,
                "engineering_update": same,
                "executive_update": same,
            }
        return payload

    answers = iter(["y", "y", "y"] * 20)

    path = run_pipeline(
        "public",
        client=FakeJsonClient(handler=handler),
        prompt_fn=lambda _msg: next(answers),
    )
    updates = json.loads(
        (path / "stakeholder_updates.json").read_text(encoding="utf-8")
    )
    assert updates
    for row in updates:
        assert row["engineering_update"].strip() != row["executive_update"].strip()


def test_operator_severity_correction_moves_agreement_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = PIPELINE_FILES_ROOT / "public"
    dest = tmp_path / "public"
    dest.mkdir()
    for name in ("alerts.json", "services.json", "runbooks.json"):
        (dest / name).write_text(
            (src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    import app.pipeline.io as io_mod

    monkeypatch.setattr(io_mod, "PIPELINE_FILES_ROOT", tmp_path)

    inner = default_fake_client()
    sev_calls = {"n": 0}

    def handler(prompt: str):
        if "assessing incident severity" in prompt:
            sev_calls["n"] += 1
            payload = inner._handler(prompt)  # type: ignore[misc]
            if sev_calls["n"] >= 2:
                for row in payload["assessments"]:
                    if row["incident_id"] == "inc-004":
                        row["severity"] = "sev0"
            return payload
        return inner._handler(prompt)  # type: ignore[misc]

    answers = iter(
        ["y", "y", "y"] * 3 + ["correct to: sev0", "y", "y"] + ["y", "y", "y"] * 2
    )

    path = run_pipeline(
        "public",
        client=FakeJsonClient(handler=handler),
        prompt_fn=lambda _msg: next(answers),
    )
    redecision = json.loads(
        (path / "severity_redecision.json").read_text(encoding="utf-8")
    )
    by_id = {row["incident_id"]: row["severity"] for row in redecision}
    assert by_id["inc-004"] == "sev0"
    final = json.loads(
        (path / "incident_command_output.json").read_text(encoding="utf-8")
    )
    assert final["agreement_delta"]["severity_count_delta"] == 1
    row = next(
        r for r in final["before_after_comparison"] if r["incident_id"] == "inc-004"
    )
    assert row["moved_toward_operator"] is True
    assert row["operator_severity"] == "sev0"
    assert row["redecided_severity"] == "sev0"
