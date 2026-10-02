#!/usr/bin/env python3
"""Compare taxsim_py with independent models on sampled CPS ASEC tax units.

Example:
    uv run --group test scripts/compare_independent.py PATH/TO/cps_2011 \
        --tax-year 2021 --sample-size 100
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taxsim_py import calculate_taxes  # noqa: E402
from taxsim_py.prep import cps_asec_tax_units, read_cps_asec  # noqa: E402
from taxsim_py.validation.independent import (  # noqa: E402
    POLICYENGINE_COMPARABLE_OUTPUTS,
    run_policyengine,
    run_taxcalc,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tax-year", type=int, action="append", dest="tax_years")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--engine",
        choices=("taxcalc", "policyengine", "both"),
        default="both",
    )
    parser.add_argument("--filers-only", action="store_true")
    parser.add_argument(
        "--state",
        type=int,
        action="append",
        dest="states",
        help="restrict the CPS sample to one or more TAXSIM state codes",
    )
    parser.add_argument(
        "--calculation-mode",
        choices=("taxsim", "statutory"),
        default="taxsim",
        help="taxsim_py behavior to compare (default: taxsim)",
    )
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def _compare(
    cases: pl.DataFrame,
    name: str,
    runner,
    outputs: tuple[str, ...],
    calculation_mode: str,
) -> pl.DataFrame:
    started = time.perf_counter()
    ours = calculate_taxes(cases, calculation_mode=calculation_mode).select(
        "taxsimid", *outputs
    )
    ours_seconds = time.perf_counter() - started
    started = time.perf_counter()
    independent = runner(cases).select(
        "taxsimid", *[pl.col(column).alias(f"{column}_{name}") for column in outputs]
    )
    independent_seconds = time.perf_counter() - started
    compared = ours.join(independent, on="taxsimid").with_columns(
        (pl.col(column) - pl.col(f"{column}_{name}")).alias(f"{column}_diff")
        for column in outputs
    )
    print(
        f"{name}: {cases.height:,} rows; taxsim_py {ours_seconds:.3f}s; "
        f"{name} {independent_seconds:.3f}s"
    )
    for column in outputs:
        differences = compared.get_column(f"{column}_diff").abs()
        mismatch = differences > 0.01
        mismatch_count = mismatch.sum()
        mismatch_mean = differences.filter(mismatch).mean() if mismatch_count else 0.0
        print(
            f"  {column:7} >$0.01: {mismatch_count:>5,} "
            f"({mismatch_count / len(differences):.2%}); "
            f"mismatch mean: ${mismatch_mean:,.2f}; "
            f"max: ${differences.max():,.2f}"
        )
    return cases.join(compared, on="taxsimid", how="left").with_columns(
        pl.lit(name).alias("independent_model"),
        pl.lit(calculation_mode).alias("calculation_mode"),
    )


def main() -> None:
    args = parse_args()
    files = read_cps_asec(args.directory)
    units, _ = cps_asec_tax_units(
        files["person"],
        files["hhld"],
        include_nonfilers=not args.filers_only,
    )
    if args.states:
        units = units.filter(pl.col("state").is_in(args.states))
        if units.is_empty():
            raise ValueError(f"No CPS units found for TAXSIM states {args.states}")
    if args.sample_size <= 0:
        raise ValueError("--sample-size must be positive")
    if units.height > args.sample_size:
        units = units.sample(n=args.sample_size, seed=args.seed).sort("taxsimid")

    requested = []
    if args.engine in ("taxcalc", "both"):
        requested.append(("taxcalc", run_taxcalc, ("fiitax", "fica"), 2013))
    if args.engine in ("policyengine", "both"):
        requested.append(
            (
                "policyengine",
                run_policyengine,
                POLICYENGINE_COMPARABLE_OUTPUTS,
                2021,
            )
        )

    source_year = int(units.get_column("year")[0])
    years = args.tax_years or [max(source_year, minimum) for *_, minimum in requested]
    results = []
    for name, runner, outputs, minimum_year in requested:
        for year in years:
            if year < minimum_year:
                print(f"Skipping {name} for {year}: supported from {minimum_year}")
                continue
            print(f"\n{name}, tax year {year}")
            cases = units.with_columns(pl.lit(year, dtype=pl.Int64).alias("year"))
            result = _compare(
                cases, name, runner, outputs, args.calculation_mode
            ).with_columns(
                pl.lit(year).alias("year")
            )
            results.append(result)

    if args.out and results:
        pl.concat(results, how="diagonal_relaxed").write_parquet(args.out)
        print(f"\nWrote comparison rows to {args.out}")


if __name__ == "__main__":
    main()
