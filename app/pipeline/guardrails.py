"""Deterministic safety pass over proposed actions."""

from __future__ import annotations

from app.pipeline.guardrail_config import (
    ACTION_ALIASES,
    FORBIDDEN_CATEGORY_TOKENS,
    ROLLBACK_ACTIONS,
    TRAFFIC_ALTERING_ACTIONS,
)
from app.pipeline.models import (
    SAFETY_RANK,
    ActionProposal,
    Runbook,
    SafetyLevel,
    SafetyReview,
    ServiceMeta,
)


def canonicalize_action(action: str) -> str:
    key = action.strip().lower().replace("-", "_").replace(" ", "_")
    spaced = action.strip().lower()
    if spaced in ACTION_ALIASES:
        return ACTION_ALIASES[spaced]
    if key in ACTION_ALIASES:
        return ACTION_ALIASES[key]
    return key


def category_forbidden_reasons(action: str) -> list[str]:
    normalized = canonicalize_action(action)
    haystack = normalized.replace("_", "")
    reasons: list[str] = []
    for category, tokens in FORBIDDEN_CATEGORY_TOKENS.items():
        for token in tokens:
            token_key = token.lower().replace("-", "_").replace(" ", "_")
            if token_key in normalized or token_key.replace("_", "") in haystack:
                reasons.append(f"semantic forbidden category: {category}")
                break
    return reasons


def review_action(
    proposal: ActionProposal,
    *,
    runbook: Runbook | None,
    service: ServiceMeta | None,
) -> SafetyReview:
    action = canonicalize_action(proposal.action)
    model_level: SafetyLevel = proposal.safety_level  # type: ignore[assignment]
    if model_level not in SAFETY_RANK:
        model_level = "needs_approval"

    reasons: list[str] = []
    final: SafetyLevel = model_level

    def escalate(level: SafetyLevel, reason: str) -> None:
        nonlocal final
        reasons.append(reason)
        if SAFETY_RANK[level] > SAFETY_RANK[final]:
            final = level

    if runbook:
        forbidden = {canonicalize_action(a) for a in runbook.forbidden_actions}
        if action in forbidden or canonicalize_action(proposal.action) in forbidden:
            escalate("forbidden", "action listed in forbidden_actions")

    for reason in category_forbidden_reasons(proposal.action):
        escalate("forbidden", reason)

    if service and service.change_freeze_required:
        if action not in ROLLBACK_ACTIONS:
            if action in TRAFFIC_ALTERING_ACTIONS:
                escalate(
                    "needs_approval",
                    "change freeze on critical path; traffic-altering action",
                )
            else:
                escalate(
                    "needs_approval",
                    "change freeze on critical dependency path",
                )

    return SafetyReview(
        incident_id=proposal.incident_id,
        action=proposal.action,
        model_safety_level=model_level,
        final_safety_level=final,
        guardrail_reasons=reasons,
    )


def review_proposals(
    proposals: list[ActionProposal],
    *,
    runbooks: list[Runbook],
    services: list[ServiceMeta],
    primary_service_by_incident: dict[str, str],
) -> list[SafetyReview]:
    runbook_by_service = {r.service: r for r in runbooks}
    service_by_name = {s.service: s for s in services}
    results: list[SafetyReview] = []
    for proposal in proposals:
        primary = primary_service_by_incident.get(proposal.incident_id)
        results.append(
            review_action(
                proposal,
                runbook=runbook_by_service.get(primary or ""),
                service=service_by_name.get(primary or ""),
            )
        )
    return results


def safest_action(
    incident_id: str,
    proposals: list[ActionProposal],
    safety: list[SafetyReview],
) -> str | None:
    incident_props = [p for p in proposals if p.incident_id == incident_id]
    if not incident_props:
        return None
    safety_by_action = {
        (s.incident_id, s.action): s for s in safety if s.incident_id == incident_id
    }
    ranked = sorted(
        incident_props,
        key=lambda p: (
            SAFETY_RANK.get(
                safety_by_action.get(
                    (incident_id, p.action),
                    SafetyReview(
                        incident_id=incident_id,
                        action=p.action,
                        model_safety_level=p.safety_level,
                        final_safety_level=p.safety_level,
                    ),
                ).final_safety_level,
                99,
            ),
            incident_props.index(p),
        ),
    )
    top = ranked[0]
    top_review = safety_by_action.get((incident_id, top.action))
    top_level = (
        top_review.final_safety_level if top_review is not None else top.safety_level
    )
    if top_level == "forbidden":
        return None
    return top.action
