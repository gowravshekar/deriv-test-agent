"""Tool for agents-cli eval: summarize a completed pair's artifacts."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.pipeline.io import PIPELINE_FILES_ROOT, read_json, read_jsonl

EVAL_FOCUSES = {
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


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def summarize_pair(pair: str = "public", focus: str = "overview") -> str:
    """Summarize incident command artifacts for a pair folder.

    Args:
        pair: Name of the folder under pipeline_files/ (also the session id).
        focus: Pipeline area to summarize. Use overview unless the user asks
            about a specific stage.

    Returns:
        Grounding evidence for the requested pipeline area.
    """
    base = PIPELINE_FILES_ROOT / pair
    if not base.is_dir():
        return f"Pair folder not found: {base}"
    if focus not in EVAL_FOCUSES:
        return (
            f"Unknown focus {focus!r}. Allowed focuses: "
            f"{', '.join(sorted(EVAL_FOCUSES))}."
        )

    def load(name: str) -> Any:
        path = base / name
        if not path.is_file():
            return None
        return read_json(path)

    groups = load("incident_groups.json") or []
    sevs = {s["incident_id"]: s for s in (load("severity_assessments.json") or [])}
    safety = load("safety_review.json") or []
    final = load("incident_command_output.json") or {}

    if focus == "inputs_and_lifecycle":
        alerts = load("alerts.json") or []
        services = load("services.json") or []
        runbooks = load("runbooks.json") or []
        stages = final.get("stages") or []
        return "\n".join(
            [
                f"Pipeline lifecycle evidence for pair={pair}.",
                (
                    f"Inputs loaded: {len(alerts)} alerts, {len(services)} service "
                    f"records, and {len(runbooks)} runbooks."
                ),
                f"Executed stages in order: {', '.join(stages)}.",
                (
                    "Validation completed and results were finalised."
                    if stages[-2:] == ["VALIDATION_COMPLETE", "RESULTS_FINALISED"]
                    else "Validation/finalisation evidence is incomplete."
                ),
                (
                    f"Quarantined input records: "
                    f"{len(final.get('quarantined_inputs') or [])}."
                ),
            ]
        )

    if focus == "incident_grouping":
        assigned = [aid for group in groups for aid in group.get("alert_ids", [])]
        lines = [
            f"Incident grouping evidence for pair={pair}.",
            f"{len(assigned)} alerts were assigned across {len(groups)} incidents.",
        ]
        lines.extend(
            f"{g.get('incident_id')} has primary service {g.get('primary_service')}, "
            f"alert ids {', '.join(g.get('alert_ids') or [])}, blast radius "
            f"{g.get('blast_radius')}, confidence {g.get('confidence')}, and "
            f"needs_human_review={g.get('needs_human_review')}."
            for g in groups
        )
        return "\n".join(lines)

    if focus == "severity_assessment":
        rows = load("severity_assessments.json") or []
        lines = [f"Severity assessment evidence for pair={pair}."]
        lines.extend(
            f"{row.get('incident_id')} is {row.get('severity')} with status "
            f"{row.get('status')}; business impact is {row.get('business_impact')}; "
            f"technical impact is {row.get('technical_impact')}; reasoning is "
            f"{row.get('reasoning')}."
            for row in rows
        )
        return "\n".join(lines)

    if focus == "action_proposals":
        rows = load("action_proposals.json") or []
        lines = [f"First-response action proposal evidence for pair={pair}."]
        lines.extend(
            f"{row.get('incident_id')} proposes {row.get('action')} at model safety "
            f"level {row.get('safety_level')}; expected effect is "
            f"{row.get('expected_effect')}; risk is {row.get('risk')}."
            for row in rows
        )
        return "\n".join(lines)

    if focus == "safety_guardrails":
        lines = [
            f"Deterministic safety guardrail evidence for pair={pair}.",
            "These are recommendations only; no action was executed.",
        ]
        lines.extend(
            f"{row.get('incident_id')} action {row.get('action')} changed from model "
            f"safety {row.get('model_safety_level')} to final safety "
            f"{row.get('final_safety_level')}; guardrail reasons are "
            f"{', '.join(row.get('guardrail_reasons') or ['none'])}."
            for row in safety
        )
        return "\n".join(lines)

    if focus == "stakeholder_drafting":
        rows = load("stakeholder_updates.json") or []
        lines = [f"Stakeholder drafting evidence for pair={pair}."]
        lines.extend(
            f"{row.get('incident_id')} engineering update: "
            f"{row.get('engineering_update')} Executive update: "
            f"{row.get('executive_update')}"
            for row in rows
        )
        return "\n".join(lines)

    if focus == "operator_review":
        rows = read_jsonl(base / "operator_feedback.jsonl")
        lines = [f"Operator review evidence for pair={pair}."]
        lines.extend(
            f"{row.get('incident_id')} severity review was "
            f"{row.get('severity_status')} and action review was "
            f"{row.get('action_status')}; original severity was "
            f"{row.get('original_severity')} and corrected severity was "
            f"{row.get('corrected_severity')}; original action was "
            f"{row.get('original_action')} and corrected action was "
            f"{row.get('corrected_action')}."
            for row in rows
        )
        return "\n".join(lines)

    if focus == "feedback_and_redecision":
        severity_rows = load("severity_redecision.json") or []
        action_rows = load("actions_redecision.json") or []
        feedback_rows = read_jsonl(base / "operator_feedback.jsonl")
        calls = read_jsonl(base / "llm_calls.jsonl")
        feedback_calls = [
            row
            for row in calls
            if row.get("stage")
            in {
                "incident_grouping_redecision",
                "severity_redecision",
                "action_redecision",
            }
            and row.get("few_shot_examples_included")
        ]
        feedback_counts = Counter(str(row.get("stage")) for row in feedback_calls)
        return "\n".join(
            [
                f"Feedback and re-decision evidence for pair={pair}.",
                f"Operator feedback records available: {len(feedback_rows)}.",
                (
                    f"Re-decision produced {len(severity_rows)} severity rows and "
                    f"{len(action_rows)} action rows."
                ),
                (
                    f"{len(feedback_calls)} re-decision LLM calls explicitly logged "
                    "few_shot_examples_included=true."
                ),
                (
                    "Feedback-injected call counts by stage: "
                    f"{_compact(dict(feedback_counts))}."
                ),
                f"Severity re-decision rows: {_compact(severity_rows)}.",
                f"Action re-decision rows: {_compact(action_rows)}.",
            ]
        )

    if focus == "comparison":
        comparison = final.get("before_after_comparison") or []
        delta = final.get("agreement_delta") or {}
        return "\n".join(
            [
                f"Before and after comparison evidence for pair={pair}.",
                f"Comparison rows: {_compact(comparison)}.",
                f"Agreement delta count and rate values: {_compact(delta)}.",
            ]
        )

    if focus == "analytics":
        analytics = (
            load("analytics_summary.json") or final.get("analytics_summary") or {}
        )
        return "\n".join(
            [
                f"Analytics evidence for pair={pair}.",
                f"Analytics summary: {_compact(analytics)}.",
            ]
        )

    if focus == "prior_feedback":
        status = load("feedback_store_status.json") or {}
        return "\n".join(
            [
                f"Prior feedback store evidence for pair={pair}.",
                f"Feedback store status: {_compact(status)}.",
            ]
        )

    if focus == "escalation":
        rows = load("escalation_bundle.json") or []
        return "\n".join(
            [
                f"Escalation evidence for pair={pair}.",
                f"Escalation bundle rows: {_compact(rows)}.",
            ]
        )

    if focus == "llm_audit":
        calls = read_jsonl(base / "llm_calls.jsonl")
        counts = Counter(str(row.get("stage")) for row in calls)
        required_fields = {
            "stage",
            "incident_id",
            "timestamp",
            "provider",
            "model",
            "prompt_hash",
            "input_artifacts",
            "output_artifact",
            "few_shot_examples_included",
        }
        complete = sum(required_fields.issubset(row) for row in calls)
        incident_ids = {str(row.get("incident_id")) for row in groups}
        action_ids = {
            str(row.get("incident_id"))
            for row in calls
            if row.get("stage") == "action_proposal"
        }
        drafting_ids = {
            str(row.get("incident_id"))
            for row in calls
            if row.get("stage") == "stakeholder_drafting"
        }
        feedback_redecision = [
            row
            for row in calls
            if row.get("stage")
            in {
                "incident_grouping_redecision",
                "severity_redecision",
                "action_redecision",
            }
        ]
        return "\n".join(
            [
                f"LLM audit evidence for pair={pair}.",
                f"The audit log contains {len(calls)} calls and {complete} complete records.",
                f"Call counts by stage: {_compact(dict(counts))}.",
                (
                    "Per-incident action-call coverage is complete="
                    f"{action_ids == incident_ids}; per-incident drafting-call coverage "
                    f"is complete={drafting_ids == incident_ids}."
                ),
                (
                    "All re-decision calls include few-shot feedback="
                    f"{bool(feedback_redecision) and all(row.get('few_shot_examples_included') for row in feedback_redecision)}."
                ),
                (
                    "Every logged call records stage, incident id, timestamp, provider, "
                    "model, prompt hash, input artifacts, output artifact, and the "
                    "few-shot flag."
                    if complete == len(calls)
                    else "Some logged calls are missing required audit fields."
                ),
            ]
        )

    lines = [
        f"Incident command summary for pair={pair}.",
        "Recommendations only — no actions were executed.",
        f"Incidents formed: {len(groups)}.",
    ]
    for g in groups:
        iid = g.get("incident_id")
        sev = sevs.get(iid, {})
        saf = [s for s in safety if s.get("incident_id") == iid]
        top = None
        rank = {"safe": 0, "needs_approval": 1, "forbidden": 2}
        if saf:
            saf_sorted = sorted(
                saf, key=lambda s: rank.get(s.get("final_safety_level"), 99)
            )
            top = saf_sorted[0]
        lines.append(
            f"- {iid}: {g.get('title')} | primary={g.get('primary_service')} | "
            f"severity={sev.get('severity', 'n/a')} | blast={g.get('blast_radius')} | "
            f"confidence={g.get('confidence')} | "
            f"top_action={top.get('action') if top else 'n/a'} "
            f"({top.get('final_safety_level') if top else 'n/a'})"
        )
        lines.append(f"  summary: {g.get('summary')}")
        if sev.get("reasoning"):
            lines.append(f"  severity_reasoning: {sev.get('reasoning')}")

    delta = final.get("agreement_delta")
    if delta:
        lines.append(f"Agreement delta: {json.dumps(delta)}")
    return "\n".join(lines)
