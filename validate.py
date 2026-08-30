#!/usr/bin/env python3
"""Validate pipeline_files/<pair> contracts. Optionally run the pipeline first."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from app.pipeline.validate_artifacts import validate_pair


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate incident pipeline artifacts")
    parser.add_argument("--pair", default="public")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run the interactive pipeline first (requires TTY)",
    )
    parser.add_argument(
        "--run-fake",
        action="store_true",
        help="Run pipeline with --fake-llm --skip-review then validate",
    )
    args = parser.parse_args(argv)

    if args.run or args.run_fake:
        cmd = [sys.executable, "-m", "app.pipeline", "--pair", args.pair]
        if args.run_fake:
            cmd.extend(["--fake-llm", "--skip-review"])
        proc = subprocess.run(cmd, check=False)
        if proc.returncode != 0:
            return proc.returncode

    errors = validate_pair(args.pair)
    if errors:
        print(f"Validation FAILED for pair={args.pair}")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"Validation OK for pair={args.pair}")
    print(f"Checked {Path('pipeline_files') / args.pair}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
