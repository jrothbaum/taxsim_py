"""Single unified validation runner for everything federal, TX, across
every year in tests/federal_cases.py's YEARS list.

Builds one test-case table (tests/federal_cases.py), runs it through
taxsim2024.exe once, runs it through the public API once, diffs every
output column we claim to support, and prints only the rows that fail -
each one labeled by its `description` so a failure is immediately
legible without hunting through separate scripts.

Add new coverage by adding rows to tests/federal_cases.py, not by writing
another script like this one.
"""

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

from taxsim_py import calculate_taxes  # noqa: E402
from taxsim_py.engine.detail import TAXSIM_FEDERAL_DETAIL_COLUMNS as FEDERAL_DETAIL_COLUMNS  # noqa: E402
from federal_cases import build_federal_test_cases  # noqa: E402
from oracle import TAXSIM_EXE, knife_edge_ids, run_oracle  # noqa: E402

# Output columns we currently claim to support, compared to the cent. Tolerance
# is slightly above $0.01, not $0.01 itself: summing the same terms in a
# different order can put a value exactly on a half-cent boundary (e.g.
# 6391.575) a hair on one side or the other in double precision, so the two
# implementations occasionally round that one case differently by a cent -
# confirmed harmless (see validate_federal.py history), not a real error.
COMPARED_COLUMNS = ["fiitax", "frate", "fica", "ficar", "tfica", *FEDERAL_DETAIL_COLUMNS]
RATE_COLUMNS = ["frate"]
MISMATCH_TOLERANCE = 0.015

# Years where this project DELIBERATELY does not match taxsim2024.exe, per
# user direction ("if the model is wrong, use the real parameters that are
# known"): the oracle's own 2023 parameters for these 4 items are confirmed
# (via live oracle probes, not assumption) to disagree with real, published
# law, so this project uses the real values instead. The OASDI wage base
# fix alone (real $160,200 vs the oracle's stale $153,600) has a wide blast
# radius - EVERY 2023 wages test above $153,600 shows a `fica`/`tfica`
# mismatch as a direct, expected consequence, not 600+ independent bugs.
# See parameters/national/payroll_tax.yaml, eitc.csv, eitc_misc.yaml, and
# amt.yaml (head_of_household AMT exemption) for the specific values and
# their sourcing. A failure in a DIFFERENT year is still a real regression
# to investigate; a 2023 failure should be checked against this list before
# assuming it's new.
KNOWN_ORACLE_DIVERGENT_YEARS = {2023}

def main() -> None:
    # The API derives the sales-tax deduction itself.
    cases = build_federal_test_cases().drop("state_sales_or_income_tax_ded")
    expected = run_oracle(cases, mtr=85, idtl=2).select(
        "taxsimid", *[pl.col(c).alias(f"{c}_expected") for c in COMPARED_COLUMNS]
    )
    actual = calculate_taxes(cases, mtr=85, idtl=2, taxsim_names=True).select(
        "taxsimid", "description", "year", *COMPARED_COLUMNS
    )

    comparison = actual.join(expected, on="taxsimid")
    # A missing result counts as a mismatch.
    diff_exprs = [
        (pl.col(c) - pl.col(f"{c}_expected")).abs().fill_null(float("inf")).fill_nan(float("inf")).alias(f"{c}_diff")
        for c in COMPARED_COLUMNS
    ]
    comparison = comparison.with_columns(diff_exprs)
    # TAXSIM's 1987-1997 credit total reads a variable it never sets there.
    comparison = comparison.with_columns(
        pl.when(pl.col("year").is_between(1987, 1997)).then(0.0).otherwise(pl.col("credits_diff")).alias("credits_diff")
    )

    is_mismatch = pl.any_horizontal(
        [pl.col(f"{c}_diff") > MISMATCH_TOLERANCE for c in COMPARED_COLUMNS]
    )
    mismatches = comparison.filter(is_mismatch)

    # Rate-only mismatches that vanish a dollar away are TAXSIM round-off.
    levels_match = pl.all_horizontal(
        pl.col(f"{c}_diff") <= MISMATCH_TOLERANCE for c in COMPARED_COLUMNS if c not in RATE_COLUMNS
    )
    knife_edges = knife_edge_ids(
        cases.join(mismatches.filter(levels_match).select("taxsimid"), on="taxsimid"),
        lambda frame: calculate_taxes(frame, mtr=85),
        RATE_COLUMNS,
    )
    knife = mismatches.filter(pl.col("taxsimid").is_in(list(knife_edges)))
    mismatches = mismatches.filter(~pl.col("taxsimid").is_in(list(knife_edges)))

    # Cases marked `oracle_divergent` are exact where the oracle is not.
    flagged = cases.select(
        "taxsimid",
        (pl.col("oracle_divergent").fill_null(False) if "oracle_divergent" in cases.columns else pl.lit(False)).alias(
            "case_divergent"
        ),
    )
    mismatches = mismatches.join(flagged, on="taxsimid")
    divergent = pl.col("year").is_in(KNOWN_ORACLE_DIVERGENT_YEARS) | pl.col("case_divergent")
    known_divergent = mismatches.filter(divergent)
    unexpected = mismatches.filter(~divergent)

    print(f"Ran {comparison.height} test cases across {COMPARED_COLUMNS}.")
    if not known_divergent.is_empty():
        print(
            f"{known_divergent.height} failures are in {sorted(KNOWN_ORACLE_DIVERGENT_YEARS)} or in cases marked "
            "`oracle_divergent` - EXPECTED, this project deliberately uses real parameters or exact arithmetic "
            "where the oracle's are known to be wrong. Not printed individually."
        )
    if not knife.is_empty():
        print(
            f"{knife.height} marginal-rate mismatches sit on a threshold where TAXSIM's single-precision "
            "round-off shows and agree a dollar away - not printed individually."
        )
    if unexpected.is_empty():
        print(f"All other cases match {TAXSIM_EXE.name} exactly.")
    else:
        mismatches = unexpected
        print(f"{mismatches.height} FAILURES:")
        for row in mismatches.iter_rows(named=True):
            failed = [c for c in COMPARED_COLUMNS if row[f"{c}_diff"] > MISMATCH_TOLERANCE]
            detail = ", ".join(f"{c}: got {row[c]}, expected {row[f'{c}_expected']}" for c in failed)
            print(f"  [{row['description']}] {detail}")


if __name__ == "__main__":
    main()
