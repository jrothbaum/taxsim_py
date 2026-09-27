"""Benchmark `calculate_taxes` on one state's cases at several batch sizes."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import validate_states  # noqa: E402
from taxsim_py import calculate_taxes  # noqa: E402


def build_cases(state: str, year: int, target_rows: int) -> pl.DataFrame:
    """Build a repeated batch from the existing validation cases."""
    case_builder, _, years = validate_states.STATES[state]
    if year not in years:
        raise ValueError(f"{state} has no validation cases for {year}")

    rows = [row for row in case_builder() if row["year"] == year]
    if not rows:
        raise ValueError(f"{state} produced no validation cases for {year}")

    base = pl.DataFrame(rows, infer_schema_length=None).drop("taxsimid", "description", "oracle_divergent", strict=False)
    repeats = (target_rows + base.height - 1) // base.height
    return pl.concat([base] * repeats).head(target_rows).with_row_index("taxsimid", offset=1)


def run_chunked(cases: pl.DataFrame, chunk_size: int) -> pl.DataFrame:
    """Run `calculate_taxes` over the batch, whole or in chunks."""
    size = cases.height if chunk_size <= 0 else chunk_size
    return pl.concat(calculate_taxes(cases.slice(offset, size)) for offset in range(0, cases.height, size))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("states", nargs="*", default=["MA", "ME", "MI"])
    parser.add_argument("--year", type=int, default=2020)
    parser.add_argument("--rows", type=int, default=5_000)
    parser.add_argument(
        "--chunks",
        type=int,
        nargs="+",
        default=[0, 1_000, 250],
        help="Chunk sizes; 0 runs the full batch at once.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    calculate_taxes(build_cases("MA", 2020, 10))  # warm up imports and parameter loading
    for state in (value.upper() for value in args.states):
        cases = build_cases(state, args.year, args.rows)
        baseline = None
        for chunk_size in args.chunks:
            started = time.perf_counter()
            tax = run_chunked(cases, chunk_size).get_column("siitax")
            elapsed = time.perf_counter() - started
            max_difference = 0.0 if baseline is None else float((tax - baseline).abs().max())
            baseline = tax if baseline is None else baseline
            label = "all" if chunk_size <= 0 else str(chunk_size)
            print(
                f"{state} year={args.year} rows={cases.height} chunk={label} "
                f"seconds={elapsed:.3f} max_tax_difference={max_difference:.12g}"
            )


if __name__ == "__main__":
    main()
