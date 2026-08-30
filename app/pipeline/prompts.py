"""Prompt builders for each LLM stage."""

from __future__ import annotations

import json
from typing import Any

from app.pipeline.models import (
    Alert,
    IncidentGroup,
    Runbook,
    ServiceMeta,
    SeverityAssessment,
)


def _dumps(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def feedback_block(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""
    return (
        "\n\nOPERATOR FEEDBACK EXAMPLES (learn from these corrections):\n"
        + _dumps(examples)
        + "\nApply similar judgment on this run.\n"
    )


GROUPING_SCHEMA = {
    "incidents": [
        {
            "incident_id": "inc-001",
            "title": "string",
            "summary": "string",
            "alert_ids": ["a-101"],
            "primary_service": "service-name",
            "suspected_cause": "string",
            "blast_radius": "single_service|multi_service|regional|global|unknown",
            "confidence": 0.0,
            "needs_human_review": False,
        }
    ]
}


def grouping_prompt(
    alerts: list[Alert],
    services: list[ServiceMeta],
    feedback_examples: list[dict[str, Any]] | None = None,
) -> str:
    return f"""You are an incident commander clustering production alerts.

Cluster potentially related alerts into incidents.
Assign every alert to exactly one primary incident.
confidence must be a float from 0.0 to 1.0.
If confidence < 0.70 set needs_human_review to true.

Alerts:
{_dumps([a.model_dump(mode="json") for a in alerts])}

Service metadata:
{_dumps([s.model_dump(mode="json") for s in services])}

Return JSON matching this schema:
{_dumps(GROUPING_SCHEMA)}
{feedback_block(feedback_examples or [])}
"""


SEVERITY_SCHEMA = {
    "assessments": [
        {
            "incident_id": "inc-001",
            "severity": "sev0|sev1|sev2|sev3",
            "status": "open",
            "business_impact": "string",
            "technical_impact": "string",
            "reasoning": "string",
        }
    ]
}


def severity_prompt(
    incidents: list[IncidentGroup],
    services: list[ServiceMeta],
    feedback_examples: list[dict[str, Any]] | None = None,
) -> str:
    return f"""You are assessing incident severity for a high-availability trading platform.

Severity criteria (use all of these):
- customer-facing impact
- trading or transaction impact
- security or compliance exposure
- breadth of affected services or regions
- whether the issue is rollout-correlated
- whether safe degradation exists
- whether data correctness or financial correctness may be at risk

Important: incidents involving trade execution dependency failure, payments reconciliation backlog,
or rollout-related risk-engine drift must NOT be treated as low-importance (sev3) without strong justification.

Statuses: open, monitoring, mitigated, closed
Severities: sev0, sev1, sev2, sev3

Incidents:
{_dumps([i.model_dump(mode="json") for i in incidents])}

Service metadata:
{_dumps([s.model_dump(mode="json") for s in services])}

Return JSON matching this schema:
{_dumps(SEVERITY_SCHEMA)}
{feedback_block(feedback_examples or [])}
"""


ACTION_SCHEMA = {
    "actions": [
        {
            "incident_id": "inc-001",
            "action": "action_name",
            "why": "string",
            "expected_effect": "string",
            "risk": "string",
            "safety_level": "safe|needs_approval|forbidden",
        }
    ]
}


def action_prompt(
    incident: IncidentGroup,
    severity: SeverityAssessment | None,
    runbook: Runbook | None,
    feedback_examples: list[dict[str, Any]] | None = None,
) -> str:
    runbook_text = (
        runbook.model_dump(mode="json")
        if runbook
        else {"notes": "No runbook found for primary service."}
    )
    return f"""Propose 1 to 3 recommended first-response actions for this incident.
Do NOT execute anything. Recommend only.
At least one action must be from allowed_actions unless you explicitly explain why none fit.
Never propose forbidden_actions.

Incident:
{_dumps(incident.model_dump(mode="json"))}

Severity assessment:
{_dumps(severity.model_dump(mode="json") if severity else {})}

Runbook:
{_dumps(runbook_text)}

Return JSON matching this schema (incident_id must be {incident.incident_id}):
{_dumps(ACTION_SCHEMA)}
{feedback_block(feedback_examples or [])}
"""


DRAFT_SCHEMA = {
    "incident_id": "inc-001",
    "engineering_update": (
        "market-data-stream and trade-execution are seeing quote timeouts after "
        "the feed delay. Likely impact is elevated order latency. Next step is "
        "to evaluate shift_traffic. Safety checks may block some actions pending "
        "approval. Owners are reviewing blast radius and containment."
    ),
    "executive_update": (
        "We are responding to degraded trading in one region. Customers may see "
        "slower orders. Containment is under review and will not proceed without "
        "approval where required."
    ),
}


def draft_prompt(
    incident: IncidentGroup,
    severity: SeverityAssessment | None,
    top_action: str | None,
    safety_rows: list[dict[str, Any]],
) -> str:
    return f"""Draft stakeholder communications for this incident. Recommend only; do not execute.
incident_id must be {incident.incident_id}

engineering_update and executive_update MUST be different texts. Do not copy one
into the other.

Both updates must be specific to THIS incident. Name the affected service, the
suspected cause, the severity, and the proposed action from the data below. Do not
write generic status text that would still read correctly for a different incident
if the incident_id were swapped.

Engineering update: 4 to 6 sentences covering affected systems, likely cause,
likely impact, immediate next steps (including the top proposed action), and any
blocked actions due to safety checks.

Executive update: 3 to 5 sentences, concise, low-jargon, no speculation stated as fact.
Mention customer or business impact and current containment posture. Do not reuse
the engineering paragraph.

Incident:
{_dumps(incident.model_dump(mode="json"))}

Severity:
{_dumps(severity.model_dump(mode="json") if severity else {})}

Top proposed action: {top_action}

Safety reviews for this incident:
{_dumps(safety_rows)}

Return JSON matching:
{_dumps(DRAFT_SCHEMA)}
"""


def normalize_incidents(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "incidents" in payload:
        return list(payload["incidents"])
    if isinstance(payload, list):
        return payload
    return []


def normalize_assessments(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "assessments" in payload:
        return list(payload["assessments"])
    if isinstance(payload, list):
        return payload
    return []


def normalize_actions(payload: Any, incident_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]]
    if isinstance(payload, dict) and "actions" in payload:
        rows = list(payload["actions"])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    for row in rows:
        row.setdefault("incident_id", incident_id)
    return rows
