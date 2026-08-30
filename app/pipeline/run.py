"""Stage-ordered incident command pipeline."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.pipeline.analytics import build_analytics, print_analytics
from app.pipeline.compare import compute_comparison
from app.pipeline.escalation import build_escalation_bundle
from app.pipeline.guardrails import canonicalize_action, review_proposals, safest_action
from app.pipeline.io import (
    load_first_pass_artifacts,
    load_inputs,
    model_dump_list,
    read_jsonl,
    write_json,
    write_jsonl,
)
from app.pipeline.llm import FakeJsonClient, LlmClient, LlmRecorder, VertexJsonClient
from app.pipeline.models import (
    ActionProposal,
    Alert,
    IncidentGroup,
    OperatorFeedback,
    PipelineStage,
    Runbook,
    SeverityAssessment,
    StakeholderUpdate,
)
from app.pipeline.prompts import (
    action_prompt,
    draft_prompt,
    grouping_prompt,
    normalize_actions,
    normalize_assessments,
    normalize_incidents,
    severity_prompt,
)
from app.pipeline.review import build_few_shot_examples, review_incidents
from app.pipeline.validate_artifacts import validate_pair


def _parse_incidents(raw_rows: list[dict[str, Any]]) -> list[IncidentGroup]:
    out: list[IncidentGroup] = []
    for row in raw_rows:
        try:
            inc = IncidentGroup.model_validate(row)
            if inc.confidence < 0.70:
                inc.needs_human_review = True
            out.append(inc)
        except ValidationError:
            # Flag via a quarantine-style stub incident if possible
            continue
    return out


def _parse_severities(raw_rows: list[dict[str, Any]]) -> list[SeverityAssessment]:
    out: list[SeverityAssessment] = []
    for row in raw_rows:
        try:
            out.append(SeverityAssessment.model_validate(row))
        except ValidationError:
            continue
    return out


def _parse_actions(raw_rows: list[dict[str, Any]]) -> list[ActionProposal]:
    out: list[ActionProposal] = []
    for row in raw_rows:
        try:
            out.append(ActionProposal.model_validate(row))
        except ValidationError:
            continue
    return out


def _stage_status(name: str, kind: str, detail: str) -> None:
    print(f"[{name}] {kind}  ({detail})", flush=True)


def _running_llm(
    name: str, recorder: LlmRecorder, *, incident_id: str | None = None
) -> None:
    label = f"{name} {incident_id}" if incident_id else name
    _stage_status(label, "RUNNING", f"{recorder.provider} {recorder.model}")


def _running_local(name: str, detail: str = "deterministic") -> None:
    _stage_status(name, "RUNNING", detail)


def _stored(name: str, artifact: str) -> None:
    _stage_status(name, "STORED", artifact)


def _dedupe_incident_alerts(incidents: list[IncidentGroup]) -> list[IncidentGroup]:
    seen: set[str] = set()
    for inc in incidents:
        unique: list[str] = []
        collision = False
        for aid in inc.alert_ids:
            if aid in seen:
                collision = True
                continue
            seen.add(aid)
            unique.append(aid)
        if collision:
            inc.needs_human_review = True
        inc.alert_ids = unique
    return incidents


def _align_regrouped(
    original: list[IncidentGroup], regrouped: list[IncidentGroup]
) -> list[IncidentGroup]:
    """Keep first-pass incident_ids so operator feedback still maps after regroup."""
    remaining = list(regrouped)
    aligned: list[IncidentGroup] = []
    for orig in original:
        orig_set = set(orig.alert_ids)
        best: IncidentGroup | None = None
        best_overlap = 0
        for cand in remaining:
            overlap = len(orig_set & set(cand.alert_ids))
            if overlap > best_overlap:
                best_overlap = overlap
                best = cand
        if best is not None and best_overlap > 0:
            remaining.remove(best)
            aligned.append(best.model_copy(update={"incident_id": orig.incident_id}))
        else:
            aligned.append(orig)
    return aligned


def _ensure_runbook_action(
    incidents: list[IncidentGroup],
    proposals: list[ActionProposal],
    runbooks: list[Runbook],
) -> list[ActionProposal]:
    runbook_by_service = {r.service: r for r in runbooks}
    for inc in incidents:
        runbook = runbook_by_service.get(inc.primary_service)
        allowed = list(runbook.allowed_actions) if runbook else []
        if not allowed:
            continue
        allowed_keys = {canonicalize_action(action) for action in allowed}
        inc_props = [p for p in proposals if p.incident_id == inc.incident_id]
        if any(canonicalize_action(p.action) in allowed_keys for p in inc_props):
            continue
        inc.needs_human_review = True
        proposals.append(
            ActionProposal(
                incident_id=inc.incident_id,
                action=allowed[0],
                why=(
                    "Fallback: first allowed runbook action "
                    "(none of the model proposals matched)."
                ),
                expected_effect="Human review",
                risk="Unknown",
                safety_level="needs_approval",
            )
        )
    return proposals


def _fill_orphan_alerts(
    incidents: list[IncidentGroup], alerts: list[Alert]
) -> list[IncidentGroup]:
    assigned = {aid for inc in incidents for aid in inc.alert_ids}
    for alert in alerts:
        if alert.id not in assigned:
            incidents.append(
                IncidentGroup(
                    incident_id=f"inc-orphan-{alert.id}",
                    title=f"Unclustered: {alert.service}",
                    summary=alert.summary,
                    alert_ids=[alert.id],
                    primary_service=alert.service,
                    suspected_cause="Grouping model omitted this alert",
                    blast_radius="unknown",
                    confidence=0.5,
                    needs_human_review=True,
                )
            )
    return incidents


def run_pipeline(
    pair: str = "public",
    *,
    client: LlmClient | None = None,
    prompt_fn: Callable[[str], str] | None = None,
    skip_review: bool = False,
    until: str | None = None,
    resume: bool = False,
    rerun: bool = False,
) -> Path:
    """Run the full staged pipeline for a pair folder.

    ``pair`` is also used as the logical ADK session_id.
    """
    stages: list[str] = [PipelineStage.INIT.value]
    session_id = pair

    alerts, services, runbooks, quarantined, pair_path = load_inputs(pair)
    stages.append(PipelineStage.INPUTS_LOADED.value)

    llm_log = pair_path / "llm_calls.jsonl"
    want_resume = not rerun and ((not skip_review) or resume)
    stored = load_first_pass_artifacts(pair_path) if want_resume else None
    use_resume = stored is not None

    if not use_resume and llm_log.exists():
        llm_log.unlink()

    llm_client = client or VertexJsonClient()
    recorder = LlmRecorder(llm_client, log_path=llm_log)

    prior_rows = read_jsonl(pair_path / "operator_feedback.jsonl")
    prior_feedback_loaded = bool(prior_rows)
    write_json(
        pair_path / "feedback_store_status.json",
        {
            "pair": pair,
            "session_id": session_id,
            "prior_feedback_loaded": prior_feedback_loaded,
            "prior_feedback_count": len(prior_rows),
        },
    )
    if prior_feedback_loaded:
        stages.append(PipelineStage.PRIOR_FEEDBACK_LOADED.value)

    prior_examples = [
        {
            "incident_id": r.get("incident_id"),
            "severity_status": r.get("severity_status"),
            "original_severity": r.get("original_severity"),
            "corrected_severity": r.get("corrected_severity"),
            "action_status": r.get("action_status"),
            "original_action": r.get("original_action"),
            "corrected_action": r.get("corrected_action"),
        }
        for r in prior_rows
        if r.get("severity_status") in {"accepted", "corrected"}
        or r.get("action_status") in {"accepted", "corrected"}
    ]

    runbook_by_service = {r.service: r for r in runbooks}

    if use_resume:
        assert stored is not None
        incidents, severities, proposals, safety, updates = stored
        print(
            "Resuming operator review from existing first-pass artifacts",
            flush=True,
        )
        _stored("incident_grouping", "incident_groups.json")
        _stored("severity_assessment", "severity_assessments.json")
        _stored("action_proposal", "action_proposals.json")
        _stored("safety_guardrails", "safety_review.json")
        _stored("stakeholder_drafting", "stakeholder_updates.json")
        stages.extend(
            [
                PipelineStage.INCIDENT_GROUPING_COMPLETE.value,
                PipelineStage.SEVERITY_ASSESSMENT_COMPLETE.value,
                PipelineStage.ACTION_PROPOSALS_COMPLETE.value,
                PipelineStage.SAFETY_GUARDRAILS_COMPLETE.value,
                PipelineStage.STAKEHOLDER_DRAFTING_COMPLETE.value,
            ]
        )
    else:
        # --- Stage 1: grouping ---
        _running_llm("incident_grouping", recorder)
        group_payload = recorder.call(
            stage="incident_grouping",
            prompt=grouping_prompt(alerts, services, prior_examples),
            input_artifacts=["alerts.json", "services.json"],
            output_artifact="incident_groups.json",
            few_shot_examples_included=bool(prior_examples),
        )
        incidents = _parse_incidents(normalize_incidents(group_payload))
        incidents = _dedupe_incident_alerts(incidents)
        incidents = _fill_orphan_alerts(incidents, alerts)
        write_json(pair_path / "incident_groups.json", model_dump_list(incidents))
        stages.append(PipelineStage.INCIDENT_GROUPING_COMPLETE.value)

        # --- Stage 2: severity ---
        _running_llm("severity_assessment", recorder)
        sev_payload = recorder.call(
            stage="severity_assessment",
            prompt=severity_prompt(incidents, services, prior_examples),
            input_artifacts=["incident_groups.json", "services.json"],
            output_artifact="severity_assessments.json",
            few_shot_examples_included=bool(prior_examples),
        )
        severities = _parse_severities(normalize_assessments(sev_payload))
        have = {s.incident_id for s in severities}
        for inc in incidents:
            if inc.incident_id not in have:
                severities.append(
                    SeverityAssessment(
                        incident_id=inc.incident_id,
                        severity="sev2",
                        status="open",
                        business_impact="Needs human review — severity parse failed",
                        technical_impact="Unknown",
                        reasoning="Defaulted after validation failure",
                    )
                )
                inc.needs_human_review = True
        write_json(pair_path / "severity_assessments.json", model_dump_list(severities))
        stages.append(PipelineStage.SEVERITY_ASSESSMENT_COMPLETE.value)

        # --- Stage 3: actions per incident ---
        proposals: list[ActionProposal] = []
        for incident in incidents:
            _running_llm("action_proposal", recorder, incident_id=incident.incident_id)
            payload = recorder.call(
                stage="action_proposal",
                prompt=action_prompt(
                    incident,
                    {s.incident_id: s for s in severities}.get(incident.incident_id),
                    runbook_by_service.get(incident.primary_service),
                    prior_examples,
                ),
                input_artifacts=[
                    "incident_groups.json",
                    "severity_assessments.json",
                    "runbooks.json",
                ],
                output_artifact="action_proposals.json",
                incident_id=incident.incident_id,
                few_shot_examples_included=bool(prior_examples),
            )
            rows = _parse_actions(normalize_actions(payload, incident.incident_id))
            if not rows:
                rb = runbook_by_service.get(incident.primary_service)
                fallback = (
                    rb.allowed_actions[0]
                    if rb and rb.allowed_actions
                    else "manual_review"
                )
                rows = [
                    ActionProposal(
                        incident_id=incident.incident_id,
                        action=fallback,
                        why="Fallback after action proposal parse failure",
                        expected_effect="Human review",
                        risk="Unknown",
                        safety_level="needs_approval",
                    )
                ]
                incident.needs_human_review = True
            proposals.extend(rows)
        proposals = _ensure_runbook_action(incidents, proposals, runbooks)
        write_json(pair_path / "action_proposals.json", model_dump_list(proposals))
        stages.append(PipelineStage.ACTION_PROPOSALS_COMPLETE.value)

        # --- Stage safety (deterministic) ---
        _running_local("safety_guardrails")
        primary_by_incident_live = {i.incident_id: i.primary_service for i in incidents}
        safety = review_proposals(
            proposals,
            runbooks=runbooks,
            services=services,
            primary_service_by_incident=primary_by_incident_live,
        )
        write_json(pair_path / "safety_review.json", model_dump_list(safety))
        stages.append(PipelineStage.SAFETY_GUARDRAILS_COMPLETE.value)

        # --- Stage 4: stakeholder drafts per incident ---
        updates: list[StakeholderUpdate] = []
        sev_by_id_live = {s.incident_id: s for s in severities}
        for incident in incidents:
            top = safest_action(incident.incident_id, proposals, safety)
            safety_rows = [
                s.model_dump(mode="json")
                for s in safety
                if s.incident_id == incident.incident_id
            ]
            _running_llm(
                "stakeholder_drafting", recorder, incident_id=incident.incident_id
            )
            payload = recorder.call(
                stage="stakeholder_drafting",
                prompt=draft_prompt(
                    incident,
                    sev_by_id_live.get(incident.incident_id),
                    top,
                    safety_rows,
                ),
                input_artifacts=[
                    "incident_groups.json",
                    "severity_assessments.json",
                    "safety_review.json",
                ],
                output_artifact="stakeholder_updates.json",
                incident_id=incident.incident_id,
            )
            try:
                if isinstance(payload, dict):
                    payload.setdefault("incident_id", incident.incident_id)
                    updates.append(StakeholderUpdate.model_validate(payload))
                else:
                    raise ValueError("not a dict")
            except Exception:
                sev_row = sev_by_id_live.get(incident.incident_id)
                severity_label = sev_row.severity if sev_row else "unassessed severity"
                business = (
                    sev_row.business_impact
                    if sev_row
                    else "Customer impact is under assessment"
                )
                technical = (
                    sev_row.technical_impact if sev_row else "Impact unconfirmed"
                )
                updates.append(
                    StakeholderUpdate(
                        incident_id=incident.incident_id,
                        engineering_update=(
                            f"Incident {incident.incident_id} ({severity_label}) affects "
                            f"{incident.primary_service} with {incident.blast_radius} blast radius. "
                            f"{incident.summary.strip().rstrip('.')}. "
                            f"Likely cause: {incident.suspected_cause.strip().rstrip('.')}. "
                            f"Technical impact: {technical.strip().rstrip('.')}. "
                            f"Immediate next step: evaluate action {top}. "
                            "Draft generation failed validation, so this update needs human review."
                        ),
                        executive_update=(
                            f"We are responding to a {severity_label} issue on "
                            f"{incident.primary_service}. "
                            f"{business.strip().rstrip('.')}. "
                            f"Early indications point to "
                            f"{incident.suspected_cause.strip().rstrip('.')}. "
                            "Containment actions are being reviewed and will not be applied "
                            "without approval where required."
                        ),
                    )
                )
                incident.needs_human_review = True
        write_json(pair_path / "stakeholder_updates.json", model_dump_list(updates))
        write_json(pair_path / "incident_groups.json", model_dump_list(incidents))
        stages.append(PipelineStage.STAKEHOLDER_DRAFTING_COMPLETE.value)

    sev_by_id = {s.incident_id: s for s in severities}
    primary_by_incident = {i.incident_id: i.primary_service for i in incidents}

    if until == "stakeholder":
        print(
            "Stopped after stakeholder drafting. "
            f"Re-run without --until to open operator review: pair={pair}",
            flush=True,
        )
        return pair_path

    # --- Operator review ---
    alerts_by_id = {a.id: a for a in alerts}
    if skip_review:
        _running_local("operator_review", "auto-accept")
        feedback = [
            OperatorFeedback(
                incident_id=inc.incident_id,
                original_severity=sev_by_id[inc.incident_id].severity
                if inc.incident_id in sev_by_id
                else "sev2",
                corrected_severity=None,
                original_action=safest_action(inc.incident_id, proposals, safety)
                or "manual_review",
                corrected_action=None,
                action_status="accepted",
                severity_status="accepted",
                original_status=sev_by_id[inc.incident_id].status
                if inc.incident_id in sev_by_id
                else "open",
                corrected_status=None,
                status_status="accepted",
                timestamp=__import__(
                    "app.pipeline.llm", fromlist=["utc_now"]
                ).utc_now(),
            )
            for inc in incidents
        ]
    else:
        _running_local("operator_review", "TTY")
        feedback = review_incidents(
            incidents=incidents,
            severities=severities,
            proposals=proposals,
            safety=safety,
            alerts_by_id=alerts_by_id,
            runbooks=runbooks,
            services=services,
            prompt_fn=prompt_fn,
        )
    _running_local("operator_feedback", "operator_feedback.jsonl")
    write_jsonl(
        pair_path / "operator_feedback.jsonl",
        [row.model_dump(mode="json") for row in feedback],
    )
    stages.append(PipelineStage.OPERATOR_REVIEW_COLLECTED.value)

    few_shot = build_few_shot_examples(feedback)
    # Also include prior examples
    few_shot = prior_examples + few_shot
    stages.append(PipelineStage.FEEDBACK_EXAMPLES_BUILT.value)

    # --- Always re-group ---
    _running_llm("incident_grouping_redecision", recorder)
    regroup_payload = recorder.call(
        stage="incident_grouping_redecision",
        prompt=grouping_prompt(alerts, services, few_shot),
        input_artifacts=["alerts.json", "services.json", "operator_feedback.jsonl"],
        output_artifact="incident_groups.json",
        few_shot_examples_included=True,
    )
    re_incidents = _parse_incidents(normalize_incidents(regroup_payload))
    if not re_incidents:
        re_incidents = incidents
    else:
        re_incidents = _align_regrouped(incidents, re_incidents)
        re_incidents = _dedupe_incident_alerts(re_incidents)
        re_incidents = _fill_orphan_alerts(re_incidents, alerts)
    # keep file as first-pass groups; re-group used for redecision context
    # (challenge requires incident_groups.json from stage 1; redecision uses severity/actions files)

    _running_llm("severity_redecision", recorder)
    re_sev_payload = recorder.call(
        stage="severity_redecision",
        prompt=severity_prompt(re_incidents, services, few_shot),
        input_artifacts=["incident_groups.json", "operator_feedback.jsonl"],
        output_artifact="severity_redecision.json",
        few_shot_examples_included=True,
    )
    re_severities = _parse_severities(normalize_assessments(re_sev_payload))
    if not re_severities:
        re_severities = severities
    write_json(pair_path / "severity_redecision.json", model_dump_list(re_severities))

    re_proposals: list[ActionProposal] = []
    re_sev_by_id = {s.incident_id: s for s in re_severities}
    re_primary = {i.incident_id: i.primary_service for i in re_incidents}
    for incident in re_incidents:
        _running_llm("action_redecision", recorder, incident_id=incident.incident_id)
        payload = recorder.call(
            stage="action_redecision",
            prompt=action_prompt(
                incident,
                re_sev_by_id.get(incident.incident_id),
                runbook_by_service.get(incident.primary_service),
                few_shot,
            ),
            input_artifacts=["operator_feedback.jsonl", "runbooks.json"],
            output_artifact="actions_redecision.json",
            incident_id=incident.incident_id,
            few_shot_examples_included=True,
        )
        rows = _parse_actions(normalize_actions(payload, incident.incident_id))
        if not rows:
            rows = [p for p in proposals if p.incident_id == incident.incident_id]
        re_proposals.extend(rows)
    write_json(pair_path / "actions_redecision.json", model_dump_list(re_proposals))

    _running_local("safety_recheck")
    re_safety = review_proposals(
        re_proposals,
        runbooks=runbooks,
        services=services,
        primary_service_by_incident=re_primary or primary_by_incident,
    )
    stages.append(PipelineStage.REDECISION_COMPLETE.value)

    _running_local("before_after_comparison")
    comparison, delta = compute_comparison(
        feedback=feedback,
        original_severities=severities,
        redecided_severities=re_severities,
        original_proposals=proposals,
        original_safety=safety,
        redecided_proposals=re_proposals,
        redecided_safety=re_safety,
    )
    stages.append(PipelineStage.BEFORE_AFTER_COMPARISON_COMPLETE.value)

    _running_local("analytics", "analytics_summary.json")
    analytics = build_analytics(
        alerts=alerts,
        incidents=incidents,
        severities=severities,
        safety=safety,
        feedback=feedback,
        delta=delta,
    )
    write_json(pair_path / "analytics_summary.json", analytics)
    print_analytics(analytics)
    stages.append(PipelineStage.ANALYTICS_GENERATED.value)

    _running_local("escalation_bundle", "escalation_bundle.json")
    escalation = build_escalation_bundle(
        incidents=re_incidents if re_incidents else incidents,
        severities=re_severities,
        services=services,
    )
    write_json(pair_path / "escalation_bundle.json", escalation)

    _running_local("incident_command_output", "incident_command_output.json")
    final = {
        "pair": pair,
        "session_id": session_id,
        "stages": stages,
        "incidents": model_dump_list(incidents),
        "severity_assessments": model_dump_list(severities),
        "action_proposals": model_dump_list(proposals),
        "safety_review": model_dump_list(safety),
        "stakeholder_updates": model_dump_list(updates),
        "operator_feedback": model_dump_list(feedback),
        "severity_redecision": model_dump_list(re_severities),
        "actions_redecision": model_dump_list(re_proposals),
        "before_after_comparison": model_dump_list(comparison),
        "agreement_delta": delta.model_dump(mode="json"),
        "analytics_summary": analytics,
        "escalation_bundle": escalation,
        "quarantined_inputs": quarantined,
    }
    write_json(pair_path / "incident_command_output.json", final)
    errors = validate_pair(pair, root=pair_path.parent)
    if errors:
        raise RuntimeError("Validation failed:\n" + "\n".join(errors))
    stages.append(PipelineStage.VALIDATION_COMPLETE.value)
    stages.append(PipelineStage.RESULTS_FINALISED.value)
    _running_local("RESULTS_FINALISED", "done")
    final["stages"] = stages
    write_json(pair_path / "incident_command_output.json", final)
    return pair_path


def default_fake_client() -> FakeJsonClient:
    """A scripted fake that produces a coherent public-fixture first pass + redecision."""

    import re

    def _incident_id_from_prompt(prompt: str) -> str | None:
        m = re.search(r"incident_id must be (inc-[0-9a-zA-Z-]+)", prompt)
        if m:
            return m.group(1)
        m = re.search(
            r'"incident_id":\s*"(inc-[0-9a-zA-Z-]+)"',
            prompt,
        )
        if m:
            return m.group(1)
        return None

    def _field_from_prompt(prompt: str, key: str) -> str | None:
        m = re.search(rf'"{key}":\s*"([^"]*)"', prompt)
        return m.group(1) if m and m.group(1) else None

    def _top_action_from_prompt(prompt: str) -> str | None:
        m = re.search(r"Top proposed action: (.+)", prompt)
        value = m.group(1).strip() if m else ""
        return value if value and value != "None" else None

    def _clause(text: str) -> str:
        return text.strip().rstrip(".")

    def _blocked_actions_from_prompt(prompt: str) -> list[str]:
        parts = prompt.split("Safety reviews for this incident:", 1)
        if len(parts) < 2:
            return []
        body = parts[1].split("Return JSON matching:", 1)[0]
        rows = re.findall(
            r'"action":\s*"([^"]+)",\s*"model_safety_level":\s*"[^"]+",\s*'
            r'"final_safety_level":\s*"([^"]+)"',
            body,
        )
        return [action for action, level in rows if level != "safe"]

    def handler(prompt: str) -> Any:
        if (
            "clustering production alerts" in prompt
            or "Cluster potentially related" in prompt
        ):
            return {
                "incidents": [
                    {
                        "incident_id": "inc-001",
                        "title": "Market data latency impacting trade execution",
                        "summary": "EU market-data latency and errors cascading to trade timeouts.",
                        "alert_ids": ["a-101", "a-102", "a-103"],
                        "primary_service": "market-data-stream",
                        "suspected_cause": "Consumer lag and fanout failures",
                        "blast_radius": "multi_service",
                        "confidence": 0.88,
                        "needs_human_review": False,
                    },
                    {
                        "incident_id": "inc-002",
                        "title": "Auth session CPU after deploy",
                        "summary": "CPU spike after recent auth deploy with normal login traffic.",
                        "alert_ids": ["a-104"],
                        "primary_service": "auth-session",
                        "suspected_cause": "Recent deployment",
                        "blast_radius": "single_service",
                        "confidence": 0.81,
                        "needs_human_review": False,
                    },
                    {
                        "incident_id": "inc-003",
                        "title": "Payments withdrawal reconciliation backlog",
                        "summary": "Global withdrawal queue depth exceeded with provider delays.",
                        "alert_ids": ["a-105"],
                        "primary_service": "payments-ledger",
                        "suspected_cause": "Settlement provider callback delay",
                        "blast_radius": "global",
                        "confidence": 0.9,
                        "needs_human_review": False,
                    },
                    {
                        "incident_id": "inc-004",
                        "title": "Risk engine model drift after rollout",
                        "summary": "Decision distribution shifted after model rollout.",
                        "alert_ids": ["a-106"],
                        "primary_service": "risk-engine",
                        "suspected_cause": "Model rollout 40 minutes ago",
                        "blast_radius": "regional",
                        "confidence": 0.86,
                        "needs_human_review": False,
                    },
                    {
                        "incident_id": "inc-005",
                        "title": "Internal LLM spend burst",
                        "summary": "Token spend spike from code review jobs.",
                        "alert_ids": ["a-107"],
                        "primary_service": "internal-dev-agent",
                        "suspected_cause": "Autonomous review job burst",
                        "blast_radius": "single_service",
                        "confidence": 0.78,
                        "needs_human_review": False,
                    },
                    {
                        "incident_id": "inc-006",
                        "title": "US market-data latency isolated",
                        "summary": "Mild latency in us-east-1 without packet loss.",
                        "alert_ids": ["a-108"],
                        "primary_service": "market-data-stream",
                        "suspected_cause": "Isolated regional pressure",
                        "blast_radius": "regional",
                        "confidence": 0.72,
                        "needs_human_review": False,
                    },
                ]
            }
        if "assessing incident severity" in prompt:
            return {
                "assessments": [
                    {
                        "incident_id": "inc-001",
                        "severity": "sev1",
                        "status": "open",
                        "business_impact": "Trading experience degraded; order delays possible",
                        "technical_impact": "Quote path and execution timeouts",
                        "reasoning": "Trade execution depends on market data; multi-service impact",
                    },
                    {
                        "incident_id": "inc-002",
                        "severity": "sev2",
                        "status": "open",
                        "business_impact": "Possible login friction",
                        "technical_impact": "CPU after deploy",
                        "reasoning": "Rollout-correlated but traffic normal",
                    },
                    {
                        "incident_id": "inc-003",
                        "severity": "sev1",
                        "status": "open",
                        "business_impact": "Withdrawals delayed",
                        "technical_impact": "Reconciliation backlog",
                        "reasoning": "Financial correctness at risk if rushed",
                    },
                    {
                        "incident_id": "inc-004",
                        "severity": "sev1",
                        "status": "open",
                        "business_impact": "Risk decisions may be wrong",
                        "technical_impact": "Model drift after rollout",
                        "reasoning": "Compliance exposure; rollback preferred",
                    },
                    {
                        "incident_id": "inc-005",
                        "severity": "sev3",
                        "status": "open",
                        "business_impact": "Internal cost only",
                        "technical_impact": "LLM spend",
                        "reasoning": "No customer trading impact",
                    },
                    {
                        "incident_id": "inc-006",
                        "severity": "sev2",
                        "status": "monitoring",
                        "business_impact": "Limited regional latency",
                        "technical_impact": "Isolated us-east-1",
                        "reasoning": "No packet loss; monitor",
                    },
                ]
            }
        if "first-response actions" in prompt:
            iid = _incident_id_from_prompt(prompt) or "inc-001"
            mapping = {
                "inc-001": [
                    ("shift_traffic", "safe"),
                    ("throttle_non_critical_consumers", "safe"),
                ],
                "inc-002": [("roll_back_recent_deploy", "safe")],
                "inc-003": [
                    ("pause_retries", "needs_approval"),
                    ("scale_workers", "needs_approval"),
                ],
                "inc-004": [("roll_back_model", "safe")],
                "inc-005": [("cap_token_budget", "safe")],
                "inc-006": [("throttle_non_critical_consumers", "safe")],
            }
            acts = mapping.get(iid, [("manual_review", "needs_approval")])
            return {
                "actions": [
                    {
                        "incident_id": iid,
                        "action": a,
                        "why": "Runbook-aligned first response",
                        "expected_effect": "Reduce impact",
                        "risk": "Low to moderate",
                        "safety_level": level,
                    }
                    for a, level in acts
                ]
            }
        if "Draft stakeholder" in prompt:
            iid = _incident_id_from_prompt(prompt) or "inc-unknown"
            service = (
                _field_from_prompt(prompt, "primary_service") or "affected service"
            )
            summary = _field_from_prompt(prompt, "summary") or "Impact under assessment"
            cause = _field_from_prompt(prompt, "suspected_cause") or "cause unconfirmed"
            blast = _field_from_prompt(prompt, "blast_radius") or "unknown"
            sev = _field_from_prompt(prompt, "severity") or "sev2"
            technical = (
                _field_from_prompt(prompt, "technical_impact")
                or "degraded service health"
            )
            business = (
                _field_from_prompt(prompt, "business_impact")
                or "impact under assessment"
            )
            action = _top_action_from_prompt(prompt) or "manual_review"
            blocked = _blocked_actions_from_prompt(prompt)
            blocked_sentence = (
                f"Safety checks hold {', '.join(blocked)} pending approval."
                if blocked
                else f"No {service} action is currently blocked by safety checks."
            )
            return {
                "incident_id": iid,
                "engineering_update": (
                    f"{iid} ({sev}) affects {service} with {blast} blast radius. "
                    f"{_clause(summary)}. "
                    f"Suspected cause is {_clause(cause)}. "
                    f"Technical impact: {_clause(technical)}. "
                    f"Immediate next step is {action.replace('_', ' ')} on {service}. "
                    f"{blocked_sentence}"
                ),
                "executive_update": (
                    f"We are responding to a {sev} issue on {service}. "
                    f"{_clause(business)}. "
                    f"Early indications point to {_clause(cause)}. "
                    f"The team is preparing to {action.replace('_', ' ')}, "
                    "and any action needing approval will wait for sign-off."
                ),
            }
        return {}

    return FakeJsonClient(handler=handler)
