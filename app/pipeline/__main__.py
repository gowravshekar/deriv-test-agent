"""CLI entry: uv run python -m app.pipeline --pair public."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from app.pipeline.run import default_fake_client, run_pipeline


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Incident command pipeline")
    parser.add_argument(
        "--pair",
        default="public",
        help="Pair folder under pipeline_files/ (also used as session_id)",
    )
    parser.add_argument(
        "--fake-llm",
        action="store_true",
        help="Use deterministic fake LLM (for tests / offline)",
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Auto-accept all reviews (non-interactive; for tests only)",
    )
    parser.add_argument(
        "--until",
        choices=["stakeholder"],
        default=None,
        help="Stop after stakeholder drafting (skip operator review and redecision)",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Regenerate first-pass artifacts even if they already exist",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Load existing first-pass artifacts (also applies with --skip-review)",
    )
    args = parser.parse_args(argv)

    client = default_fake_client() if args.fake_llm else None
    try:
        path = run_pipeline(
            args.pair,
            client=client,
            skip_review=args.skip_review,
            until=args.until,
            resume=args.resume,
            rerun=args.rerun,
        )
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    if args.until == "stakeholder":
        print(f"Stopped after stakeholder drafting for pair={args.pair}")
        print(f"Artifacts written to {path}")
        print("Re-run without --until to open operator review.")
        return 0
    print(f"Pipeline complete for pair={args.pair} session_id={args.pair}")
    print(f"Artifacts written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
