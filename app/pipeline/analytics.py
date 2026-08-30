"""Analytics summary builder."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.pipeline.models import (
    AgreementDelta,
    Alert,
    IncidentGroup,
    OperatorFeedback,
    SafetyReview,
    SeverityAssessment,
)


def build_analytics(
    *,
    alerts: list[Alert],
    incidents: list[IncidentGroup],
    severities: list[SeverityAssessment],
    safety: list[SafetyReview],
    feedback: list[OperatorFeedback],
    delta: AgreementDelta,
) -> dict[str, Any]:
    confidences = [i.confidence for i in incidents]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    sev_dist = Counter(s.severity for s in severities)
    blast_dist = Counter(i.blast_radius for i in incidents)
    safety_dist = Counter(s.final_safety_level for s in safety)
    corrections = sum(
        1
        for f in feedback
        if f.severity_status == "corrected" or f.action_status == "corrected"
    )
    service_counts: Counter[str] = Counter()
    for incident in incidents:
        service_counts[incident.primary_service] += 1

    return {
        "incident_count": len(incidents),
        "alert_count": len(alerts),
        "average_grouping_confidence": round(avg_conf, 4),
        "severity_distribution": dict(sev_dist),
        "blast_radius_distribution": dict(blast_dist),
        "human_review_flag_count": sum(1 for i in incidents if i.needs_human_review),
        "proposed_actions_by_safety_level": dict(safety_dist),
        "operator_correction_count": corrections,
        "agreement_delta": delta.model_dump(mode="json"),
        "services_most_involved": [
            {"service": svc, "incident_count": count}
            for svc, count in service_counts.most_common()
        ],
    }


def print_analytics(analytics: dict[str, Any]) -> None:
    print("\n--- Analytics summary ---")
    print(
        f"Incidents: {analytics['incident_count']} from {analytics['alert_count']} alerts"
    )
    print(f"Avg grouping confidence: {analytics['average_grouping_confidence']}")
    print(f"Severity distribution: {analytics['severity_distribution']}")
    print(f"Blast radius: {analytics['blast_radius_distribution']}")
    print(f"Human review flags: {analytics['human_review_flag_count']}")
    print(f"Actions by safety: {analytics['proposed_actions_by_safety_level']}")
    print(f"Operator corrections: {analytics['operator_correction_count']}")
    print(f"Agreement delta: {analytics['agreement_delta']}")
    print(f"Services involved: {analytics['services_most_involved']}")
