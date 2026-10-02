#!/usr/bin/env python3
"""Run the repository's beta validation gates in one command.

The normal path runs the API/unit suite, vectorization check, and the complete
federal and state TAXSIM parity matrices. A cached CPS directory can be added
for a deterministic independent-model smoke comparison.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cps-directory",
        type=Path,
        help="Optional cached CPS ASEC directory for an independent smoke run",
    )
    parser.add_argument("--cps-tax-year", type=int, default=2021)
    parser.add_argument("--cps-sample-size", type=int, default=1_000)
    parser.add_argument(
        "--skip-states",
        action="store_true",
        help="Run quick gates only; useful while editing a calculator",
    )
    return parser.parse_args()


def run_gate(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===", flush=True)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise SystemExit(f"{label} failed with exit code {completed.returncode}")


def main() -> None:
    args = parse_args()
    python = sys.executable
    run_gate("ruff", [python, "-m", "ruff", "check", "."])
    run_gate("pytest", [python, "-m", "pytest", "-q"])
    run_gate("vectorization", [python, "scripts/check_vectorization.py"])
    run_gate("federal TAXSIM parity", [python, "scripts/validate_federal.py"])
    if not args.skip_states:
        run_gate("state TAXSIM parity", [python, "scripts/validate_states.py"])
    if args.cps_directory is not None:
        if not args.cps_directory.is_dir():
            raise SystemExit(f"CPS directory does not exist: {args.cps_directory}")
        run_gate(
            "independent CPS smoke",
            [
                python,
                "scripts/compare_independent.py",
                str(args.cps_directory),
                "--tax-year",
                str(args.cps_tax_year),
                "--sample-size",
                str(args.cps_sample_size),
                "--engine",
                "both",
            ],
        )
    print("\nAll requested validation gates passed.")


if __name__ == "__main__":
    main()
