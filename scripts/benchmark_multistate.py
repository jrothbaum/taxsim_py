"""Benchmark mixed-year, mixed-state tax calculation against TAXSIM Fortran."""

from __future__ import annotations

import argparse
import importlib
import io
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from taxsim_py import calculate_taxes  # noqa: E402

TAXSIM_EXE = ROOT / "taxsim2024.exe"
INPUT_COLUMNS = [
    "taxsimid",
    "year",
    "state",
    "mstat",
    "depx",
    "dep17",
    "dep18",
    "dep6",
    "pwages",
    "swages",
    "proptax",
    "otheritem",
    "mortgage",
    "dep13",
    "childcare",
    "intrec",
    "psemp",
    "ssemp",
    "dividends",
    "stcg",
    "ltcg",
    "ui",
    "pui",
    "sui",
]


def _case_builder(state: str) -> Callable[[], list[dict]]:
    module = importlib.import_module(f"{state.lower()}_cases")
    return getattr(module, f"build_{state.lower()}_test_cases")


def build_cases(states: list[str], years: list[int], rows: int) -> pl.DataFrame:
    """Build a deterministic batch containing every requested state/year pair."""
    representatives: list[pl.DataFrame] = []
    pools: list[pl.DataFrame] = []
    for state in states:
        cases = pl.DataFrame(_case_builder(state)())
        for year in years:
            partition = cases.filter(pl.col("year") == year)
            if partition.is_empty():
                raise ValueError(f"{state} has no validation cases for {year}")
            representatives.append(partition.head(1))
            pools.append(partition)

    minimum_rows = len(representatives)
    if rows < minimum_rows:
        raise ValueError(
            f"rows must be at least {minimum_rows} to include all state/year partitions"
        )

    required = pl.concat(representatives, how="diagonal_relaxed")
    pool = pl.concat(pools, how="diagonal_relaxed").sample(fraction=1.0, shuffle=True, seed=20260924)
    remaining = rows - required.height
    repeats = (remaining + pool.height - 1) // pool.height
    frame = pl.concat([required, *([pool] * repeats)], how="diagonal_relaxed").head(rows)
    return frame.drop("taxsimid", strict=False).with_row_index("taxsimid", offset=1)


def encode_fortran_input(cases: pl.DataFrame) -> str:
    lines = [" ".join(INPUT_COLUMNS)]
    lines.extend(
        " ".join(str(value) for value in row)
        for row in cases.select(INPUT_COLUMNS).iter_rows()
    )
    return "\n".join(lines) + "\n"


def run_fortran(payload: str) -> str:
    result = subprocess.run(
        [str(TAXSIM_EXE)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"taxsim.exe failed: {result.stderr}\n{result.stdout[-2000:]}")
    return result.stdout


def parse_fortran_output(output: str) -> pl.DataFrame:
    return pl.read_csv(io.StringIO(output))


def run_fortran_dataframe(cases: pl.DataFrame) -> pl.DataFrame:
    return parse_fortran_output(run_fortran(encode_fortran_input(cases)))


def median_seconds(function: Callable[[], object], repeats: int) -> tuple[float, object]:
    timings: list[float] = []
    value: object = None
    for _ in range(repeats):
        started = time.perf_counter()
        value = function()
        timings.append(time.perf_counter() - started)
    return statistics.median(timings), value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--states",
        nargs="+",
        default=["AL", "CA", "CT", "MA", "MI", "MN", "NY", "OH"],
    )
    parser.add_argument("--years", type=int, nargs="+", default=[1990, 2000, 2010, 2020])
    parser.add_argument(
        "--rows",
        type=int,
        nargs="+",
        default=[100, 1_000, 10_000, 100_000, 250_000],
    )
    parser.add_argument("--repeats", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    states = [state.upper() for state in args.states]
    partitions = len(states) * len(args.years)
    smallest = min(args.rows)

    warmup = build_cases(states, args.years, smallest)
    calculate_taxes(warmup)
    run_fortran(encode_fortran_input(warmup))

    print(
        "rows,partitions,python_api_s,fortran_exec_s,fortran_dataframe_s,"
        "python_rows_per_s,fortran_exec_rows_per_s,fortran_dataframe_rows_per_s"
    )
    for row_count in args.rows:
        cases = build_cases(states, args.years, row_count)
        payload = encode_fortran_input(cases)

        python_seconds, python_result = median_seconds(
            lambda: calculate_taxes(cases), args.repeats
        )
        fortran_seconds, fortran_output = median_seconds(
            lambda: run_fortran(payload), args.repeats
        )
        dataframe_seconds, fortran_frame = median_seconds(
            lambda: run_fortran_dataframe(cases), args.repeats
        )

        if not isinstance(python_result, pl.DataFrame) or python_result.height != row_count:
            raise RuntimeError("Python API returned the wrong number of rows")
        output_rows = len([line for line in str(fortran_output).splitlines()[1:] if line.strip()])
        if output_rows != row_count:
            raise RuntimeError("Fortran returned the wrong number of rows")
        if not isinstance(fortran_frame, pl.DataFrame) or fortran_frame.height != row_count:
            raise RuntimeError("Parsed Fortran output has the wrong number of rows")

        print(
            f"{row_count},{partitions},{python_seconds:.6f},{fortran_seconds:.6f},"
            f"{dataframe_seconds:.6f},{row_count / python_seconds:.0f},"
            f"{row_count / fortran_seconds:.0f},{row_count / dataframe_seconds:.0f}"
        )


if __name__ == "__main__":
    main()
