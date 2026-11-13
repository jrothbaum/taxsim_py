"""How much each statutory switch matters: flip one switch to TAXSIM's behavior and count the CPS households that change.

usage: uv run python scripts/audit_switch_impact.py PATH/TO/cps_2011 [--per-state 40] [--years 1985,2000,2012,2022,2025]

Writes docs/data/statutory_switch_impact.csv (one row per switch; resumes if the file exists). The statutory side of every
switch should be verified against a form or statute; this ranks them by how many households and dollars are at stake.
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
from build_comparison_report import STATE_NAME, for_year, sample_units  # noqa: E402
from taxsim_py.behavior import ALL_OVERRIDES_BEHAVIOR as STATUTORY_BEHAVIOR, FIXED_IN_2026_BUILD, BehaviorProfile  # noqa: E402
from taxsim_py.engine.inputs import OPTIONAL_CHILD_COUNT_INPUTS, TAXSIM_INPUTS  # noqa: E402
from taxsim_py.prep import cps_asec_tax_units, read_cps_asec  # noqa: E402

STATE_WORDS = {
    "alabama": 1, "arizona": 3, "arkansas": 4, "california": 5, "colorado": 6, "connecticut": 7, "delaware": 8,
    "georgia": 11, "hawaii": 12, "idaho": 13, "illinois": 14, "indiana": 15, "iowa": 16, "kansas": 17, "kentucky": 18,
    "louisiana": 19, "maine": 20, "maryland": 21, "massachusetts": 22, "michigan": 23, "minnesota": 24,
    "mississippi": 25, "missouri": 26, "montana": 27, "nebraska": 28, "jersey": 31, "mexico": 32, "york": 33,
    "carolina": None, "dakota": 35, "ohio": 36, "oklahoma": 37, "oregon": 38, "pennsylvania": 39, "rhode": 40,
    "utah": 45, "vermont": 46, "virginia": 47, "wisconsin": 50, "dc": 9, "district": 9,
}


def states_for(name: str) -> list[int] | None:
    words = name.split("_")
    if "north" in words and "carolina" in words:
        return [34]
    if "south" in words and "carolina" in words:
        return [41]
    if "west" in words and "virginia" in words:
        return [49]
    found = [STATE_WORDS[w] for w in words if STATE_WORDS.get(w)]
    return found or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--per-state", type=int, default=40)
    parser.add_argument("--years", default="1985,2000,2012,2022,2025")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "data" / "statutory_switch_impact.csv")
    args = parser.parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", "4")
    years = [int(y) for y in args.years.split(",")]

    files = read_cps_asec(args.directory)
    units, _ = cps_asec_tax_units(files["person"], files["hhld"], include_nonfilers=False)
    base_year = int(units["year"][0])
    units = units.drop("year").with_columns(year=pl.lit(base_year, dtype=pl.Int64))
    sample = sample_units(units, args.per_state, 2011)
    keep = [c for c in sample.columns if c in TAXSIM_INPUTS or c == "taxsimid"]

    def cases_for(states: list[int] | None, per_state: int) -> pl.DataFrame:
        frames = []
        for year in years:
            frame = for_year(sample, year, base_year).select(keep)
            if states is not None:
                frame = frame.filter(pl.col("state").is_in(states))
            else:
                frame = pl.concat([g.head(per_state) for g in frame.partition_by("state")])
            frames.append(frame)
        out = pl.concat(frames).with_row_index("row", offset=1)
        return out.with_columns(taxsimid=pl.col("row").cast(pl.Int64)).drop("row")

    original = api.resolve_behavior

    def run(cases: pl.DataFrame, behavior: BehaviorProfile) -> pl.DataFrame:
        api.resolve_behavior = lambda mode: behavior
        try:
            return api.calculate_taxes(
                cases.drop([c for c in OPTIONAL_CHILD_COUNT_INPUTS if c in cases.columns]),
                calculation_mode="statutory", engine="python",
            ).select("taxsimid", "year", "state", "fiitax", "siitax")
        finally:
            api.resolve_behavior = original

    done = set(pl.read_csv(args.out)["switch"]) if args.out.exists() else set()
    rows = [pl.read_csv(args.out)] if args.out.exists() else []
    baseline_cache: dict[tuple, pl.DataFrame] = {}
    names = [f.name for f in fields(BehaviorProfile) if f.name != "mode" and f.name not in FIXED_IN_2026_BUILD]
    for index, name in enumerate(names, 1):
        if name in done:
            continue
        states = states_for(name)
        key = tuple(states) if states else ("all",)
        cases = cases_for(states, 8)
        if key not in baseline_cache:
            baseline_cache[key] = run(cases, STATUTORY_BEHAVIOR)
        flipped = run(cases, replace(STATUTORY_BEHAVIOR, **{name: not getattr(STATUTORY_BEHAVIOR, name)}))
        joined = baseline_cache[key].join(flipped, on="taxsimid", suffix="_f")
        diff = (joined["fiitax_f"] - joined["fiitax"]).abs().alias("fed")
        diff_s = (joined["siitax_f"] - joined["siitax"]).abs().alias("state")
        frame = joined.with_columns(diff, diff_s)
        changed = frame.filter((pl.col("fed") > 1) | (pl.col("state") > 1))
        rows.append(
            pl.DataFrame(
                {
                    "switch": [name],
                    "states": [",".join(STATE_NAME[s] for s in states) if states else "all"],
                    "units": [frame.height],
                    "changed": [changed.height],
                    "share_changed": [changed.height / max(frame.height, 1)],
                    "mean_abs_change_if_changed": [float(changed.select(pl.max_horizontal("fed", "state")).mean().item() or 0.0)],
                    "max_abs_change": [float(frame.select(pl.max_horizontal("fed", "state")).max().item() or 0.0)],
                    "years_changed": [",".join(str(y) for y in sorted(set(changed["year_f"].to_list() if "year_f" in changed.columns else changed["year"].to_list())))],
                }
            )
        )
        pl.concat(rows, how="diagonal_relaxed").write_csv(args.out)
        print(f"{index}/{len(names)} {name}: {changed.height}/{frame.height} changed", flush=True)


if __name__ == "__main__":
    main()
