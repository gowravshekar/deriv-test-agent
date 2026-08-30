"""Shared artifact validation used by validate.py and CodeExecutionMetric."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.pipeline.guardrails import canonicalize_action
from app.pipeline.io import pair_dir, read_json, read_jsonl
from app.pipeline.models import (
    BLAST_RADII,
    REVIEW_ACTIONS,
    SAFETY_LEVELS,
    SEVERITIES,
    STATUSES,
    Alert,
    IncidentGroup,
    PipelineStage,
    Runbook,
    ServiceMeta,
)

REQUIRED_OUTPUTS = [
    "incident_groups.json",
    "severity_assessments.json",
    "action_proposals.json",
    "safety_review.json",
    "stakeholder_updates.json",
    "operator_feedback.jsonl",
    "severity_redecision.json",
    "actions_redecision.json",
    "incident_command_output.json",
    "analytics_summary.json",
    "feedback_store_status.json",
    "escalation_bundle.json",
    "llm_calls.jsonl",
]


def _fail(errors: list[str], msg: str) -> None:
    errors.append(msg)


def normalize_update(text: str) -> str:
    """Strip incident ids so boilerplate reused across incidents collapses to one key."""
    without_ids = re.sub(r"inc-[0-9a-z-]+", "<incident>", text.strip().lower())
    return " ".join(without_ids.split())


def validate_pair(pair: str, *, root: Path | None = None) -> list[str]:
    errors: list[str] = []
    try:
        base = pair_dir(pair, root=root)
    except ValueError as exc:
        return [str(exc)]
    if not base.is_dir():
        return [f"Pair folder missing: {base}"]

    # Input schemas: skip invalid rows (same as load_inputs) instead of aborting.
    for name, model in (
        ("alerts.json", Alert),
        ("services.json", ServiceMeta),
        ("runbooks.json", Runbook),
    ):
        path = base / name
        if not path.is_file():
            _fail(errors, f"Missing input {path}")
            continue
        try:
            raw = read_json(path)
        except ValueError as exc:
            _fail(errors, str(exc))
            continue
        if not isinstance(raw, list):
            _fail(errors, f"{name} must be an array")
            continue
        for idx, item in enumerate(raw):
            try:
                model.model_validate(item)
            except Exception as exc:
                _fail(errors, f"{name}[{idx}] schema error: {exc}")

    missing_outputs = False
    for name in REQUIRED_OUTPUTS:
        if not (base / name).is_file():
            _fail(errors, f"Missing artifact {name}")
            missing_outputs = True

    if missing_outputs:
        return errors

    try:
        alerts: list[Alert] = []
        for item in read_json(base / "alerts.json"):
            try:
                alerts.append(Alert.model_validate(item))
            except Exception:
                continue
        groups_raw = read_json(base / "incident_groups.json")
        groups = [IncidentGroup.model_validate(g) for g in groups_raw]
    except Exception as exc:
        return [*errors, f"Failed loading core artifacts: {exc}"]

    alert_ids = {a.id for a in alerts}
    assigned: dict[str, str] = {}
    for g in groups:
        if g.blast_radius not in BLAST_RADII:
            _fail(errors, f"{g.incident_id} invalid blast_radius {g.blast_radius}")
        if not 0.0 <= g.confidence <= 1.0:
            _fail(errors, f"{g.incident_id} confidence out of range")
        if g.confidence < 0.70 and not g.needs_human_review:
            _fail(
                errors, f"{g.incident_id} confidence < 0.70 must set needs_human_review"
            )
        for aid in g.alert_ids:
            if aid in assigned:
                _fail(errors, f"Alert {aid} assigned to multiple incidents")
            assigned[aid] = g.incident_id
    missing = alert_ids - set(assigned)
    extra = set(assigned) - alert_ids
    if missing:
        _fail(errors, f"Alerts not assigned to an incident: {sorted(missing)}")
    if extra:
        _fail(errors, f"Unknown alert ids in incidents: {sorted(extra)}")

    for row in read_json(base / "severity_assessments.json"):
        if row.get("severity") not in SEVERITIES:
            _fail(errors, f"Invalid severity {row.get('severity')}")
        if row.get("status") not in STATUSES:
            _fail(errors, f"Invalid status {row.get('status')}")

    for row in read_json(base / "safety_review.json"):
        if row.get("final_safety_level") not in SAFETY_LEVELS:
            _fail(errors, f"Invalid final_safety_level {row.get('final_safety_level')}")
        if row.get("model_safety_level") not in SAFETY_LEVELS:
            _fail(errors, f"Invalid model_safety_level {row.get('model_safety_level')}")

    incident_ids = {g.incident_id for g in groups}
    updates = read_json(base / "stakeholder_updates.json")
    update_ids = {u.get("incident_id") for u in updates}
    if incident_ids - update_ids:
        _fail(
            errors,
            f"Missing stakeholder updates for {sorted(incident_ids - update_ids)}",
        )
    seen_by_field: dict[str, dict[str, str]] = {
        "engineering_update": {},
        "executive_update": {},
    }
    for u in updates:
        eng = (u.get("engineering_update") or "").strip()
        exe = (u.get("executive_update") or "").strip()
        if eng and exe and eng == exe:
            _fail(
                errors,
                f"{u.get('incident_id')} engineering_update and executive_update "
                "must differ",
            )
        for field, text in (("engineering_update", eng), ("executive_update", exe)):
            key = normalize_update(text)
            if not key:
                continue
            seen = seen_by_field[field]
            if key in seen:
                _fail(
                    errors,
                    f"{u.get('incident_id')} {field} is boilerplate identical to "
                    f"{seen[key]} apart from the incident id",
                )
            else:
                seen[key] = str(u.get("incident_id"))

    feedback = read_jsonl(base / "operator_feedback.jsonl")
    if not feedback:
        _fail(errors, "operator_feedback.jsonl is empty")
    for row in feedback:
        if row.get("severity_status") not in REVIEW_ACTIONS:
            _fail(errors, f"Invalid severity_status {row.get('severity_status')}")
        if row.get("action_status") not in REVIEW_ACTIONS:
            _fail(errors, f"Invalid action_status {row.get('action_status')}")

    final = read_json(base / "incident_command_output.json")
    expected_stages = [
        stage.value
        for stage in PipelineStage
        if stage
        not in {
            PipelineStage.VALIDATION_COMPLETE,
            PipelineStage.RESULTS_FINALISED,
        }
    ]
    feedback_status = read_json(base / "feedback_store_status.json")
    if not feedback_status.get("prior_feedback_loaded"):
        expected_stages.remove(PipelineStage.PRIOR_FEEDBACK_LOADED.value)
    recorded = list(final.get("stages") or [])
    while recorded and recorded[-1] in {
        PipelineStage.VALIDATION_COMPLETE.value,
        PipelineStage.RESULTS_FINALISED.value,
    }:
        recorded.pop()
    if recorded != expected_stages:
        _fail(errors, "incident_command_output.json has missing or out-of-order stages")
    if "agreement_delta" not in final and "before_after_comparison" not in final:
        _fail(errors, "incident_command_output.json missing comparison/delta")
    if "agreement_delta" not in final:
        _fail(errors, "agreement_delta missing from incident_command_output.json")
    if "before_after_comparison" not in final:
        _fail(errors, "before_after_comparison missing")
    comparison_ids = {
        row.get("incident_id") for row in final.get("before_after_comparison") or []
    }
    if comparison_ids != incident_ids:
        _fail(errors, "before_after_comparison must cover every incident")

    for artifact in (
        "severity_assessments.json",
        "action_proposals.json",
        "safety_review.json",
        "stakeholder_updates.json",
        "severity_redecision.json",
        "actions_redecision.json",
    ):
        artifact_ids = {
            row.get("incident_id") for row in read_json(base / artifact) or []
        }
        if artifact_ids != incident_ids:
            _fail(errors, f"{artifact} must cover every incident")

    calls = read_jsonl(base / "llm_calls.jsonl")
    stages = {c.get("stage") for c in calls}
    redecision_required = {
        "severity_redecision",
        "action_redecision",
        "incident_grouping_redecision",
    }
    for required in redecision_required:
        if required not in stages:
            _fail(errors, f"llm_calls.jsonl missing stage {required}")
    first_pass_required = {
        "incident_grouping",
        "severity_assessment",
        "action_proposal",
        "stakeholder_drafting",
    }
    if stages & first_pass_required:
        for required in first_pass_required:
            if required not in stages:
                _fail(errors, f"llm_calls.jsonl missing stage {required}")

    if "action_proposal" in stages:
        action_calls = [c for c in calls if c.get("stage") == "action_proposal"]
        if {c.get("incident_id") for c in action_calls} != incident_ids:
            _fail(
                errors,
                "Expected one or more action_proposal LLM calls per incident",
            )
    if "stakeholder_drafting" in stages:
        drafting_calls = [c for c in calls if c.get("stage") == "stakeholder_drafting"]
        if {c.get("incident_id") for c in drafting_calls} != incident_ids:
            _fail(errors, "Expected one stakeholder_drafting LLM call per incident")

    redecision_calls = [
        c
        for c in calls
        if c.get("stage")
        in {"severity_redecision", "action_redecision", "incident_grouping_redecision"}
    ]
    if not any(c.get("few_shot_examples_included") for c in redecision_calls):
        _fail(errors, "Re-decision LLM calls must set few_shot_examples_included true")

    # Safety override check: forbidden runbook actions must end forbidden
    try:
        runbooks = {
            r["service"]: r for r in read_json(base / "runbooks.json") if "service" in r
        }
        groups_by_id = {g.incident_id: g for g in groups}
        for row in read_json(base / "safety_review.json"):
            inc = groups_by_id.get(row.get("incident_id"))
            if not inc:
                continue
            rb = runbooks.get(inc.primary_service)
            if not rb:
                continue
            forbidden = {
                canonicalize_action(action)
                for action in (rb.get("forbidden_actions") or [])
            }
            action_key = canonicalize_action(str(row.get("action") or ""))
            if action_key in forbidden and row.get("final_safety_level") != "forbidden":
                _fail(
                    errors,
                    f"Safety did not forbid {row.get('action')} for {row.get('incident_id')}",
                )
    except Exception as exc:
        _fail(errors, f"Safety override check failed: {exc}")

    return errors


def score_pair(pair: str = "public", *, root: Path | None = None) -> dict[str, Any]:
    """Return CodeExecutionMetric-style score for CI."""
    errors = validate_pair(pair, root=root)
    if errors:
        return {"score": 0.0, "explanation": "; ".join(errors[:8])}
    return {"score": 1.0, "explanation": f"Pair {pair} artifacts and contracts OK"}
