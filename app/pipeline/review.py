"""Interactive terminal operator review."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from app.pipeline.guardrails import canonicalize_action, review_action, safest_action
from app.pipeline.llm import utc_now
from app.pipeline.models import (
    REVIEW_ACTIONS,
    SEVERITIES,
    STATUSES,
    ActionProposal,
    IncidentGroup,
    OperatorFeedback,
    Runbook,
    SafetyReview,
    ServiceMeta,
    SeverityAssessment,
)

PromptFn = Callable[[str], str]


def _parse_choice(raw: str, allowed: set[str]) -> tuple[str, str | None]:
    """Return (status, corrected_value_or_none)."""
    text = raw.strip()
    lower = text.lower()
    if lower in {"y", "yes", "accepted"}:
        return "accepted", None
    if lower in {"skip", "s", "skipped"}:
        return "skipped", None
    if lower.startswith("correct to:"):
        value = text.split(":", 1)[1].strip()
        return "corrected", value
    if lower.startswith("correct to "):
        value = text[len("correct to ") :].strip()
        return "corrected", value
    # bare value treated as correction (case-insensitive, canonicalized)
    allowed_by_key = {canonicalize_action(item): item for item in allowed}
    matched = allowed_by_key.get(canonicalize_action(text))
    if matched is not None:
        return "corrected", matched
    raise ValueError(f"Unrecognized input: {raw!r}")


def _confirm(ask: PromptFn, question: str) -> bool:
    return ask(f"{question} (y/n): ").strip().lower() in {"y", "yes"}


def _guardrail_verdict(
    incident_id: str,
    action: str,
    *,
    runbook: Runbook | None,
    service: ServiceMeta | None,
) -> SafetyReview:
    """Score an operator-supplied action with the same deterministic pass as the model's."""
    return review_action(
        ActionProposal(
            incident_id=incident_id,
            action=action,
            why="operator override",
            expected_effect="operator override",
            risk="operator override",
            safety_level="safe",
        ),
        runbook=runbook,
        service=service,
    )


def require_tty() -> None:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Operator review requires an interactive TTY. "
            "Run: uv run python -m app.pipeline --pair <pair>"
        )


