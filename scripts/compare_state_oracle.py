#!/usr/bin/env python3
"""Compare TAXSIM-mode state results with the oracle on seeded random households.

Prints, per state and year, how many of the sampled households differ in `siitax`
(plus the federal columns, to separate federal from state differences).

    uv run python scripts/compare_state_oracle.py --years 2022 2023 --per-state 20
    uv run python scripts/compare_state_oracle.py --states 5 6 --years 2024 --show 10
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("TAXSIM_PY_ENGINE", "python")

import argparse
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from oracle import run_oracle  # noqa: E402
from taxsim_py import calculate_taxes  # noqa: E402

TOLERANCE = 0.02
INPUTS = ("year", "state", "mstat", "page", "sage", "depx", "pwages", "swages", "intrec", "dividends", "ltcg", "proptax", "mortgage", "pensions", "gssi", "rentpaid")


def households(years: list[int], states: list[int], per_state: int, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for year in years:
        for state in states:
            for _ in range(per_state):
                married = int(rng.choice([1, 2, 2, 3]))
                children = int(rng.choice([0, 0, 1, 2, 3]))
                rows.append(
                    dict(
                        year=year, state=state, mstat=married, page=int(rng.integers(25, 75)),
                        sage=int(rng.integers(25, 75)) if married == 2 else 0,
                        depx=children, dep13=children, dep17=children, dep18=children,
                        pwages=float(round(rng.lognormal(10.8, 0.8), -2)),
                        swages=float(round(rng.lognormal(10.5, 0.8), -2)) if married == 2 else 0.0,
                        intrec=float(rng.choice([0, 0, 500, 5000])), dividends=float(rng.choice([0, 0, 0, 3000])),
                        ltcg=float(rng.choice([0, 0, 0, 10000])), proptax=float(rng.choice([0, 0, 6000])),
                        mortgage=float(rng.choice([0, 0, 9000])), pensions=float(rng.choice([0, 0, 0, 20000])),
                        gssi=float(rng.choice([0, 0, 0, 15000])), rentpaid=float(rng.choice([0, 0, 0, 9000])),
                    )
                )
    return pl.DataFrame(rows).with_row_index("taxsimid", offset=1).with_columns(pl.col("taxsimid").cast(pl.Int64))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=[2021, 2022, 2023, 2024, 2025])
    parser.add_argument("--states", type=int, nargs="+", default=list(range(1, 52)))
    parser.add_argument("--per-state", type=int, default=8)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--show", type=int, default=0, help="print this many differing records per state-year")
    args = parser.parse_args()

    cases = households(args.years, args.states, args.per_state, args.seed)
    expected = run_oracle(cases).select("taxsimid", "fiitax", "siitax", "fica", "tfica")
    actual = calculate_taxes(cases, calculation_mode="taxsim", engine="python").select("taxsimid", "fiitax", "siitax", "fica", "tfica")
    joined = cases.join(expected, on="taxsimid").join(actual, on="taxsimid", suffix="_py")
    for column in ("fiitax", "siitax", "fica", "tfica"):
        joined = joined.with_columns(((pl.col(column) - pl.col(f"{column}_py")).abs() > TOLERANCE).alias(f"d_{column}"))
    pl.Config.set_tbl_rows(200)
    by_year = joined.group_by("year").agg(
        pl.col("d_fiitax").sum().alias("fiitax"), pl.col("d_siitax").sum().alias("siitax"),
        pl.col("d_fica").sum().alias("fica"), pl.col("d_tfica").sum().alias("tfica"), pl.len().alias("n"),
    ).sort("year")
    print(by_year)
    table = joined.group_by("state").agg(
        *[pl.col("d_siitax").filter(pl.col("year") == y).sum().alias(str(y)) for y in args.years]
    ).sort("state")
    print(f"State income tax differences out of {args.per_state} households per state-year:")
    print(table)
    if args.show:
        for (state, year), group in joined.filter(pl.col("d_siitax")).group_by(["state", "year"], maintain_order=True):
            print(f"-- state {state} year {year}")
            for row in group.head(args.show).iter_rows(named=True):
                inputs = " ".join(f"{k}={v:g}" for k, v in row.items() if k in INPUTS and v)
                print(f"  {inputs}  oracle {row['siitax']:.2f}  python {row['siitax_py']:.2f}")


if __name__ == "__main__":
    main()
