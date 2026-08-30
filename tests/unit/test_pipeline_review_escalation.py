"""Extra unit tests for review parsing and escalation."""

from __future__ import annotations

import pytest

from app.pipeline.escalation import build_escalation_bundle
from app.pipeline.models import (
    ActionProposal,
    IncidentGroup,
    OperatorFeedback,
    Runbook,
    SafetyReview,
    ServiceMeta,
    SeverityAssessment,
)
from app.pipeline.review import (
    _parse_choice,
    build_few_shot_examples,
    review_incidents,
)

RISK_RUNBOOK = Runbook(
    service="risk-engine",
    allowed_actions=[
        "roll_back_model",
        "route_to_previous_policy",
        "raise_review_threshold",
    ],
    forbidden_actions=["disable_risk_scoring", "auto_approve_all_transactions"],
    notes="Prefer rollback on rollout-correlated drift.",
)
RISK_SERVICE = ServiceMeta(
    service="risk-engine",
    tier="critical",
    owner_team="Risk & Compliance",
    depends_on=["model-serving"],
    customer_impact="Risk decisions may be inconsistent.",
    change_freeze_required=True,
)


def _review(answers: list[str]) -> OperatorFeedback:
    incident = IncidentGroup(
        incident_id="inc-004",
        title="Risk engine drift",
        summary="Decision distribution shifted after rollout.",
        alert_ids=["a-106"],
        primary_service="risk-engine",
        suspected_cause="Model rollout",
        blast_radius="regional",
        confidence=0.86,
    )
    severity = SeverityAssessment(
        incident_id="inc-004",
        severity="sev1",
        status="open",
        business_impact="Risk decisions may be wrong",
        technical_impact="Model drift",
        reasoning="Rollout correlated",
    )
    proposal = ActionProposal(
        incident_id="inc-004",
        action="roll_back_model",
        why="Runbook aligned",
        expected_effect="Restore baseline",
        risk="Low",
        safety_level="safe",
    )
    safety = SafetyReview(
        incident_id="inc-004",
        action="roll_back_model",
        model_safety_level="safe",
        final_safety_level="needs_approval",
        guardrail_reasons=["change freeze on critical dependency path"],
    )
    answer_iter = iter(answers)
    rows = review_incidents(
        incidents=[incident],
        severities=[severity],
        proposals=[proposal],
        safety=[safety],
        alerts_by_id={},
        runbooks=[RISK_RUNBOOK],
        services=[RISK_SERVICE],
        prompt_fn=lambda _msg: next(answer_iter),
    )
    return rows[0]


def test_parse_choice_variants() -> None:
    assert _parse_choice("y", {"sev1"}) == ("accepted", None)
    assert _parse_choice("skip", {"sev1"}) == ("skipped", None)
    assert _parse_choice("correct to: sev1", {"sev0", "sev1"}) == ("corrected", "sev1")
    assert _parse_choice("ROLL BACK MODEL", {"roll_back_model"}) == (
        "corrected",
        "roll_back_model",
    )


def test_bare_runbook_action_is_accepted_as_correction() -> None:
    row = _review(["y", "route_to_previous_policy", "y", "y"])
    assert row.action_status == "corrected"
    assert row.corrected_action == "route_to_previous_policy"


def test_bare_action_outside_runbook_reprompts() -> None:
    row = _review(["y", "scale_out", "roll_back_model", "y", "y"])
    assert row.corrected_action == "roll_back_model"


def test_forbidden_override_is_rejected_by_guardrails() -> None:
    row = _review(
        ["y", "correct to: disable_risk_scoring", "route_to_previous_policy", "y", "y"]
    )
    assert row.corrected_action == "route_to_previous_policy"


def test_semantically_forbidden_override_is_rejected() -> None:
    row = _review(
        ["y", "correct to: bypass risk checks", "route_to_previous_policy", "y", "y"]
    )
    assert row.corrected_action == "route_to_previous_policy"


def test_off_runbook_override_requires_confirmation() -> None:
    declined = _review(["y", "correct to: scale_out", "n", "roll_back_model", "y", "y"])
    assert declined.corrected_action == "roll_back_model"

    accepted = _review(["y", "correct to: scale_out", "y", "y", "y", "y"])
    assert accepted.corrected_action == "scale_out"


