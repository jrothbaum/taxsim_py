"""Benchmark state calculation batch sizes and repeated federal calls."""

from __future__ import annotations

import argparse
import importlib
import time
from collections.abc import Callable

import polars as pl

import validate_states
from taxsim_py.engine import federal_state


def build_cases(state: str, year: int, target_rows: int) -> pl.DataFrame:
    """Build a repeated batch from the existing validation cases."""
    case_builder, _, years = validate_states.STATES[state]
    if year not in years:
        raise ValueError(f"{state} has no validation cases for {year}")

    rows = [row for row in case_builder() if row["year"] == year]
    if not rows:
        raise ValueError(f"{state} produced no validation cases for {year}")

    base = pl.DataFrame(rows)
    repeats = (target_rows + base.height - 1) // base.height
    return (
        pl.concat([base] * repeats)
        .head(target_rows)
        .with_row_index("benchmark_id")
    )


def run_partitioned(
    cases: pl.DataFrame,
    year: int,
    state_calculator: Callable,
    chunk_size: int,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Run a state calculator in chunks and count calculator calls."""
    counts = {"state": 0, "federal": 0, "nested_federal": 0}
    state_module = importlib.import_module(state_calculator.__module__)
    original_federal = federal_state.compute_regular_tax
    original_nested = getattr(state_module, "compute_regular_tax", None)

    def counted_federal(*args, **kwargs):
        counts["federal"] += 1
        return original_federal(*args, **kwargs)

    def counted_nested(*args, **kwargs):
        counts["nested_federal"] += 1
        return original_federal(*args, **kwargs)

    def counted_state(*args, **kwargs):
        result = state_calculator(*args, **kwargs)
        counts["state"] += 1
        return result

    federal_state.compute_regular_tax = counted_federal
    if original_nested is not None:
        state_module.compute_regular_tax = counted_nested

    try:
        size = cases.height if chunk_size <= 0 else chunk_size
        parts = []
        for offset in range(0, cases.height, size):
            chunk = cases.slice(offset, size)
            parts.append(
                federal_state.resolve_federal_and_state(
                    chunk, year, counted_state
                )
            )
        return pl.concat(parts), counts
    finally:
        federal_state.compute_regular_tax = original_federal
        if original_nested is not None:
            state_module.compute_regular_tax = original_nested


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
    for state in (value.upper() for value in args.states):
        _, calculator, _ = validate_states.STATES[state]
        cases = build_cases(state, args.year, args.rows)
        baseline = None

        for chunk_size in args.chunks:
            started = time.perf_counter()
            result, counts = run_partitioned(
                cases, args.year, calculator, chunk_size
            )
            elapsed = time.perf_counter() - started
            tax = result.get_column("siitax")

            if baseline is None:
                baseline = tax
                max_difference = 0.0
            else:
                max_difference = float((tax - baseline).abs().max())

            label = "all" if chunk_size <= 0 else str(chunk_size)
            print(
                f"{state} year={args.year} rows={cases.height} chunk={label} "
                f"seconds={elapsed:.3f} state_calls={counts['state']} "
                f"federal_calls={counts['federal']} "
                f"nested_federal_calls={counts['nested_federal']} "
                f"max_tax_difference={max_difference:.12g}"
            )


if __name__ == "__main__":
    main()
