"""Exact-output regression grid: seeded random households for every state, year and mode.

usage:
  uv run --group test python scripts/regression_grid.py run OUT_DIR [--years 1977-2025] [--states 0-51] [--n 150]
  uv run --group test python scripts/regression_grid.py compare BASE_DIR NEW_DIR [--tol 1e-6]

`run` writes OUT_DIR/{mode}_{year}.parquet with every output column (idtl=2).
Households are drawn from a seed per state and year, so two runs with the same
--n see the same records. `compare` lists every column that differs and exits 1
if any does. State law starts in 1977; for 1960-1976 run state 0 only.

To check that a change leaves outputs alone, build the baseline from the
previous commit in a separate worktree:
  git worktree add ../taxsim_base HEAD
  (cd ../taxsim_base && uv run --group test python scripts/regression_grid.py run /tmp/base)
  uv run --group test python scripts/regression_grid.py run /tmp/new
  uv run --group test python scripts/regression_grid.py compare /tmp/base /tmp/new
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

from taxsim_py import calculate_taxes


def households(n: int, seed: int) -> dict:
    """Random households covering every TAXSIM input."""
    rng = np.random.default_rng(seed)

    def some(p, scale):
        return np.where(rng.random(n) < p, np.round(rng.lognormal(np.log(scale), 1.0, n)), 0.0)

    mstat = rng.choice([1, 2, 6, 8, 1, 2], n)
    married = np.isin(mstat, [2, 6])
    depx = np.where(mstat == 8, 0, rng.choice([0, 0, 1, 2, 3, 4], n))
    return dict(
        mstat=mstat, depx=depx,
        page=rng.choice([0, 30, 45, 64, 66, 72, 80], n),
        sage=np.where(married, rng.choice([0, 30, 50, 66, 75], n), 0),
        age1=np.where(depx >= 1, rng.integers(0, 22, n), 0),
        age2=np.where(depx >= 2, rng.integers(0, 22, n), 0),
        age3=np.where(depx >= 3, rng.integers(0, 22, n), 0),
        pwages=np.where(rng.random(n) < 0.85, np.round(rng.lognormal(np.log(40000), 1.1, n)), 0.0),
        swages=np.where(married & (rng.random(n) < 0.6), np.round(rng.lognormal(np.log(30000), 1.1, n)), 0.0),
        psemp=some(0.12, 15000) * rng.choice([1, 1, 1, -1], n),
        ssemp=np.where(married, some(0.06, 10000), 0.0),
        dividends=some(0.25, 2000), intrec=some(0.35, 1500),
        stcg=some(0.1, 3000) * rng.choice([1, 1, -1], n),
        ltcg=some(0.15, 12000) * rng.choice([1, 1, 1, -1], n),
        otherprop=some(0.1, 5000) * rng.choice([1, 1, -1], n),
        nonprop=some(0.06, 4000), pensions=some(0.2, 15000), gssi=some(0.2, 18000),
        pui=some(0.08, 6000), sui=np.where(married, some(0.04, 5000), 0.0),
        transfers=some(0.1, 4000), rentpaid=some(0.35, 10000), proptax=some(0.4, 4000),
        otheritem=some(0.4, 8000), childcare=np.where(depx > 0, some(0.4, 4000), 0.0),
        mortgage=some(0.3, 9000), scorp=some(0.05, 20000),
        pbusinc=some(0.06, 30000), pprofinc=some(0.04, 40000),
        sbusinc=np.where(married, some(0.03, 20000), 0.0),
        sprofinc=np.where(married, some(0.02, 20000), 0.0),
    )


def _span(text: str) -> range:
    low, _, high = text.partition("-")
    return range(int(low), int(high or low) + 1)


def run(out: Path, years: range, states: range, n: int, modes: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for year in years:
        frames = [
            pl.DataFrame({"taxsimid": np.arange(n) + state * 1000, "state": state, **households(n, year * 100 + state)})
            for state in states
        ]
        df = pl.concat(frames, how="vertical_relaxed")
        for mode in modes:
            start = time.time()
            result = calculate_taxes(df, year=year, calculation_mode=mode, idtl=2)
            result.write_parquet(out / f"{mode}_{year}.parquet")
            print(f"{mode} {year}: {result.height} rows in {time.time() - start:.1f}s", flush=True)


def compare(base: Path, new: Path, tol: float) -> int:
    differing = 0
    names = sorted(p.name for p in new.glob("*.parquet"))
    for name in names:
        n = pl.read_parquet(new / name).sort("taxsimid")
        b = pl.read_parquet(base / name).filter(pl.col("taxsimid").is_in(n["taxsimid"].implode())).sort("taxsimid")
        if b.height != n.height or b.columns != n.columns:
            print(f"{name}: rows or columns differ")
            differing += 1
            continue
        for column in b.columns:
            if b[column].dtype.is_numeric():
                missing = (-7e99, -9e99)
                left = b[column].cast(pl.Float64).fill_nan(missing[0]).fill_null(missing[1])
                right = n[column].cast(pl.Float64).fill_nan(missing[0]).fill_null(missing[1])
                gap = (left - right).abs()
                if (gap > tol).any():
                    differing += 1
                    examples = b.filter(gap > tol).select("taxsimid", "state").head(3).rows()
                    print(f"{name} {column}: {(gap > tol).sum()} rows differ, max {gap.max():.4g}, e.g. {examples}")
            elif not b[column].equals(n[column]):
                differing += 1
                print(f"{name} {column}: differs")
    print(f"compared {len(names)} files; {differing} differing columns")
    return 1 if differing else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("out", type=Path)
    run_parser.add_argument("--years", default="1977-2025")
    run_parser.add_argument("--states", default="0-51")
    run_parser.add_argument("--n", type=int, default=150, help="households per state and year")
    run_parser.add_argument("--modes", default="statutory,taxsim")
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("base", type=Path)
    compare_parser.add_argument("new", type=Path)
    compare_parser.add_argument("--tol", type=float, default=1e-6)
    args = parser.parse_args()
    if args.command == "run":
        run(args.out, _span(args.years), _span(args.states), args.n, args.modes.split(","))
    else:
        sys.exit(compare(args.base, args.new, args.tol))


if __name__ == "__main__":
    main()
