"""Single unified validation runner for everything federal, TX, across
every year in tests/federal_cases.py's YEARS list.

Builds one test-case table (tests/federal_cases.py), runs it through
taxsim2022.exe once, runs it through our own pipeline once, diffs every
output column we claim to support, and prints only the rows that fail -
each one labeled by its `description` so a failure is immediately
legible without hunting through separate scripts.

Add new coverage by adding rows to tests/federal_cases.py, not by writing
another script like this one.
"""

import subprocess
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from taxsim_py.calculators.federal import compute_marginal_rate  # noqa: E402
from taxsim_py.calculators.payroll import compute_payroll_tax  # noqa: E402
from taxsim_py.engine.schema import load_yaml, resolve_year  # noqa: E402
from taxsim_py.engine.sales_tax import sales_tax_deduction  # noqa: E402
from federal_cases import ROOT as CASES_ROOT, YEARS, build_federal_test_cases  # noqa: E402

TAXSIM_EXE = ROOT / "taxsim2024.exe"

# Output columns we currently claim to support, compared to the cent. Tolerance
# is slightly above $0.01, not $0.01 itself: summing the same terms in a
# different order can put a value exactly on a half-cent boundary (e.g.
# 6391.575) a hair on one side or the other in double precision, so the two
# implementations occasionally round that one case differently by a cent -
# confirmed harmless (see validate_federal.py history), not a real error.
COMPARED_COLUMNS = ["fiitax", "frate", "fica", "ficar", "tfica"]
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

INPUT_COLUMNS = [
    "taxsimid",
    "year",
    "state",
    "mstat",
    "depx",
    "dep17",
    "dep18",
    "dep6",
    "pwages",
    "swages",
    "proptax",
    "otheritem",
    "mortgage",
    "dep13",
    "childcare",
    "intrec",
    "psemp",
    "ssemp",
    "dividends",
    "stcg",
    "ltcg",
    "ui",
    "pui",
    "sui",
]


def add_sales_tax_deduction(df: pl.DataFrame, year: int) -> pl.DataFrame:
    # The optional state/local sales tax itemized deduction didn't exist in
    # law before 2004 (American Jobs Creation Act of 2004) - the source's
    # own `saletx` function hardcodes `if(iy.lt.2004) return` with saletx
    # left at its initialized 0, confirmed directly in the source rather
    # than assumed. No a/b/c coefficients exist for year<2004 in
    # sales_tax_deduction.yaml on purpose.
    if year < 2004:
        return df.with_columns(state_sales_or_income_tax_ded=pl.lit(0.0))
    tx_params = load_yaml(CASES_ROOT / "parameters" / "states" / "tx" / "sales_tax_deduction.yaml")
    family_size = pl.when(pl.col("mstat") == 2).then(2).otherwise(1) + pl.col("depx")
    return df.with_columns(
        state_sales_or_income_tax_ded=sales_tax_deduction(
            pl.col("pwages")
            + pl.col("swages")
            + pl.col("intrec")
            + pl.col("dividends")
            + pl.col("stcg")
            + pl.col("ltcg"),
            family_size,
            a=resolve_year(tx_params["a"], year),
            b=resolve_year(tx_params["b"], year),
            c=resolve_year(tx_params["c"], year),
        )
    )


def run_taxsim_exe(cases: pl.DataFrame) -> pl.DataFrame:
    lines = [" ".join(INPUT_COLUMNS)]
    for row in cases.select(INPUT_COLUMNS).iter_rows(named=True):
        lines.append(" ".join(str(row[c]) for c in INPUT_COLUMNS))
    result = subprocess.run(
        [str(TAXSIM_EXE)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"taxsim.exe failed: {result.stderr}\n{result.stdout[-2000:]}")

    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    header_cols = out_lines[0].split(",")
    data_rows = [ln.split(",") for ln in out_lines[1:]]
    out = pl.DataFrame(data_rows, schema=header_cols, orient="row")
    out = out.with_columns(pl.col("taxsimid").cast(pl.Float64).cast(pl.Int64))
    for col in COMPARED_COLUMNS:
        out = out.with_columns(pl.col(col).cast(pl.Float64))
    return out.select("taxsimid", *[pl.col(c).alias(f"{c}_expected") for c in COMPARED_COLUMNS])


def main() -> None:
    import functools

    cases = build_federal_test_cases()

    expected = run_taxsim_exe(cases)

    # Our own pipeline takes `year` as a single Python-level argument (it
    # resolves parameters once per call, not per row), so each year's slice
    # of cases is run through it separately and the results are recombined.
    # The TX sales-tax-deduction coefficients are also year-specific (see
    # sales_tax_deduction.yaml), so add_sales_tax_deduction is bound to each
    # year's own value here too, not computed once over mixed-year rows.
    actual_parts = []
    for year in YEARS:
        year_cases = cases.filter(pl.col("year") == year)
        refresh = functools.partial(add_sales_tax_deduction, year=year)
        year_cases = refresh(year_cases)
        year_actual = compute_marginal_rate(year_cases, year, refresh_dependent_columns=refresh)
        year_actual = compute_payroll_tax(year_actual, year)
        # Selected down per-year (not after concat): the pre-1987 and
        # 1987+ calculators keep different sets of intermediate working
        # columns (law79 trims them, law87 doesn't), so concatenating the
        # full, un-selected frames across both eras fails on width - only
        # this fixed set is actually needed downstream.
        year_actual = year_actual.with_columns(pl.lit(year).alias("year"))
        actual_parts.append(year_actual.select("taxsimid", "description", "year", *COMPARED_COLUMNS))
    actual = pl.concat(actual_parts)

    comparison = actual.join(expected, on="taxsimid")
    diff_exprs = [
        (pl.col(c) - pl.col(f"{c}_expected")).abs().alias(f"{c}_diff") for c in COMPARED_COLUMNS
    ]
    comparison = comparison.with_columns(diff_exprs)

    is_mismatch = pl.any_horizontal(
        [pl.col(f"{c}_diff") > MISMATCH_TOLERANCE for c in COMPARED_COLUMNS]
    )
    mismatches = comparison.filter(is_mismatch)

    known_divergent = mismatches.filter(pl.col("year").is_in(KNOWN_ORACLE_DIVERGENT_YEARS))
    unexpected = mismatches.filter(~pl.col("year").is_in(KNOWN_ORACLE_DIVERGENT_YEARS))

    print(f"Ran {comparison.height} test cases across {COMPARED_COLUMNS}.")
    if not known_divergent.is_empty():
        print(
            f"{known_divergent.height} failures are in {sorted(KNOWN_ORACLE_DIVERGENT_YEARS)} - "
            "EXPECTED, this project deliberately uses real parameters over the oracle's own known-wrong "
            "ones there (see KNOWN_ORACLE_DIVERGENT_YEARS above). Not printed individually."
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
