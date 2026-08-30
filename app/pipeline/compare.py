"""Before/after comparison and agreement delta."""

from __future__ import annotations

from app.pipeline.guardrails import safest_action
from app.pipeline.models import (
    ActionProposal,
    AgreementDelta,
    BeforeAfterRow,
    OperatorFeedback,
    SafetyReview,
    SeverityAssessment,
)


def _operator_severity(fb: OperatorFeedback) -> str | None:
    if fb.severity_status == "accepted":
        return fb.original_severity
    if fb.severity_status == "corrected":
        return fb.corrected_severity
    return None


def _operator_action(fb: OperatorFeedback) -> str | None:
    if fb.action_status == "accepted":
        return fb.original_action
    if fb.action_status == "corrected":
        return fb.corrected_action
    return None


def compute_comparison(
    *,
    feedback: list[OperatorFeedback],
    original_severities: list[SeverityAssessment],
    redecided_severities: list[SeverityAssessment],
    original_proposals: list[ActionProposal],
    original_safety: list[SafetyReview],
    redecided_proposals: list[ActionProposal],
    redecided_safety: list[SafetyReview],
) -> tuple[list[BeforeAfterRow], AgreementDelta]:
    orig_sev = {s.incident_id: s.severity for s in original_severities}
    re_sev = {s.incident_id: s.severity for s in redecided_severities}

    rows: list[BeforeAfterRow] = []
    labeled = [
        fb
        for fb in feedback
        if fb.severity_status in {"accepted", "corrected"}
        or fb.action_status in {"accepted", "corrected"}
    ]

    sev_before = sev_after = 0
    act_before = act_after = 0
    n_sev = n_act = 0

    for fb in feedback:
        op_sev = _operator_severity(fb)
        op_act = _operator_action(fb)
        original_top = (
            safest_action(fb.incident_id, original_proposals, original_safety)
            or fb.original_action
        )
        re_top = (
            safest_action(fb.incident_id, redecided_proposals, redecided_safety)
            or original_top
        )
        redecided_severity = re_sev.get(
            fb.incident_id, orig_sev.get(fb.incident_id, fb.original_severity)
        )

        before_sev_match = op_sev is not None and fb.original_severity == op_sev
        after_sev_match = op_sev is not None and redecided_severity == op_sev
        before_act_match = op_act is not None and original_top == op_act
        after_act_match = op_act is not None and re_top == op_act

        if fb.severity_status in {"accepted", "corrected"} and op_sev is not None:
            n_sev += 1
            if before_sev_match:
                sev_before += 1
            if after_sev_match:
                sev_after += 1
        if fb.action_status in {"accepted", "corrected"} and op_act is not None:
            n_act += 1
            if before_act_match:
                act_before += 1
            if after_act_match:
                act_after += 1

        # True only when a labeled field disagreed before and agrees after.
        moved = (after_sev_match and not before_sev_match) or (
            after_act_match and not before_act_match
        )

        rows.append(
            BeforeAfterRow(
                incident_id=fb.incident_id,
                original_severity=fb.original_severity,
                operator_severity=op_sev,
                redecided_severity=redecided_severity,
                original_top_action=original_top,
                operator_action=op_act,
                redecided_top_action=re_top,
                moved_toward_operator=moved,
            )
        )

    # Rates are per field, over incidents the operator actually labeled.
    sev_n = n_sev or 0
    act_n = n_act or 0
    sev_rate_before = (sev_before / sev_n) if sev_n else 0.0
    sev_rate_after = (sev_after / sev_n) if sev_n else 0.0
    act_rate_before = (act_before / act_n) if act_n else 0.0
    act_rate_after = (act_after / act_n) if act_n else 0.0

    delta = AgreementDelta(
        n_labeled=len(labeled),
        severity_before_matches=sev_before,
        severity_after_matches=sev_after,
        severity_count_delta=sev_after - sev_before,
        severity_rate_before=sev_rate_before,
        severity_rate_after=sev_rate_after,
        severity_rate_delta=sev_rate_after - sev_rate_before,
        action_before_matches=act_before,
        action_after_matches=act_after,
        action_count_delta=act_after - act_before,
        action_rate_before=act_rate_before,
        action_rate_after=act_rate_after,
        action_rate_delta=act_rate_after - act_rate_before,
    )
    return rows, delta
