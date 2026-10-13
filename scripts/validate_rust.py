"""Compare the compiled calculator with the Python one, state by state and year by year.

For every state and law year the compiled calculator accepts (statutory mode), run both
engines on the state's validation cases for that year plus seeded random households (with
marginal rates) and require the same results. Prints the combinations that match, the ones
that do not (exit status 1) and the ones the compiled calculator has no law for. Nothing is
saved: `calculate_taxes` uses the compiled calculator whenever it is asked to.

Run after building the extension (`uv sync`, or `maturin develop --release`):

    .venv/bin/python scripts/validate_rust.py [--states WV ...] [--years 2022 ...] [--rows 3000]
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from engine_compare import ABS_TOL, RATE_UNIT, assert_engines_match  # noqa: E402
from random_households import random_households  # noqa: E402
from taxsim_py import calculate_taxes  # noqa: E402
from taxsim_py.api import LAST_SUPPORTED_YEAR  # noqa: E402
from taxsim_py.calculators.states import STATE_CALCULATOR_PATHS  # noqa: E402
from taxsim_py.engine import rust_backend  # noqa: E402

FIRST_YEAR = 1960
MARGINAL_INPUT = 11  # primary and spouse wages together
MODE = "statutory"


def state_cases(module: str, state: int, year: int) -> pl.DataFrame | None:
    """The state's validation cases for `year`, if it has any."""
    name = module.rstrip("_")
    try:
        cases = getattr(importlib.import_module(f"{name}_cases"), f"build_{name}_test_cases")()
    except (ImportError, AttributeError):
        return None
    frame = pl.DataFrame(cases)
    if "description" in frame.columns:
        frame = frame.drop("description")
    frame = frame.filter(pl.col("year") == year)
    return frame.with_columns(state=pl.lit(state)) if frame.height else None


def test_frame(module: str, state: int, year: int, rows: int) -> pl.DataFrame:
    parts = [random_households(state, year, rows)]
    cases = state_cases(module, state, year)
    if cases is not None:
        parts.append(cases)
    frame = pl.concat(parts, how="diagonal_relaxed")
    return frame.with_row_index("taxsim_id", offset=1)


def run(engine: str, frame: pl.DataFrame) -> pl.DataFrame:
    os.environ["TAXSIM_PY_ENGINE"] = engine
    return calculate_taxes(frame, calculation_mode=MODE, mtr=MARGINAL_INPUT)


def describe_mismatch(expected: pl.DataFrame, found: pl.DataFrame) -> str:
    worst = []
    for column in expected.columns:
        if expected[column].dtype.is_float():
            difference = (expected[column] - found[column]).abs().fill_null(0)
            if difference.max() > (RATE_UNIT if column in ("frate", "srate") else ABS_TOL):
                worst.append(f"{column} ({int((difference > ABS_TOL).sum())} rows, max {difference.max():.6g})")
    return ", ".join(worst[:4]) or "frames differ"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--states", nargs="*", help="state modules to compare, e.g. wv (default: every state)")
    parser.add_argument("--years", nargs="*", type=int, help="law years (default: all coded years)")
    parser.add_argument("--rows", type=int, default=3000, help="random households per state and year")
    args = parser.parse_args()

    if rust_backend._plugin_path() is None:
        print("the compiled calculator is not installed; build it with `uv sync` or `maturin develop --release`")
        return 2
    selected = {code: module for code, (module, _) in STATE_CALCULATOR_PATHS.items()}
    if args.states:
        wanted = {name.lower().rstrip("_") for name in args.states}
        selected = {code: module for code, module in selected.items() if module.rstrip("_") in wanted}
    years = args.years or list(range(FIRST_YEAR, LAST_SUPPORTED_YEAR + 1))

    matched: dict[int, list[int]] = defaultdict(list)
    failed: list[tuple[int, int, str]] = []
    skipped: dict[str, int] = defaultdict(int)
    for code, module in selected.items():
        for year in years:
            try:
                run("rust", random_households(code, year, 1))  # cheap probe
                frame = test_frame(module, code, year, args.rows)
                found = run("rust", frame)
            except Exception as error:  # the compiled calculator has no law for this combination
                skipped[_reason(error)] += 1
                continue
            try:
                expected = run("python", frame)
            except Exception as error:  # Python has no law for this state and year either
                skipped[f"Python cannot calculate it: {_reason(error)}"] += 1
                continue
            try:
                assert_engines_match(expected, found)
            except AssertionError:
                failed.append((code, year, describe_mismatch(expected, found)))
                continue
            matched[code].append(year)

    print(f"\nmatched: {sum(len(y) for y in matched.values())} state-year combinations")
    for code, matched_years in sorted(matched.items()):
        print(f"  {code:2d} {selected[code]:14s} {_ranges(matched_years)}")
    if failed:
        print(f"\nDID NOT MATCH ({len(failed)}):")
        for code, year, detail in failed:
            print(f"  {code:2d} {selected[code]:14s} {year}: {detail}")
    if skipped:
        print("\nnot compared (state-years: reason):")
        for reason, count in sorted(skipped.items(), key=lambda item: -item[1])[:8]:
            print(f"  {count:5d}: {reason}")
    return 1 if failed else 0


def _reason(error: Exception) -> str:
    """The compiled calculator's own message from a (possibly multi-line) Polars error."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    for line in lines:
        if "compiled calculator" in line or "not supported" in line or "supports" in line:
            return re.sub(r"\b(19|20)\d\d\b", "<year>", line)[:110]
    return (lines[0] if lines else type(error).__name__)[:110]


def _ranges(values: list[int]) -> str:
    out, start = [], None
    for index, value in enumerate(values):
        if start is None:
            start = previous = value
        elif value == previous + 1:
            previous = value
        else:
            out.append(f"{start}-{previous}" if previous != start else str(start))
            start = previous = value
        if index == len(values) - 1:
            out.append(f"{start}-{previous}" if previous != start else str(start))
    return ", ".join(out) or "none"


if __name__ == "__main__":
    raise SystemExit(main())
