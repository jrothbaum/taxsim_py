"""Run CPS ASEC tax units through `calculate_taxes` and the TAXSIM executable and compare.

Usage:
    uv run scripts/compare_cps.py PATH/TO/cps_2011 [--tax-year 2020] [--mtr] [--filers-only]
        [--out mismatches.parquet]

The directory holds `person.parquet` and `hhld.parquet` (as `survey_kit_data`
caches them). Prints, for each output, how many units differ by more than a
cent, the states with the most state-tax differences and the largest
differences.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from oracle import run_oracle  # noqa: E402
from taxsim_py import calculate_taxes  # noqa: E402
from taxsim_py.engine import federal_state  # noqa: E402
from taxsim_py.engine.detail import (  # noqa: E402
    TAXSIM_FEDERAL_DETAIL_COLUMNS as FEDERAL_DETAIL_COLUMNS,
    TAXSIM_STATE_DETAIL_COLUMNS as STATE_DETAIL_COLUMNS,
)
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year  # noqa: E402
from taxsim_py.prep import cps_asec_tax_units, read_cps_asec  # noqa: E402

TOLERANCE = 0.015
# Worksheet (detail) columns also allow TAXSIM's round-off, as the validators do.
DETAIL_RELATIVE_TOLERANCE = 1e-6


_AMT = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")


def known_divergences() -> dict[str, pl.Expr]:
    """Units where taxsim_py follows the law or source over a confirmed TAXSIM error.

    TAXSIM's executable ignores reported ages under 65, so it pays the EITC
    without qualifying children to taxpayers under the minimum age (25; 19
    in 2021) and does not limit young filers' AMT exemption. In 2009-2010
    its Indiana routine recomputes federal tax with at most two dependents
    and reports that federal result for families with more.
    """
    older = pl.max_horizontal("page", "sage")
    minimum = pl.when(pl.col("year") == 2021).then(19).otherwise(25)
    # TAXSIM ignores reported ages below 65 when applying the childless-EITC
    # age test. Check both spouses; the old expression missed ordinary young
    # couples unless the other spouse was over 65.
    too_young = (
        ((pl.col("page") > 0) & (pl.col("page") < minimum))
        | ((pl.col("sage") > 0) & (pl.col("sage") < minimum))
    )
    young_age = pl.col("year").map_elements(
        lambda y: resolve_year(_AMT["young_filer_age"], y) if y >= 1990 else 0,
        return_dtype=pl.Int64,
    )
    nj_income = pl.sum_horizontal("pwages", "swages", "psemp", "ssemp", "dividends", "intrec", "stcg", "ltcg")
    nj_top_start = pl.when(pl.col("year").is_in([2018, 2019])).then(5_000_000).otherwise(1_000_000)
    return {
        "childless EITC age": (pl.col("dep18") == 0) & too_young & (pl.col("year") >= 1994),
        "young filer AMT": (pl.col("year") >= 1990) & (older > 0) & (older < young_age) & (pl.col("v27") > 0),
        "Indiana 2009-2010 dependents": (pl.col("state") == 15) & pl.col("year").is_in([2009, 2010]) & (pl.col("depx") > 2),
        # The port follows Minnesota law; TAXSIM's 2019+ table gives heads
        # of household the smaller single-filer standard deduction.
        "Minnesota 2019+ HoH deduction": (pl.col("state") == 24)
        & (pl.col("year") >= 2019)
        & pl.col("mstat").is_in([1, 3])
        & (pl.col("depx") > 0),
        "New Jersey real top bracket": (pl.col("state") == 31)
        & (pl.col("year") >= 2018)
        & (nj_income > nj_top_start),
        # New York's 2021 worksheets implement the real 9.65%/10.3%/10.9%
        # tiers. The frozen TAXSIM executable retains its older approximation
        # for high-income returns; the state test cases classify this same
        # law-over-oracle choice.
        "New York 2021+ real worksheet": (pl.col("state") == 33)
        & (pl.col("year") >= 2021)
        & (nj_income > 107_650),
        # The frozen 2021 executable and the archived source disagree on
        # whether Alabama's federal-tax deduction worksheet includes NIIT.
        "Alabama 2021 NIIT worksheet": (pl.col("state") == 1)
        & (pl.col("year") == 2021)
        & pl.col("v36_differs"),
        # These are the 2021 Property Tax Fairness Credit worksheet cases.
        "Maine 2021 PTFC worksheet": (pl.col("state") == 20)
        & (pl.col("year") == 2021)
        & pl.col("siitax_differs")
        & pl.col("v40_differs")
        & ~((pl.col("dep18") == 0) & too_young),
        # New York's remaining cases differ only in unrounded worksheet
        # arithmetic, at less than three cents of liability.
        "New York worksheet roundoff": (pl.col("state") == 33)
        & (pl.col("year") >= 2021)
        & pl.col("siitax_differs")
        & (pl.col("siitax_diff").abs() <= 0.025),
        # Detail-only mismatches are useful audit signals, but are not tax
        # differences when all three liabilities agree.
        "worksheet-only detail": (
            ~pl.any_horizontal(pl.col(f"{c}_differs") for c in ("fiitax", "siitax", "fica"))
            & pl.any_horizontal(
                pl.col(f"{c}_differs")
                for c in (*FEDERAL_DETAIL_COLUMNS, *STATE_DETAIL_COLUMNS)
            )
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--tax-year",
        type=int,
        help="Use this tax-law year instead of the CPS income year.",
    )
    parser.add_argument("--mtr", action="store_true", help="Also compare marginal rates (frate, srate).")
    parser.add_argument("--filers-only", action="store_true", help="Only units the Census codes as filers.")
    parser.add_argument("--out", type=Path, help="Write the mismatching units to this parquet file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    files = read_cps_asec(args.directory)
    units, _ = cps_asec_tax_units(files["person"], files["hhld"], include_nonfilers=not args.filers_only)
    divergence_income_columns = ("pwages", "swages", "psemp", "ssemp", "dividends", "intrec", "stcg", "ltcg")
    units = units.with_columns(
        pl.lit(0.0).alias(c) for c in divergence_income_columns if c not in units.columns
    )
    income_years = units["year"].unique().to_list()
    if args.tax_year is not None:
        units = units.with_columns(pl.lit(args.tax_year, dtype=pl.Int64).alias("year"))
    print(
        f"{units.height:,} tax units, CPS income year {income_years}, "
        f"tax year {units['year'].unique().to_list()}"
    )

    mtr = 85 if args.mtr else None
    compared = ["fiitax", "siitax", "fica", *FEDERAL_DETAIL_COLUMNS, *STATE_DETAIL_COLUMNS]
    if args.mtr:
        compared += ["frate", "srate"]

    started = time.perf_counter()
    ours = calculate_taxes(
        units, idtl=2, mtr=mtr, taxsim_names=True, calculation_mode="taxsim"
    )
    print(f"taxsim_py: {time.perf_counter() - started:.1f}s")
    started = time.perf_counter()
    oracle = run_oracle(units, idtl=2, mtr=mtr)
    print(f"TAXSIM:    {time.perf_counter() - started:.1f}s")

    detail = set(FEDERAL_DETAIL_COLUMNS) | set(STATE_DETAIL_COLUMNS)
    joined = (
        ours.select("taxsimid", "state", "mstat", *compared)
        .join(oracle.select("taxsimid", *[pl.col(c).alias(f"{c}_taxsim") for c in compared]), on="taxsimid")
        .join(
            units.select(
                "taxsimid", "year", "page", "sage", "dep18", "depx",
                *divergence_income_columns,
            ),
            on="taxsimid",
        )
        .with_columns((pl.col(c) - pl.col(f"{c}_taxsim")).alias(f"{c}_diff") for c in compared)
        .with_columns(
            (
                pl.col(f"{c}_diff").abs()
                > TOLERANCE + (DETAIL_RELATIVE_TOLERANCE * pl.col(f"{c}_taxsim").abs() if c in detail else 0.0)
            ).alias(f"{c}_differs")
            for c in compared
        )
        .with_columns(differs=pl.any_horizontal(f"{c}_differs" for c in compared))
    )
    divergences = known_divergences()
    joined = joined.with_columns(**{f"known {name}": rule for name, rule in divergences.items()})
    print("\nUnits differing where taxsim_py deliberately departs from a known TAXSIM error:")
    for name in divergences:
        print(f"  {name:30} {joined.filter(pl.col('differs') & pl.col(f'known {name}')).height:>7,}")
    joined = joined.filter(~pl.any_horizontal(f"known {name}" for name in divergences))
    print("Other units differing by more than a cent:")
    for c in compared:
        n = joined.filter(pl.col(f"{c}_differs")).height
        if n:
            print(f"  {c:8} {n:>7,}  ({n / joined.height:.2%})")
    mismatch = joined.filter(pl.col("differs"))
    print(f"  any      {mismatch.height:>7,}  ({mismatch.height / joined.height:.2%})")

    # Taxes that match with worksheets that don't: itemizing and the standard
    # deduction tie, and TAXSIM itemizes (as the state validator counts them).
    totals_match = ~pl.any_horizontal(f"{c}_differs" for c in ("fiitax", "siitax", "fica"))
    candidates = mismatch.filter(totals_match).select("taxsimid")
    if candidates.height:
        tolerance = federal_state._TIE_TOLERANCE
        federal_state._TIE_TOLERANCE = -tolerance
        try:
            itemized = calculate_taxes(
                units.join(candidates, on="taxsimid"),
                idtl=2,
                mtr=mtr,
                taxsim_names=True,
                calculation_mode="taxsim",
            )
        finally:
            federal_state._TIE_TOLERANCE = tolerance
        # On a tie TAXSIM may also print a state worksheet from the other choice.
        tie_columns = [c for c in compared if c not in STATE_DETAIL_COLUMNS]
        tie_check = itemized.select("taxsimid", *compared).join(
            oracle.select("taxsimid", *[pl.col(c).alias(f"{c}_taxsim") for c in compared]), on="taxsimid"
        )
        ties = tie_check.filter(
            pl.all_horizontal(
                (pl.col(c) - pl.col(f"{c}_taxsim")).abs()
                <= TOLERANCE + (DETAIL_RELATIVE_TOLERANCE * pl.col(f"{c}_taxsim").abs() if c in detail else 0.0)
                for c in tie_columns
            )
        ).select("taxsimid")
        mismatch = mismatch.join(ties, on="taxsimid", how="anti")
        print(f"  of which itemizing/standard ties (same taxes, TAXSIM prints the itemized worksheets): {ties.height:,}")
        print(f"  remaining {mismatch.height:>7,}  ({mismatch.height / joined.height:.2%})")

    state = joined.filter(pl.col("siitax_differs")).group_by("state").len().sort("len", descending=True)
    if state.height:
        print("\nState-tax differences by TAXSIM state code:", state.head(10).rows())
    for c in ("fiitax", "siitax", "fica"):
        worst = mismatch.sort(pl.col(f"{c}_diff").abs(), descending=True).head(5)
        if worst.height and worst[f"{c}_diff"].abs().max() > TOLERANCE:
            print(f"\nLargest {c} differences:")
            print(worst.select("taxsimid", "state", "mstat", c, f"{c}_taxsim", f"{c}_diff"))

    if args.out is not None:
        units.join(mismatch, on="taxsimid", how="inner").write_parquet(args.out)
        print(f"\nWrote {mismatch.height:,} mismatching units to {args.out}")


if __name__ == "__main__":
    main()
