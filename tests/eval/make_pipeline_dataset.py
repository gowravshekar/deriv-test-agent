#!/usr/bin/env python3
"""Regenerate the pipeline eval dataset with a fresh grounding context.

`grounding_v1` scores the response against the eval case `context` field and
fails if that field is absent. The correct grounding source here is the
`summarize_pair` tool output, which changes whenever the evaluator swaps the
input fixtures, so the dataset is generated rather than hardcoded.

Run after the pipeline has produced artifacts for the pair:

    uv run python tests/eval/make_pipeline_dataset.py --pair public
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.pipeline.eval_tool import summarize_pair  # noqa: E402

# Each case targets a distinct pipeline contract from the challenge. The
# instructions are checkable from response text alone; grounding and
# hallucination metrics separately compare the response with the generated
# context. Full sentences are required because grounding_v1 does not score a
# bullets-only response reliably.
CASES = [
    (
        "overview",
        "Summarize the final incident-command result. State that the pair is "
        "{pair}, recommendations were not executed, how many incidents formed, "
        "and the incident id, primary service, severity, and top action for each.",
    ),
    (
        "inputs_and_lifecycle",
        "Explain input loading and pipeline lifecycle completion for pair {pair}. "
        "State the alert, service, and runbook counts, list the executed stages "
        "in order, report quarantined input count, and say whether validation and "
        "finalisation completed.",
    ),
    (
        "incident_grouping",
        "Report incident grouping results for pair {pair}. State the alert and incident "
        "counts, then give each incident id, assigned alert ids, primary service, "
        "blast radius, confidence, and human-review flag.",
    ),
    (
        "severity_assessment",
        "Summarize severity assessment for pair {pair}. For every incident state "
        "its severity, status, business impact, technical impact, and reasoning.",
    ),
    (
        "action_proposals",
        "Begin by saying these are first-response action proposals for pair "
        "{pair}. For every "
        "proposal state the incident id, action, model safety level, expected "
        "effect, and risk. Describe recommendations only.",
    ),
    (
        "safety_guardrails",
        "Explain deterministic safety review for pair {pair}. For every reviewed "
        "action state the model safety level, final safety level, and guardrail "
        "reasons. Explicitly say that no action was executed.",
    ),
    (
        "stakeholder_drafting",
        "Summarize stakeholder drafting for pair {pair}. Confirm every incident "
        "has a distinct engineering commander update and executive update, then "
        "describe the affected system, likely impact, next step, and blocked "
        "actions reflected in those drafts.",
    ),
    (
        "operator_review",
        "Begin by saying this is persisted operator review for pair {pair}. "
        "For every incident "
        "state the severity and action review statuses and any corrected values.",
    ),
    (
        "feedback_and_redecision",
        "Report feedback-driven re-decision results for pair {pair}. State how many "
        "feedback records, severity rows, and action rows were used or produced; "
        "confirm whether re-grouping, severity, and action re-decision calls "
        "logged injected few-shot feedback; summarize the re-decided results.",
    ),
    (
        "comparison",
        "Begin by saying this is the before-and-after comparison for pair {pair}. "
        "State original, "
        "operator, and re-decided severity and top action for each incident, "
        "whether each moved toward operator feedback, and all agreement-delta "
        "count and rate values.",
    ),
    (
        "analytics",
        "Summarize analytics for pair {pair}. State incident and alert counts, "
        "average grouping confidence, severity and blast-radius distributions, "
        "human-review count, action safety counts, operator correction count, "
        "agreement delta, and most involved services.",
    ),
    (
        "prior_feedback",
        "Explain prior-feedback-store status for pair {pair}. State whether prior "
        "feedback was loaded, its count, and the recorded session id.",
    ),
    (
        "escalation",
        "Summarize sev0/sev1 escalation bundles for pair {pair}. For every bundle "
        "state incident id, owner team, page flag, suggested channel, and quote "
        "the summary exactly.",
    ),
    (
        "llm_audit",
        "Summarize LLM-call auditability for pair {pair}. State total calls, "
        "counts by stage, whether each incident has an action and drafting call, "
        "whether re-decision calls include feedback, and whether every record "
        "contains the required audit fields.",
    ),
]

PROMPT_PREFIX = (
    "Use summarize_pair with pair={pair} and focus={focus}. Write only full "
    "sentences, not bullets or tables. "
)

DEFAULT_OUTPUT = REPO_ROOT / "tests" / "eval" / "datasets" / "pipeline-dataset.json"


def build_dataset(pair: str) -> dict:
    overview = summarize_pair(pair)
    if "not found" in overview.lower():
        raise SystemExit(
            f"No artifacts for pair={pair}. Run the pipeline first:\n"
            f"  uv run python -m app.pipeline --pair {pair} --fake-llm --skip-review"
        )

    eval_cases = []
    for focus, instruction in CASES:
        context = summarize_pair(pair, focus)
        eval_cases.append(
            {
                "eval_case_id": f"{pair}_{focus}",
                "prompt": {
                    "role": "user",
                    "parts": [
                        {
                            "text": (PROMPT_PREFIX + instruction).format(
                                pair=pair, focus=focus
                            )
                        }
                    ],
                },
                "context": context,
            }
        )
    return {"eval_cases": eval_cases}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default="public")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    dataset = build_dataset(args.pair)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} (pair={args.pair})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
