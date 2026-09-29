#!/usr/bin/env python3
"""Compare versioned TAXSIM executables on the same CPS ASEC tax units."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from oracle import run_oracle  # noqa: E402
from taxsim_py.prep import cps_asec_tax_units, read_cps_asec  # noqa: E402

VERSION_PATTERN = re.compile(r"Version of\s+(\d+)")
TOLERANCE = 0.015


def parse_args() -> argparse.Namespace:
    policyengine_executable = (
        ROOT
        / ".venv/share/policyengine_taxsim/taxsimtest/taxsimtest-linux.exe"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, help="Cached CPS ASEC directory")
    parser.add_argument(
        "--executable",
        action="append",
        metavar="LABEL=PATH",
        help="TAXSIM build to compare; repeat at least twice",
    )
    parser.add_argument("--tax-year", type=int, default=2021)
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, help="Write a JSON audit summary")
    parser.set_defaults(
        default_executables=[
            f"frozen-2024={ROOT / 'taxsim2024.exe'}",
            f"policyengine={policyengine_executable}",
        ]
    )
    return parser.parse_args()


def parse_executables(specifications: list[str]) -> dict[str, Path]:
    executables: dict[str, Path] = {}
    for specification in specifications:
        if "=" not in specification:
            raise ValueError(f"Expected LABEL=PATH, got {specification!r}")
        label, raw_path = specification.split("=", 1)
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        executables[label] = path
    if len(executables) < 2:
        raise ValueError("Specify at least two distinct executable labels")
    return executables


def executable_metadata(path: Path) -> dict[str, str | int]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    probe = subprocess.run(
        [str(path)], input="", capture_output=True, text=True, timeout=10
    )
    diagnostic = f"{probe.stdout}\n{probe.stderr}"
    match = VERSION_PATTERN.search(diagnostic)
    return {
        "path": str(path),
        "build": int(match.group(1)) if match else "unknown",
        "sha256": digest,
        "size_bytes": path.stat().st_size,
    }


def compare_frames(
    baseline: pl.DataFrame, candidate: pl.DataFrame
) -> dict[str, dict[str, float | int]]:
    common_outputs = sorted(
        (set(baseline.columns) & set(candidate.columns)) - {"taxsimid"}
    )
    compared = baseline.select("taxsimid", *common_outputs).join(
        candidate.select(
            "taxsimid",
            *[pl.col(column).alias(f"{column}_candidate") for column in common_outputs],
        ),
        on="taxsimid",
    )
    summary: dict[str, dict[str, float | int]] = {}
    for column in common_outputs:
        absolute_difference = (
            compared.get_column(column)
            - compared.get_column(f"{column}_candidate")
        ).abs()
        mismatch_count = int((absolute_difference > TOLERANCE).sum())
        if mismatch_count:
            summary[column] = {
                "mismatch_count": mismatch_count,
                "mismatch_rate": mismatch_count / compared.height,
                "maximum_absolute_difference": float(absolute_difference.max()),
            }
    return summary


def main() -> None:
    args = parse_args()
    executable_specs = args.executable or args.default_executables
    executables = parse_executables(executable_specs)
    if args.sample_size <= 0:
        raise ValueError("--sample-size must be positive")

    files = read_cps_asec(args.directory)
    cases, _ = cps_asec_tax_units(
        files["person"], files["hhld"], include_nonfilers=True
    )
    cases = cases.with_columns(pl.lit(args.tax_year).alias("year"))
    if cases.height > args.sample_size:
        cases = cases.sample(args.sample_size, seed=args.seed).sort("taxsimid")

    metadata = {label: executable_metadata(path) for label, path in executables.items()}
    outputs: dict[str, pl.DataFrame] = {}
    elapsed_seconds: dict[str, float] = {}
    print(f"{cases.height:,} CPS tax units under {args.tax_year} law")
    for label, path in executables.items():
        started = time.perf_counter()
        outputs[label] = run_oracle(cases, idtl=2, executable=path)
        elapsed_seconds[label] = time.perf_counter() - started
        print(
            f"  {label}: build {metadata[label]['build']}, "
            f"{elapsed_seconds[label]:.2f}s, {len(outputs[label].columns) - 1} outputs"
        )

    baseline_label = next(iter(executables))
    comparisons = {}
    for candidate_label in list(executables)[1:]:
        differences = compare_frames(
            outputs[baseline_label], outputs[candidate_label]
        )
        comparison_name = f"{baseline_label}_vs_{candidate_label}"
        comparisons[comparison_name] = differences
        print(f"\n{comparison_name}: {len(differences)} differing outputs")
        for column, result in sorted(
            differences.items(),
            key=lambda item: item[1]["mismatch_count"],
            reverse=True,
        ):
            print(
                f"  {column:12} {result['mismatch_count']:>6,} "
                f"({result['mismatch_rate']:.2%}), "
                f"max ${result['maximum_absolute_difference']:,.2f}"
            )

    report = {
        "tax_year": args.tax_year,
        "sample_size": cases.height,
        "seed": args.seed,
        "executables": metadata,
        "elapsed_seconds": elapsed_seconds,
        "comparisons": comparisons,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
