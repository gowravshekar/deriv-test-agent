"""Pair-folder I/O. Pair name == ADK session_id."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.pipeline.models import (
    ActionProposal,
    Alert,
    IncidentGroup,
    Runbook,
    SafetyReview,
    ServiceMeta,
    SeverityAssessment,
    StakeholderUpdate,
)

T = TypeVar("T", bound=BaseModel)

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_FILES_ROOT = REPO_ROOT / "pipeline_files"

REQUIRED_INPUTS = ("alerts.json", "services.json", "runbooks.json")

OUTPUT_FILES = (
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
)


def pair_dir(pair: str, *, root: Path | None = None) -> Path:
    files_root = (root or PIPELINE_FILES_ROOT).resolve()
    path = (files_root / pair).resolve()
    if path != files_root and files_root not in path.parents:
        raise ValueError(f"Pair {pair!r} escapes {files_root}")
    return path


def ensure_pair_inputs(pair: str) -> Path:
    path = pair_dir(pair)
    if not path.is_dir():
        raise FileNotFoundError(f"Pair folder not found: {path}")
    missing = [name for name in REQUIRED_INPUTS if not (path / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing input files in {path}: {', '.join(missing)}")
    return path


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Corrupt line: skip, keep going
            continue
    return rows


def _parse_list(
    raw: Any,
    model: type[T],
    *,
    label: str,
) -> tuple[list[T], list[dict[str, Any]]]:
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be a JSON array")
    good: list[T] = []
    bad: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        try:
            good.append(model.model_validate(item))
        except ValidationError as exc:
            bad.append(
                {
                    "index": idx,
                    "item": item,
                    "error": str(exc),
                    "needs_human_review": True,
                }
            )
    return good, bad


FIRST_PASS_ARTIFACTS = (
    "incident_groups.json",
    "severity_assessments.json",
    "action_proposals.json",
    "safety_review.json",
    "stakeholder_updates.json",
)


def _parse_list_complete(
    path: Path,
    model: type[T],
    *,
    label: str,
) -> list[T] | None:
    if not path.is_file():
        return None
    try:
        raw = read_json(path)
    except ValueError:
        return None
    try:
        items, bad = _parse_list(raw, model, label=label)
    except ValueError:
        return None
    if bad or not items:
        return None
    return items


def load_first_pass_artifacts(
    pair_path: Path,
) -> (
    tuple[
        list[IncidentGroup],
        list[SeverityAssessment],
        list[ActionProposal],
        list[SafetyReview],
        list[StakeholderUpdate],
    ]
    | None
):
    """Return parsed first-pass outputs, or None if any artifact is missing/invalid."""
    if any(not (pair_path / name).is_file() for name in FIRST_PASS_ARTIFACTS):
        return None
    incidents = _parse_list_complete(
        pair_path / "incident_groups.json",
        IncidentGroup,
        label="incident_groups.json",
    )
    severities = _parse_list_complete(
        pair_path / "severity_assessments.json",
        SeverityAssessment,
        label="severity_assessments.json",
    )
    proposals = _parse_list_complete(
        pair_path / "action_proposals.json",
        ActionProposal,
        label="action_proposals.json",
    )
    safety = _parse_list_complete(
        pair_path / "safety_review.json",
        SafetyReview,
        label="safety_review.json",
    )
    updates = _parse_list_complete(
        pair_path / "stakeholder_updates.json",
        StakeholderUpdate,
        label="stakeholder_updates.json",
    )
    if None in (incidents, severities, proposals, safety, updates):
        return None
    return incidents, severities, proposals, safety, updates  # type: ignore[return-value]


def load_inputs(
    pair: str,
) -> tuple[list[Alert], list[ServiceMeta], list[Runbook], list[dict[str, Any]], Path]:
    path = ensure_pair_inputs(pair)
    alerts, bad_alerts = _parse_list(
        read_json(path / "alerts.json"), Alert, label="alerts.json"
    )
    services, bad_services = _parse_list(
        read_json(path / "services.json"), ServiceMeta, label="services.json"
    )
    runbooks, bad_runbooks = _parse_list(
        read_json(path / "runbooks.json"), Runbook, label="runbooks.json"
    )
    quarantined = (
        [{"source": "alerts", **row} for row in bad_alerts]
        + [{"source": "services", **row} for row in bad_services]
        + [{"source": "runbooks", **row} for row in bad_runbooks]
    )
    if not alerts:
        raise ValueError(f"No valid alerts loaded from {path / 'alerts.json'}")
    return alerts, services, runbooks, quarantined, path


def model_dump_list(items: list[BaseModel]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]
