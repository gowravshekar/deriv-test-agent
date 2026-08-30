"""Escalation bundles for sev0/sev1 incidents."""

from __future__ import annotations

from typing import Any

from app.pipeline.models import IncidentGroup, ServiceMeta, SeverityAssessment


def _channel(owner_team: str) -> str:
    slug = owner_team.lower().replace("&", "and").replace(" ", "-")
    return f"#incident-{slug}"


def build_escalation_bundle(
    *,
    incidents: list[IncidentGroup],
    severities: list[SeverityAssessment],
    services: list[ServiceMeta],
) -> list[dict[str, Any]]:
    sev_by_id = {s.incident_id: s for s in severities}
    owner_by_service = {s.service: s.owner_team for s in services}
    bundle: list[dict[str, Any]] = []
    for incident in incidents:
        sev = sev_by_id.get(incident.incident_id)
        if sev is None or sev.severity not in {"sev0", "sev1"}:
            continue
        owner = owner_by_service.get(incident.primary_service, "Unknown")
        bundle.append(
            {
                "incident_id": incident.incident_id,
                "owner_team": owner,
                "page": True,
                "suggested_channel": _channel(owner),
                "summary": f"[{sev.severity}] {incident.title}: {incident.summary}",
            }
        )
    return bundle
