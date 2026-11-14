"""Run every CPS ASEC tax unit of a state through the compiled TAXSIM and taxsim mode, for each state and law year.

usage: uv run python scripts/cps_full_state_check.py PATH/TO/cps_2011 --years 2022,2023,2024,2025,2026 [--states 1,5] [--out DIR]

Unlike scripts/find_cps_gaps.py there is no sampling: all the units of a state. Writes DIR/summary.csv (one row per state and
year: units, how many differ in state tax and in federal tax by more than $1 and by more than 2 cents, the largest gap) and
DIR/gaps_<year>.parquet (the households that differ by more than $1 in state tax, largest first). Resumes: a state-year already
in summary.csv is skipped. Run it under `ulimit -v` and `POLARS_MAX_THREADS=4`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_comparison_report import (  # noqa: E402
    OPTIONAL_CHILD_COUNT_INPUTS, STATE_NAME, TAXSIM_INPUTS, calculate_taxes, cps_asec_tax_units, for_year, read_cps_asec, run_oracle,
)

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("--years", required=True)
parser.add_argument("--states", default="")
parser.add_argument("--out", type=Path, default=ROOT / "docs" / "data" / "cps_full_state_check")
args = parser.parse_args()
os.environ.setdefault("POLARS_MAX_THREADS", "4")

files = read_cps_asec(args.directory)
units, _ = cps_asec_tax_units(files["person"], files["hhld"], include_nonfilers=False)
base = int(units["year"][0])
units = units.drop("year").with_columns(year=pl.lit(base, dtype=pl.Int64))
keep = [c for c in units.columns if c in TAXSIM_INPUTS or c == "taxsimid"]
states = [int(s) for s in args.states.split(",")] if args.states else sorted(set(units["state"].to_list()) - {0})
years = [int(y) for y in args.years.split(",")]
args.out.mkdir(parents=True, exist_ok=True)
summary_path = args.out / "summary.csv"
rows = [pl.read_csv(summary_path)] if summary_path.exists() else []
done = {(r["state"], r["year"]) for r in rows[0].to_dicts()} if rows else set()

for year in years:
    gaps = []
    for state in states:
        if (state, year) in done:
            continue
        group = for_year(units.filter(pl.col("state") == state), year, base).select(keep)
        if group.height == 0:
            continue
        group = group.with_row_index("row", offset=1).with_columns(taxsimid=pl.col("row").cast(pl.Int64)).drop("row")
        oracle = run_oracle(group).select("taxsimid", pl.col("siitax").alias("o_si"), pl.col("fiitax").alias("o_fi"))
        mine = calculate_taxes(
            group.drop([c for c in OPTIONAL_CHILD_COUNT_INPUTS if c in group.columns]), calculation_mode="taxsim", engine="python"
        ).select("taxsimid", pl.col("siitax").alias("t_si"), pl.col("fiitax").alias("t_fi"))
        joined = group.join(oracle, on="taxsimid").join(mine, on="taxsimid").with_columns(
            gap=pl.col("t_si") - pl.col("o_si"), fgap=pl.col("t_fi") - pl.col("o_fi")
        )
        differ = joined.filter(pl.col("gap").abs() > 1)
        gaps.append(differ)
        rows.append(
            pl.DataFrame(
                {
                    "state": [state], "name": [STATE_NAME.get(state, str(state))], "year": [year], "units": [joined.height],
                    "state_over_1": [differ.height], "state_over_2c": [joined.filter(pl.col("gap").abs() > 0.02).height],
                    "federal_over_1": [joined.filter(pl.col("fgap").abs() > 1).height],
                    "max_state_gap": [float(joined["gap"].abs().max())],
                }
            )
        )
        pl.concat(rows).write_csv(summary_path)
        print(STATE_NAME.get(state, state), year, f"{differ.height}/{joined.height} differ by more than $1", flush=True)
    if gaps:
        pl.concat(gaps).sort(pl.col("gap").abs(), descending=True).write_parquet(args.out / f"gaps_{year}.parquet")