def review_incidents(
    *,
    incidents: list[IncidentGroup],
    severities: list[SeverityAssessment],
    proposals: list[ActionProposal],
    safety: list[SafetyReview],
    alerts_by_id: dict[str, Any],
    runbooks: list[Runbook] | None = None,
    services: list[ServiceMeta] | None = None,
    prompt_fn: PromptFn | None = None,
) -> list[OperatorFeedback]:
    ask = prompt_fn or input
    if prompt_fn is None:
        require_tty()

    sev_by_id = {s.incident_id: s for s in severities}
    runbook_by_service = {r.service: r for r in runbooks or []}
    service_by_name = {s.service: s for s in services or []}
    feedback: list[OperatorFeedback] = []

    for incident in incidents:
        sev = sev_by_id.get(incident.incident_id)
        top = safest_action(incident.incident_id, proposals, safety)
        alert_summaries = []
        for aid in incident.alert_ids:
            alert = alerts_by_id.get(aid)
            if alert is None:
                alert_summaries.append(aid)
            else:
                alert_summaries.append(
                    f"{aid}: {alert.service}/{alert.signal_type} — {alert.summary}"
                )
        safety_for = [
            s
            for s in safety
            if s.incident_id == incident.incident_id and s.action == top
        ]
        verdict = safety_for[0].final_safety_level if safety_for else "unknown"

        print("\n" + "=" * 72)
        print(f"Incident {incident.incident_id}: {incident.title}")
        print(f"Summary: {incident.summary}")
        print("Alerts:")
        for line in alert_summaries:
            print(f"  - {line}")
        print(f"Severity: {sev.severity if sev else 'unknown'}")
        print(f"Status: {sev.status if sev else 'open'}")
        print(f"Top action (safest): {top}")
        print(f"Final safety verdict: {verdict}")

        original_severity = sev.severity if sev else "sev3"
        original_status = sev.status if sev else "open"
        original_action = top or "unknown"

        while True:
            raw = ask(
                "severity correct? (y / correct to: [sev0|sev1|sev2|sev3] / skip): "
            )
            try:
                sev_status, corrected_sev = _parse_choice(raw, SEVERITIES)
                if sev_status == "corrected" and corrected_sev not in SEVERITIES:
                    print(f"Invalid severity {corrected_sev!r}. Try again.")
                    continue
                break
            except ValueError as exc:
                print(exc)

        runbook = runbook_by_service.get(incident.primary_service)
        service = service_by_name.get(incident.primary_service)
        runbook_actions = set(runbook.allowed_actions) if runbook else set()
        if runbook_actions:
            print(f"Runbook actions: {', '.join(sorted(runbook_actions))}")

        while True:
            raw = ask("action correct? (y / correct to: [action_name] / skip): ")
            try:
                act_status, corrected_act = _parse_choice(raw, runbook_actions)
            except ValueError as exc:
                if runbook_actions:
                    print(
                        f"{raw.strip()!r} is not a runbook action for "
                        f"{incident.primary_service}. Enter one of "
                        f"{', '.join(sorted(runbook_actions))}, or use "
                        "'correct to: <action>' to override."
                    )
                else:
                    print(exc)
                continue
            if act_status == "accepted":
                if not top:
                    print(
                        "No safe top action is available. "
                        "Skip or correct to a runbook action."
                    )
                    continue
                accepted_verdict = _guardrail_verdict(
                    incident.incident_id,
                    original_action,
                    runbook=runbook,
                    service=service,
                )
                if accepted_verdict.final_safety_level == "forbidden":
                    print(
                        f"Cannot accept forbidden action {original_action!r}: "
                        f"{'; '.join(accepted_verdict.guardrail_reasons)}. "
                        "Choose another action."
                    )
                    continue
                break
            if act_status != "corrected":
                break
            if not corrected_act:
                print("Provide an action name after 'correct to:'.")
                continue
            # An override still has to clear the guardrails the model's proposals face.
            verdict = _guardrail_verdict(
                incident.incident_id,
                corrected_act,
                runbook=runbook,
                service=service,
            )
            if verdict.final_safety_level == "forbidden":
                print(
                    f"Guardrails reject {corrected_act!r}: "
                    f"{'; '.join(verdict.guardrail_reasons)}. Choose another action."
                )
                continue
            if (
                runbook_actions
                and canonicalize_action(corrected_act)
                not in {canonicalize_action(a) for a in runbook_actions}
                and _confirm(
                    ask,
                    f"{corrected_act!r} is not in the runbook for "
                    f"{incident.primary_service}. Use it anyway?",
                )
                is False
            ):
                continue
            if verdict.final_safety_level == "needs_approval" and (
                _confirm(
                    ask,
                    f"{corrected_act!r} needs approval "
                    f"({'; '.join(verdict.guardrail_reasons)}). Record it anyway?",
                )
                is False
            ):
                continue
            break

        while True:
            raw = ask(
                "status? (y / correct to: [open|monitoring|mitigated|closed] / skip): "
            )
            try:
                st_status, corrected_st = _parse_choice(raw, STATUSES)
                if st_status == "corrected" and corrected_st not in STATUSES:
                    print(f"Invalid status {corrected_st!r}. Try again.")
                    continue
                break
            except ValueError as exc:
                print(exc)

        assert sev_status in REVIEW_ACTIONS
        assert act_status in REVIEW_ACTIONS

        feedback.append(
            OperatorFeedback(
                incident_id=incident.incident_id,
                original_severity=original_severity,  # type: ignore[arg-type]
                corrected_severity=corrected_sev if sev_status == "corrected" else None,  # type: ignore[arg-type]
                original_action=original_action,
                corrected_action=corrected_act if act_status == "corrected" else None,
                action_status=act_status,  # type: ignore[arg-type]
                severity_status=sev_status,  # type: ignore[arg-type]
                original_status=original_status,  # type: ignore[arg-type]
                corrected_status=corrected_st if st_status == "corrected" else None,  # type: ignore[arg-type]
                status_status=st_status,  # type: ignore[arg-type]
                timestamp=utc_now(),
            )
        )
    return feedback


def build_few_shot_examples(feedback: list[OperatorFeedback]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for row in feedback:
        if row.severity_status == "skipped" and row.action_status == "skipped":
            continue
        examples.append(
            {
                "incident_id": row.incident_id,
                "severity_status": row.severity_status,
                "original_severity": row.original_severity,
                "corrected_severity": row.corrected_severity,
                "action_status": row.action_status,
                "original_action": row.original_action,
                "corrected_action": row.corrected_action,
                "status_status": row.status_status,
                "original_status": row.original_status,
                "corrected_status": row.corrected_status,
            }
        )
    return examples