def test_review_without_runbooks_still_accepts_explicit_correction() -> None:
    answers = iter(["y", "correct to: anything_goes", "y", "y"])
    incident = IncidentGroup(
        incident_id="inc-009",
        title="Unknown service",
        summary="No runbook available.",
        alert_ids=["a-900"],
        primary_service="mystery-service",
        suspected_cause="unknown",
        blast_radius="unknown",
        confidence=0.9,
    )
    rows = review_incidents(
        incidents=[incident],
        severities=[],
        proposals=[],
        safety=[],
        alerts_by_id={},
        prompt_fn=lambda _msg: next(answers),
    )
    assert rows[0].corrected_action == "anything_goes"


def test_cannot_accept_forbidden_top_action() -> None:
    incident = IncidentGroup(
        incident_id="inc-004",
        title="Risk engine drift",
        summary="Decision distribution shifted after rollout.",
        alert_ids=["a-106"],
        primary_service="risk-engine",
        suspected_cause="Model rollout",
        blast_radius="regional",
        confidence=0.86,
    )
    answers = iter(["y", "y", "route_to_previous_policy", "y", "y"])
    rows = review_incidents(
        incidents=[incident],
        severities=[
            SeverityAssessment(
                incident_id="inc-004",
                severity="sev1",
                status="open",
                business_impact="Risk decisions may be wrong",
                technical_impact="Model drift",
                reasoning="Rollout correlated",
            )
        ],
        proposals=[
            ActionProposal(
                incident_id="inc-004",
                action="disable_risk_scoring",
                why="unsafe",
                expected_effect="bad",
                risk="high",
                safety_level="forbidden",
            )
        ],
        safety=[
            SafetyReview(
                incident_id="inc-004",
                action="disable_risk_scoring",
                model_safety_level="forbidden",
                final_safety_level="forbidden",
                guardrail_reasons=["runbook forbids disable_risk_scoring"],
            )
        ],
        alerts_by_id={},
        runbooks=[RISK_RUNBOOK],
        services=[RISK_SERVICE],
        prompt_fn=lambda _msg: next(answers),
    )
    assert rows[0].action_status == "corrected"
    assert rows[0].corrected_action == "route_to_previous_policy"


@pytest.mark.parametrize("answer", ["y", "skip"])
def test_accept_and_skip_do_not_trigger_guardrails(answer: str) -> None:
    row = _review(["y", answer, "y", "y"])
    assert row.corrected_action is None


def test_few_shot_skips_all_skipped() -> None:
    rows = [
        OperatorFeedback(
            incident_id="inc-1",
            original_severity="sev2",
            original_action="a",
            action_status="skipped",
            severity_status="skipped",
            timestamp="2026-01-01T00:00:00Z",
        ),
        OperatorFeedback(
            incident_id="inc-2",
            original_severity="sev2",
            corrected_severity="sev1",
            original_action="a",
            action_status="accepted",
            severity_status="corrected",
            timestamp="2026-01-01T00:00:00Z",
        ),
    ]
    examples = build_few_shot_examples(rows)
    assert len(examples) == 1
    assert examples[0]["incident_id"] == "inc-2"


def test_escalation_only_sev0_sev1() -> None:
    incidents = [
        IncidentGroup(
            incident_id="inc-1",
            title="t",
            summary="s",
            alert_ids=["a-1"],
            primary_service="payments-ledger",
            suspected_cause="x",
            blast_radius="global",
            confidence=0.9,
        ),
        IncidentGroup(
            incident_id="inc-2",
            title="t2",
            summary="s2",
            alert_ids=["a-2"],
            primary_service="internal-dev-agent",
            suspected_cause="x",
            blast_radius="single_service",
            confidence=0.9,
        ),
    ]
    sevs = [
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
    services = [
        ServiceMeta(
            service="payments-ledger",
            tier="critical",
            owner_team="Payments Platform",
            customer_impact="x",
        ),
        ServiceMeta(
            service="internal-dev-agent",
            tier="medium",
            owner_team="Developer Productivity",
            customer_impact="x",
        ),
    ]
    bundle = build_escalation_bundle(
        incidents=incidents, severities=sevs, services=services
    )
    assert len(bundle) == 1
    assert bundle[0]["page"] is True
    assert bundle[0]["suggested_channel"] == "#incident-payments-platform"
