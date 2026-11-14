"""Confirm each logic-error switch against the compiled TAXSIM: does TAXSIM really do what the switch says?

usage: uv run python scripts/confirm_logic_switches.py PATH/TO/cps_2011 [--per-state 40]

For every switch the ledger calls a logic error, take the CPS households whose federal or state tax changes when that
one switch is flipped from the statutory side to TAXSIM's side (the affected households), run the compiled TAXSIM on
them, and count how often the oracle agrees with
  - TAXSIM mode (all switches on TAXSIM's side),
  - statutory mode with only this switch flipped,
  - statutory mode as is.
A switch is confirmed when TAXSIM mode matches the oracle on the affected households and the statutory side does not.
Writes docs/data/logic_switch_confirmation.csv (one row per switch; resumes if the file exists).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import fields, replace
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import taxsim_py.api as api  # noqa: E402
from audit_switch_impact import states_for  # noqa: E402
from build_comparison_report import STATE_NAME, for_year, sample_units  # noqa: E402
from oracle import run_oracle  # noqa: E402
from taxsim_py.behavior import FIXED_IN_2026_BUILD, STATUTORY_BEHAVIOR, BehaviorProfile  # noqa: E402
from taxsim_py.engine.inputs import OPTIONAL_CHILD_COUNT_INPUTS, TAXSIM_INPUTS  # noqa: E402
from taxsim_py.prep import cps_asec_tax_units, read_cps_asec  # noqa: E402

YEARS = [1979, 1985, 1990, 1995, 2000, 2005, 2010, 2012, 2015, 2018, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
COLUMNS = ["fiitax", "siitax"]
TOLERANCE = 1.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--per-state", type=int, default=40)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "data" / "logic_switch_confirmation.csv")
    parser.add_argument("--only", default="", help="comma-separated switches to rerun (their rows are replaced)")
    parser.add_argument(
        "--redo-zero", action="store_true",
        help="rerun only the switches that changed no household in an earlier run, with the (larger) --per-state sample",
    )
    args = parser.parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", "4")

    live = {f.name for f in fields(BehaviorProfile)}
    names = []
    for line in (ROOT / "docs" / "statutory_audit.md").read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 7 and cells[1].startswith("`") and cells[6] == "logic":
            name = cells[1].strip("`")
            if name in live and name not in FIXED_IN_2026_BUILD:
                names.append(name)

    files = read_cps_asec(args.directory)
    units, _ = cps_asec_tax_units(files["person"], files["hhld"], include_nonfilers=False)
    base_year = int(units["year"][0])
    units = units.drop("year").with_columns(year=pl.lit(base_year, dtype=pl.Int64))
    sample = sample_units(units, args.per_state, 2011)
    keep = [c for c in sample.columns if c in TAXSIM_INPUTS or c == "taxsimid"]

    def cases_for(states: list[int] | None) -> pl.DataFrame:
        frames = []
        for year in YEARS:
            frame = for_year(sample, year, base_year).select(keep)
            if states is not None:
                frame = frame.filter(pl.col("state").is_in(states))
            else:
                frame = pl.concat([g.head(30 if args.redo_zero else 8) for g in frame.partition_by("state")])
            frames.append(frame)
        out = pl.concat(frames).with_row_index("row", offset=1)
        return out.with_columns(taxsimid=pl.col("row").cast(pl.Int64)).drop("row")

    original = api.resolve_behavior

    def run(cases: pl.DataFrame, mode: str, behavior: BehaviorProfile | None = None) -> pl.DataFrame:
        if behavior is not None:
            api.resolve_behavior = lambda _mode: behavior
        try:
            return api.calculate_taxes(
                cases.drop([c for c in OPTIONAL_CHILD_COUNT_INPUTS if c in cases.columns]),
                calculation_mode=mode, engine="python",
            ).select("taxsimid", *COLUMNS)
        finally:
            api.resolve_behavior = original

    def close(a: pl.DataFrame, b: pl.DataFrame, ids: pl.Series) -> float:
        joined = a.join(b, on="taxsimid", suffix="_b").filter(pl.col("taxsimid").is_in(ids))
        if joined.height == 0:
            return float("nan")
        ok = pl.all_horizontal([(pl.col(c) - pl.col(f"{c}_b")).abs() <= TOLERANCE for c in COLUMNS])
        return float(joined.select(ok.mean()).item())

    done = set(pl.read_csv(args.out)["switch"]) if args.out.exists() else set()
    rows = [pl.read_csv(args.out)] if args.out.exists() else []
    if args.only:
        wanted = set(args.only.split(","))
        earlier = pl.read_csv(args.out)
        names = [n for n in names if n in wanted]
        done = set()
        rows = [earlier.filter(~pl.col("switch").is_in(list(wanted)))]
    if args.redo_zero:
        earlier = pl.read_csv(args.out)
        zero = earlier.filter(pl.col("affected") == 0)["switch"].to_list()
        names = [n for n in names if n in zero]
        done = set()
        rows = [earlier.filter(pl.col("affected") > 0)]
    cache: dict[tuple, tuple] = {}
    for index, name in enumerate(names, 1):
        if name in done:
            continue
        states = states_for(name)
        key = tuple(states) if states else ("all",)
        if key not in cache:
            cache.clear()
            cases = cases_for(states)
            cache[key] = (cases, run(cases, "statutory", STATUTORY_BEHAVIOR), run(cases, "taxsim"))
        cases, statutory, taxsim_mode = cache[key]
        flipped = run(cases, "statutory", replace(STATUTORY_BEHAVIOR, **{name: not getattr(STATUTORY_BEHAVIOR, name)}))
        joined = statutory.join(flipped, on="taxsimid", suffix="_f")
        changed = joined.filter(pl.any_horizontal([(pl.col(c) - pl.col(f"{c}_f")).abs() > TOLERANCE for c in COLUMNS]))
        affected = changed["taxsimid"]
        record = {"switch": name, "states": ",".join(STATE_NAME[s] for s in states) if states else "all", "affected": affected.len()}
        if affected.len():
            subset = cases.filter(pl.col("taxsimid").is_in(affected))
            oracle = run_oracle(subset).select("taxsimid", *COLUMNS)
            record["oracle_equals_taxsim_mode"] = close(oracle, taxsim_mode, affected)
            record["oracle_equals_flipped_switch"] = close(oracle, flipped, affected)
            record["oracle_equals_statutory"] = close(oracle, statutory, affected)
            record["years"] = ",".join(str(y) for y in sorted(set(subset["year"].to_list())))
        rows.append(pl.DataFrame([record]))
        pl.concat(rows, how="diagonal_relaxed").write_csv(args.out)
        print(f"{index}/{len(names)} {name}: {record}", flush=True)


if __name__ == "__main__":
    main()
