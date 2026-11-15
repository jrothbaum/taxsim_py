"""Confirm the logic-error switches that no CPS household reaches, on random households instead.

usage: uv run python scripts/confirm_unreached_switches.py [--households 1500] [--only a,b]

scripts/confirm_logic_switches.py reads logic_switch_confirmation.csv; the switches with zero affected CPS households have no
evidence against the compiled TAXSIM. For each of them this draws seeded random households (scripts/regression_grid.py) for
the switch's states over many law years, flips the one switch, runs the compiled TAXSIM on the households whose tax changes
and records how often the oracle follows TAXSIM mode, the flipped switch and statutory mode. Rewrites the zero rows of
docs/data/logic_switch_confirmation.csv.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import taxsim_py.api as api  # noqa: E402
from audit_switch_impact import states_for  # noqa: E402
from build_comparison_report import STATE_NAME  # noqa: E402
from oracle import run_oracle  # noqa: E402
from regression_grid import households  # noqa: E402
from taxsim_py.behavior import STATUTORY_BEHAVIOR, BehaviorProfile  # noqa: E402

YEARS = [1977, 1979, 1981, 1983, 1985, 1987, 1989, 1992, 1995, 1998, 2001, 2003, 2006, 2009, 2012, 2015, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025, 2026]
FEDERAL_SAMPLE_STATES = [0, 5, 24, 34, 36, 39, 49]
COLUMNS = ["fiitax", "siitax"]
TOLERANCE = 1.0
MAX_ORACLE = 80


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--households", type=int, default=1500)
    parser.add_argument("--only", default="")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "data" / "logic_switch_confirmation.csv")
    args = parser.parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", "4")

    earlier = pl.read_csv(args.out)
    zero = earlier.filter(pl.col("affected") == 0)["switch"].to_list()
    if args.only:
        zero = [s for s in zero if s in set(args.only.split(","))]
    live = {f.name for f in fields(BehaviorProfile)}
    zero = [s for s in zero if s in live]
    done = {}
    original = api.resolve_behavior

    def cases_for(states: list[int]) -> pl.DataFrame:
        frames = []
        for year in YEARS:
            for state in states:
                n = args.households
                data = households(n, year * 100 + state)
                # The compiled program stops on an adult age of 0 and on spouse inputs of a non-joint return.
                joint = data["mstat"] == 2
                data["page"] = np.where(data["page"] == 0, 40, data["page"])
                data["sage"] = np.where(joint, np.where(data["sage"] == 0, 40, data["sage"]), 0)
                for spouse in ("swages", "ssemp", "sui", "sbusinc", "sprofinc"):
                    data[spouse] = np.where(joint, data[spouse], 0.0)
                frames.append(pl.DataFrame({"state": state, "year": year, **data}))
        out = pl.concat(frames, how="vertical_relaxed")
        return out.with_columns(taxsimid=pl.int_range(1, out.height + 1, dtype=pl.Int64))

    def run(cases: pl.DataFrame, mode: str, behavior: BehaviorProfile | None = None) -> pl.DataFrame:
        if behavior is not None:
            api.resolve_behavior = lambda _mode: behavior
        try:
            return api.calculate_taxes(cases, calculation_mode=mode, engine="python").select("taxsimid", *COLUMNS)
        finally:
            api.resolve_behavior = original

    def close(a: pl.DataFrame, b: pl.DataFrame, ids: pl.Series) -> float:
        joined = a.join(b, on="taxsimid", suffix="_b").filter(pl.col("taxsimid").is_in(ids.implode()))
        ok = pl.all_horizontal([(pl.col(c) - pl.col(f"{c}_b")).abs() <= TOLERANCE for c in COLUMNS])
        return float(joined.select(ok.mean()).item()) if joined.height else float("nan")

    cache: dict[tuple, tuple] = {}
    for index, name in enumerate(zero, 1):
        states = states_for(name) or FEDERAL_SAMPLE_STATES
        key = tuple(states)
        if key not in cache:
            cache.clear()
            cases = cases_for(states)
            cache[key] = (cases, run(cases, "statutory", STATUTORY_BEHAVIOR), run(cases, "taxsim"))
        cases, statutory, taxsim_mode = cache[key]
        flipped = run(cases, "statutory", replace(STATUTORY_BEHAVIOR, **{name: not getattr(STATUTORY_BEHAVIOR, name)}))
        joined = statutory.join(flipped, on="taxsimid", suffix="_f")
        changed = joined.filter(pl.any_horizontal([(pl.col(c) - pl.col(f"{c}_f")).abs() > TOLERANCE for c in COLUMNS]))
        affected = changed["taxsimid"]
        record = {"switch": name, "states": ",".join(STATE_NAME.get(s, "all") for s in states) if states_for(name) else "all", "affected": affected.len()}
        if affected.len():
            picked = affected.sample(min(MAX_ORACLE, affected.len()), seed=1)
            subset = cases.filter(pl.col("taxsimid").is_in(picked.implode()))
            oracle = run_oracle(subset).select("taxsimid", *COLUMNS)
            record["oracle_equals_taxsim_mode"] = close(oracle, taxsim_mode, picked)
            record["oracle_equals_flipped_switch"] = close(oracle, flipped, picked)
            record["oracle_equals_statutory"] = close(oracle, statutory, picked)
            record["years"] = ",".join(str(y) for y in sorted(set(subset["year"].to_list())))
        done[name] = record
        print(f"{index}/{len(zero)} {name}: {record}", flush=True)
        keep = earlier.filter(~pl.col("switch").is_in(list(done)))
        pl.concat([keep, pl.DataFrame(list(done.values()))], how="diagonal_relaxed").write_csv(args.out)


if __name__ == "__main__":
    main()
