"""Grid of ages and incomes for one state, TAXSIM mode against the executable (one state-year at a time).

usage: uv run python scripts/grid_state.py STATE_CODE YEAR[,YEAR...]
Prints every household that differs by more than $1.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_comparison_report import calculate_taxes, run_oracle  # noqa: E402

state = int(sys.argv[1])
rows = []
for mstat, (page, sage), (pw, sw), pens, intr, gssi in itertools.product(
    (1, 2), [(60, 0), (63, 0), (66, 0), (60, 60), (63, 60), (66, 60), (63, 63), (66, 63), (66, 66)],
    [(0, 0), (3000, 0), (30000, 0), (30000, 20000), (60000, 5000)], (0, 12000, 40000), (0, 3000), (0, 15000),
):
    if mstat == 1 and sage:
        continue
    rows.append(dict(mstat=mstat, page=page, sage=sage, pwages=pw, swages=sw if mstat == 2 else 0, pensions=pens,
                     intrec=intr, gssi=gssi))
base = pl.DataFrame(rows)
for year in (int(y) for y in sys.argv[2].split(",")):
    cases = base.with_columns(taxsimid=pl.int_range(1, base.height + 1), year=pl.lit(year), state=pl.lit(state))
    o = run_oracle(cases).select("taxsimid", pl.col("siitax").alias("o"))
    t = calculate_taxes(cases, calculation_mode="taxsim", engine="python").select("taxsimid", pl.col("siitax").alias("t"))
    j = cases.join(o, on="taxsimid").join(t, on="taxsimid").with_columns(gap=pl.col("t") - pl.col("o"))
    bad = j.filter(pl.col("gap").abs() > 1)
    if len(sys.argv) > 3:
        bad = bad.filter(~pl.col('page').is_between(62, 64) & ~pl.col('sage').is_between(62, 64))
    print(year, f"{bad.height}/{j.height} differ")
    if bad.height:
        with pl.Config(tbl_rows=12, tbl_cols=20, tbl_width_chars=200):
            print(bad.sort(pl.col("gap").abs(), descending=True).drop("taxsimid", "year", "state").head(12))
