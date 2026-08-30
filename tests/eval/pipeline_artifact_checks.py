"""CodeExecutionMetric for CI: share artifact checks with validate.py.

The grader compiles this source in-process, so `__file__` is unavailable and
the project root is not guaranteed to be on `sys.path`. Resolve the root by
walking up from the working directory looking for `pipeline_files/`.

The grader environment also has no `google.adk`, so the `app` and `app.pipeline`
packages are registered as bare namespace modules. That skips their `__init__`
files, which import the ADK agent, while the validator chain itself needs only
pydantic and the standard library.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any


def _repo_root() -> Path | None:
    candidates = [Path.cwd(), *Path.cwd().parents]
    for candidate in candidates:
        if (candidate / "pipeline_files").is_dir() and (
            candidate / "app" / "pipeline"
        ).is_dir():
            return candidate
    return None


def evaluate(instance: dict[str, Any]) -> dict[str, Any]:
    """Score pipeline_files/<pair> contracts.

    Pair defaults to public; override with a `pair` field on the eval case.
    """
    root = _repo_root()
    if root is None:
        return {
            "score": 0.0,
            "explanation": f"Could not locate project root from cwd={Path.cwd()}",
        }

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    for name, path in (
        ("app", root / "app"),
        ("app.pipeline", root / "app" / "pipeline"),
    ):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module

    try:
        from app.pipeline.validate_artifacts import score_pair
    except Exception as exc:  # pragma: no cover - import guard
        return {"score": 0.0, "explanation": f"Could not import validators: {exc}"}

    pair = instance.get("pair") or "public"
    return score_pair(pair, root=root / "pipeline_files")
