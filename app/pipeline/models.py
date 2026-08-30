"""Controlled vocabularies and schemas for the incident pipeline."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class PipelineStage(StrEnum):
    INIT = "INIT"
    INPUTS_LOADED = "INPUTS_LOADED"
    PRIOR_FEEDBACK_LOADED = "PRIOR_FEEDBACK_LOADED"
    INCIDENT_GROUPING_COMPLETE = "INCIDENT_GROUPING_COMPLETE"
    SEVERITY_ASSESSMENT_COMPLETE = "SEVERITY_ASSESSMENT_COMPLETE"
    ACTION_PROPOSALS_COMPLETE = "ACTION_PROPOSALS_COMPLETE"
    SAFETY_GUARDRAILS_COMPLETE = "SAFETY_GUARDRAILS_COMPLETE"
    STAKEHOLDER_DRAFTING_COMPLETE = "STAKEHOLDER_DRAFTING_COMPLETE"
    OPERATOR_REVIEW_COLLECTED = "OPERATOR_REVIEW_COLLECTED"
    FEEDBACK_EXAMPLES_BUILT = "FEEDBACK_EXAMPLES_BUILT"
    REDECISION_COMPLETE = "REDECISION_COMPLETE"
    BEFORE_AFTER_COMPARISON_COMPLETE = "BEFORE_AFTER_COMPARISON_COMPLETE"
    ANALYTICS_GENERATED = "ANALYTICS_GENERATED"
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"
    RESULTS_FINALISED = "RESULTS_FINALISED"


IncidentStatus = Literal["open", "monitoring", "mitigated", "closed"]
Severity = Literal["sev0", "sev1", "sev2", "sev3"]
BlastRadius = Literal[
    "single_service", "multi_service", "regional", "global", "unknown"
]
SafetyLevel = Literal["safe", "needs_approval", "forbidden"]
ReviewAction = Literal["accepted", "corrected", "skipped"]

SEVERITIES: set[str] = {"sev0", "sev1", "sev2", "sev3"}
STATUSES: set[str] = {"open", "monitoring", "mitigated", "closed"}
BLAST_RADII: set[str] = {
    "single_service",
    "multi_service",
    "regional",
    "global",
    "unknown",
}
SAFETY_LEVELS: set[str] = {"safe", "needs_approval", "forbidden"}
REVIEW_ACTIONS: set[str] = {"accepted", "corrected", "skipped"}

SAFETY_RANK = {"safe": 0, "needs_approval": 1, "forbidden": 2}


class Alert(BaseModel):
    id: str
    timestamp: str
    service: str
    region: str
    signal_type: str
    summary: str
    details: str
    metric_value: float
    threshold: float


class ServiceMeta(BaseModel):
    service: str
    tier: str
    owner_team: str
    depends_on: list[str] = Field(default_factory=list)
    customer_impact: str
    change_freeze_required: bool = False


class Runbook(BaseModel):
    service: str
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    notes: str = ""


class IncidentGroup(BaseModel):
    incident_id: str
    title: str
    summary: str
    alert_ids: list[str]
    primary_service: str
    suspected_cause: str
    blast_radius: BlastRadius
    confidence: float
    needs_human_review: bool = False

    @field_validator("confidence")
    @classmethod
    def _confidence_bounds(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.confidence < 0.70:
            self.needs_human_review = True


class SeverityAssessment(BaseModel):
    incident_id: str
    severity: Severity
    status: IncidentStatus = "open"
    business_impact: str
    technical_impact: str
    reasoning: str


class ActionProposal(BaseModel):
    incident_id: str
    action: str
    why: str
    expected_effect: str
    risk: str
    safety_level: SafetyLevel = "safe"


class SafetyReview(BaseModel):
    incident_id: str
    action: str
    model_safety_level: SafetyLevel
    final_safety_level: SafetyLevel
    guardrail_reasons: list[str] = Field(default_factory=list)


class StakeholderUpdate(BaseModel):
    incident_id: str
    engineering_update: str
    executive_update: str

    @model_validator(mode="after")
    def _distinct_audiences(self) -> StakeholderUpdate:
        if self.engineering_update.strip() == self.executive_update.strip():
            raise ValueError(
                "engineering_update and executive_update must be different texts"
            )
        return self


class OperatorFeedback(BaseModel):
    incident_id: str
    original_severity: Severity
    corrected_severity: Severity | None = None
    original_action: str
    corrected_action: str | None = None
    action_status: ReviewAction
    severity_status: ReviewAction
    original_status: IncidentStatus | None = None
    corrected_status: IncidentStatus | None = None
    status_status: ReviewAction | None = None
    timestamp: str


class LlmCallLog(BaseModel):
    stage: str
    incident_id: str | None
    timestamp: str
    provider: str
    model: str
    prompt_hash: str
    input_artifacts: list[str]
    output_artifact: str
    few_shot_examples_included: bool = False


class BeforeAfterRow(BaseModel):
    incident_id: str
    original_severity: str
    operator_severity: str | None
    redecided_severity: str
    original_top_action: str
    operator_action: str | None
    redecided_top_action: str
    moved_toward_operator: bool


class AgreementDelta(BaseModel):
    n_labeled: int
    severity_before_matches: int
    severity_after_matches: int
    severity_count_delta: int
    severity_rate_before: float
    severity_rate_after: float
    severity_rate_delta: float
    action_before_matches: int
    action_after_matches: int
    action_count_delta: int
    action_rate_before: float
    action_rate_after: float
    action_rate_delta: float
