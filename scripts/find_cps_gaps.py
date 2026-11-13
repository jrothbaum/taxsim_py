"""List CPS households where taxsim-py (TAXSIM mode) differs from the TAXSIM executable.

usage: uv run python scripts/find_cps_gaps.py PATH/TO/cps_2011 --states 16,11 --years 1995,2005,2015 [--units 200] [--out DIR]

Uses the same sample and price scaling as scripts/build_comparison_report.py, then writes the failing households
(inputs, both taxes) to DIR/gaps.parquet, one row per household-year, largest gap first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_comparison_report import (  # noqa: E402
    OPTIONAL_CHILD_COUNT_INPUTS, STATE_NAME, TAXSIM_INPUTS, calculate_taxes, cps_asec_tax_units, for_year, read_cps_asec, run_oracle, sample_units,
)

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("--states", required=True)
parser.add_argument("--years", required=True)
parser.add_argument("--units", type=int, default=200)
parser.add_argument("--seed", type=int, default=2011)
parser.add_argument("--out", type=Path, default=Path("/tmp/gaps"))
args = parser.parse_args()

files = read_cps_asec(args.directory)
units, _ = cps_asec_tax_units(files["person"], files["hhld"], include_nonfilers=False)
base = int(units["year"][0])
units = units.drop("year").with_columns(year=pl.lit(base, dtype=pl.Int64))
states = [int(s) for s in args.states.split(",")]
sample = sample_units(units.filter(pl.col("state").is_in(states)), args.units, args.seed)
keep = [c for c in sample.columns if c in TAXSIM_INPUTS or c == "taxsimid"]
out = []
for year in (int(y) for y in args.years.split(",")):
    cases = for_year(sample, year, base).select(keep)
    cases = cases.with_row_index("row", offset=1).with_columns(taxsimid=pl.col("row").cast(pl.Int64)).drop("row")
    for group in cases.partition_by("state", maintain_order=True):
        oracle = run_oracle(group).select("taxsimid", pl.col("siitax").alias("o_si"), pl.col("fiitax").alias("o_fi"))
        mine = calculate_taxes(
            group.drop([c for c in OPTIONAL_CHILD_COUNT_INPUTS if c in group.columns]), calculation_mode="taxsim", engine="python"
        ).select(
            "taxsimid", pl.col("siitax").alias("t_si"), pl.col("fiitax").alias("t_fi"))
        joined = group.join(oracle, on="taxsimid").join(mine, on="taxsimid")
        out.append(joined.with_columns(gap=pl.col("t_si") - pl.col("o_si")).filter(pl.col("gap").abs() > 1))
        print(STATE_NAME[group["state"][0]], year, f"{out[-1].height}/{group.height} differ", flush=True)
args.out.mkdir(parents=True, exist_ok=True)
result = pl.concat(out).sort(pl.col("gap").abs(), descending=True)
result.write_parquet(args.out / "gaps.parquet")
print(result.height, "households ->", args.out / "gaps.parquet")
