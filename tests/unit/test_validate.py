"""Artifact validator tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.pipeline.io import PIPELINE_FILES_ROOT
from app.pipeline.validate_artifacts import score_pair, validate_pair


def test_missing_pair_folder(tmp_path: Path) -> None:
    errors = validate_pair("nope", root=tmp_path)
    assert errors
    assert score_pair("nope", root=tmp_path)["score"] == 0.0


def test_public_pair_scores_one_when_artifacts_present() -> None:
    result = score_pair("public")
    assert result["score"] == 1.0
    assert "OK" in result["explanation"]


def test_validate_rejects_identical_stakeholder_updates(tmp_path: Path) -> None:
    shutil.copytree(PIPELINE_FILES_ROOT / "public", tmp_path / "public")
    path = tmp_path / "public" / "stakeholder_updates.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    same = "Same text for both audiences."
    for row in rows:
        row["engineering_update"] = same
        row["executive_update"] = same
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    errors = validate_pair("public", root=tmp_path)
    assert any(
        "engineering_update and executive_update must differ" in e for e in errors
    )


def test_validate_rejects_boilerplate_across_incidents(tmp_path: Path) -> None:
    shutil.copytree(PIPELINE_FILES_ROOT / "public", tmp_path / "public")
    path = tmp_path / "public" / "stakeholder_updates.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        iid = row["incident_id"]
        row["engineering_update"] = (
            f"{iid}: Affected systems are under investigation. Owners are coordinating."
        )
        row["executive_update"] = f"We are managing incident {iid}. Impact is limited."
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    errors = validate_pair("public", root=tmp_path)
    assert any("boilerplate identical to" in e for e in errors)


def test_validate_continues_after_bad_input_row(tmp_path: Path) -> None:
    shutil.copytree(PIPELINE_FILES_ROOT / "public", tmp_path / "public")
    alerts_path = tmp_path / "public" / "alerts.json"
    alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
    alerts.append({"not": "an alert"})
    alerts_path.write_text(json.dumps(alerts, indent=2), encoding="utf-8")
    updates_path = tmp_path / "public" / "stakeholder_updates.json"
    rows = json.loads(updates_path.read_text(encoding="utf-8"))
    same = "Same text for both audiences."
    for row in rows:
        row["engineering_update"] = same
        row["executive_update"] = same
    updates_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    errors = validate_pair("public", root=tmp_path)
    assert any("schema error" in e for e in errors)
    assert any(
        "engineering_update and executive_update must differ" in e for e in errors
    )
    assert not any("Failed loading core artifacts" in e for e in errors)


def test_pair_dir_rejects_path_escape(tmp_path: Path) -> None:
    from app.pipeline.io import pair_dir

    with pytest.raises(ValueError, match="escapes"):
        pair_dir("../secret", root=tmp_path)
    errors = validate_pair("../secret", root=tmp_path)
    assert errors
    assert any("escapes" in e for e in errors)
