"""Resume / --until stakeholder / stage-status tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline.io import FIRST_PASS_ARTIFACTS, PIPELINE_FILES_ROOT
from app.pipeline.run import default_fake_client, run_pipeline


def _copy_inputs(dest: Path) -> None:
    src = PIPELINE_FILES_ROOT / "public"
    dest.mkdir()
    for name in ("alerts.json", "services.json", "runbooks.json"):
        (dest / name).write_text(
            (src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )


def _copy_first_pass(dest: Path) -> None:
    src = PIPELINE_FILES_ROOT / "public"
    for name in FIRST_PASS_ARTIFACTS:
        (dest / name).write_text(
            (src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )


def _patch_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.pipeline.io as io_mod

    monkeypatch.setattr(io_mod, "PIPELINE_FILES_ROOT", tmp_path)


def _llm_stages(path: Path) -> list[str]:
    log = path / "llm_calls.jsonl"
    if not log.is_file():
        return []
    return [
        json.loads(line)["stage"]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_until_stakeholder_stops_before_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "public"
    _copy_inputs(dest)
    _patch_roots(monkeypatch, tmp_path)

    path = run_pipeline(
        "public",
        client=default_fake_client(),
        until="stakeholder",
    )
    assert (path / "stakeholder_updates.json").is_file()
    assert not (path / "operator_feedback.jsonl").is_file()
    assert not (path / "severity_redecision.json").is_file()
    stages = set(_llm_stages(path))
    assert "incident_grouping" in stages
    assert "stakeholder_drafting" in stages
    assert "severity_redecision" not in stages
    out = capsys.readouterr().out
    assert "RUNNING" in out
    assert "Stopped after stakeholder drafting" in out


def test_resume_skips_first_pass_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "public"
    _copy_inputs(dest)
    _copy_first_pass(dest)
    _patch_roots(monkeypatch, tmp_path)

    inner = default_fake_client()
    answers = iter(["y", "y", "y"] * 20)

    path = run_pipeline(
        "public",
        client=inner,
        prompt_fn=lambda _msg: next(answers),
    )
    stages = _llm_stages(path)
    first_pass = {
        "incident_grouping",
        "severity_assessment",
        "action_proposal",
        "stakeholder_drafting",
    }
    assert first_pass.isdisjoint(stages)
    assert "incident_grouping_redecision" in stages
    assert "severity_redecision" in stages
    assert "action_redecision" in stages
    assert (path / "operator_feedback.jsonl").is_file()
    out = capsys.readouterr().out
    assert "STORED" in out
    assert "Resuming operator review" in out
    assert "incident_grouping_redecision" in out
    assert "RUNNING" in out


def test_skip_review_reruns_first_pass_even_with_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "public"
    _copy_inputs(dest)
    _copy_first_pass(dest)
    _patch_roots(monkeypatch, tmp_path)

    path = run_pipeline(
        "public",
        client=default_fake_client(),
        skip_review=True,
    )
    stages = set(_llm_stages(path))
    assert "incident_grouping" in stages
    assert "stakeholder_drafting" in stages
    assert "severity_redecision" in stages


def test_skip_review_resume_loads_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "public"
    _copy_inputs(dest)
    _copy_first_pass(dest)
    _patch_roots(monkeypatch, tmp_path)

    inner = default_fake_client()
    path = run_pipeline(
        "public",
        client=inner,
        skip_review=True,
        resume=True,
    )
    stages = set(_llm_stages(path))
    assert "incident_grouping" not in stages
    assert "stakeholder_drafting" not in stages
    assert "severity_redecision" in stages
    assert (path / "incident_command_output.json").is_file()


def test_operator_feedback_is_truncated_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "public"
    _copy_inputs(dest)
    _patch_roots(monkeypatch, tmp_path)
    stale = dest / "operator_feedback.jsonl"
    stale.write_text(
        '{"incident_id":"stale"}\n{"incident_id":"stale-2"}\n',
        encoding="utf-8",
    )

    path = run_pipeline(
        "public",
        client=default_fake_client(),
        skip_review=True,
    )
    rows = [
        line
        for line in (path / "operator_feedback.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert rows
    assert all('"incident_id": "stale"' not in line for line in rows)


def test_until_stakeholder_uses_stored_without_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "public"
    _copy_inputs(dest)
    _copy_first_pass(dest)
    _patch_roots(monkeypatch, tmp_path)

    client = default_fake_client()
    path = run_pipeline(
        "public",
        client=client,
        until="stakeholder",
    )
    assert not (path / "llm_calls.jsonl").is_file() or _llm_stages(path) == []
    assert client.calls == []
    assert not (path / "operator_feedback.jsonl").is_file()
    out = capsys.readouterr().out
    assert "STORED" in out
