"""Unified validation runner for state income tax calculators, mirroring
scripts/validate_federal.py's pattern (one test-case table per state, one
shared runner here). Add a new state by writing `tests/{st}_cases.py`
(`build_{st}_test_cases()`) and `calculators/states/{st}.py`
(`compute_{st}_tax(df, year) -> pl.DataFrame`, adding a `siitax` column),
then register both below.
"""

import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

from taxsim_py import calculate_taxes  # noqa: E402
from taxsim_py.calculators.states import get_state_calculator  # noqa: E402
from taxsim_py.engine.detail import TAXSIM_STATE_DETAIL_COLUMNS as STATE_DETAIL_COLUMNS  # noqa: E402
from taxsim_py.engine import federal_state  # noqa: E402
from oracle import TAXSIM_EXE, knife_edge_ids, run_oracle  # noqa: E402
from state_new_inputs import build_new_input_cases  # noqa: E402

# The state tax, its marginal rate, and TAXSIM's detailed state outputs.
COMPARED_COLUMNS = ["siitax", "srate", *STATE_DETAIL_COLUMNS]
RATE_COLUMNS = ["srate", "v41"]
# Federal tax, payroll tax and the standard deduction (v13), which states read.
FEDERAL_COLUMNS = ["fiitax", "fica", "v13"]
OREGON = 38
MISSOURI = 26
MISMATCH_TOLERANCE = 0.015
DETAIL_RELATIVE_TOLERANCE = 1e-6


# Registry: state code -> (test-case builder, calculator, YEARS to run).
def _load_states() -> dict:
    """Validation states from `tests/<xx>_cases.py`, in TAXSIM code order.

    Each module defines `STATE_<XX>` (the TAXSIM code), `build_<xx>_test_cases`
    and `YEARS`, and may define `shared_case_divergent(row)` to mark shared
    new-input cases that hit a known oracle divergence; the calculator comes
    from the public API's registry.
    """
    found = []
    for path in (ROOT / "tests").glob("*_cases.py"):
        prefix = path.stem.removesuffix("_cases")
        if prefix == "federal":
            continue
        module = importlib.import_module(path.stem)
        abbreviation = prefix.upper()
        code = getattr(module, f"STATE_{abbreviation}")
        build = getattr(module, f"build_{prefix}_test_cases")
        found.append((code, abbreviation, (build, get_state_calculator(code), module.YEARS)))
        STATE_CODES[abbreviation] = code
        if hasattr(module, "shared_case_divergent"):
            SHARED_CASE_DIVERGENCE[abbreviation] = module.shared_case_divergent
    return {abbreviation: entry for _, abbreviation, entry in sorted(found)}


STATE_CODES: dict[str, int] = {}
SHARED_CASE_DIVERGENCE: dict = {}


STATES = _load_states()


def run_taxsim_exe(cases: pl.DataFrame) -> pl.DataFrame:
    out = run_oracle(cases, idtl=2)
    return out.select(
        "taxsimid",
        *[pl.col(c).alias(f"{c}_expected") for c in COMPARED_COLUMNS],
        *[pl.col(c).alias(f"oracle_{c}") for c in FEDERAL_COLUMNS],
    )


# In 2023 this project uses real federal parameters where the oracle's are
# known to be wrong (see validate_federal.KNOWN_ORACLE_DIVERGENT_YEARS: the
# Social Security wage base, EITC amounts and others). A 2023 case whose
# federal income tax, payroll tax or standard deduction differs from the
# oracle's carries that difference into the state results, so it counts as an
# expected difference.
_FEDERAL_DIVERGENT_YEARS = {2023}
_FEDERAL_TOLERANCE = 0.015


def _federal_divergence() -> pl.Expr:
    differs = pl.any_horizontal(
        [(pl.col(c) - pl.col(f"oracle_{c}")).abs() > _FEDERAL_TOLERANCE for c in FEDERAL_COLUMNS]
    )
    return pl.col("year").is_in(_FEDERAL_DIVERGENT_YEARS) & differs


def _oracle_last() -> pl.Expr:
    """Records the oracle must run after all others.

    The oracle keeps Oregon's 2019 surplus credit in a shared variable and
    subtracts it from every later Oregon record, and Missouri's 2018 law
    overwrites the top rate of the table earlier years use (both logged as
    TAXSIM errors).
    """
    return ((pl.col("state") == OREGON) & (pl.col("year") == 2019)) | (
        (pl.col("state") == MISSOURI) & (pl.col("year") == 2018)
    )


def _close(column: str) -> pl.Expr:
    return (pl.col(column) - pl.col(f"{column}_expected")).abs() <= MISMATCH_TOLERANCE


def main() -> None:
    total = 0
    total_failures = 0
    selected = set(a.upper() for a in sys.argv[1:]) or None
    states_to_run = {k: v for k, v in STATES.items() if selected is None or k in selected}

    # Every selected state's cases form one batch with unique ids: one oracle
    # run and one `calculate_taxes` call, which shares each year's federal
    # pass across states and resolves years concurrently.
    parts = []
    for name, (build, _, years) in states_to_run.items():
        shared = build_new_input_cases(STATE_CODES[name], name, years)
        if name in SHARED_CASE_DIVERGENCE:
            for row in shared:
                row["oracle_divergent"] = row.get("oracle_divergent", False) or SHARED_CASE_DIVERGENCE[name](row)
        rows = build() + shared
        cases = pl.DataFrame(rows, infer_schema_length=None).filter(pl.col("year").is_in(years))
        parts.append(cases.drop("taxsimid", strict=False).with_columns(validation_state=pl.lit(name)))
    batch = pl.concat(parts, how="diagonal_relaxed").with_row_index("taxsimid", offset=1)

    oracle_order = batch.sort(_oracle_last(), maintain_order=True)
    with ThreadPoolExecutor(max_workers=1) as oracle:
        expected_future = oracle.submit(run_taxsim_exe, oracle_order)
        results = calculate_taxes(batch, mtr=85, idtl=2, taxsim_names=True).select(
            "taxsimid", "description", *COMPARED_COLUMNS, *FEDERAL_COLUMNS
        )
        expected = expected_future.result()

    # Rate-only mismatches that vanish a dollar away are TAXSIM round-off.
    rate_only = results.join(expected, on="taxsimid").filter(
        pl.all_horizontal(_close(c) for c in COMPARED_COLUMNS if c not in RATE_COLUMNS),
        ~pl.all_horizontal(_close(c) for c in RATE_COLUMNS),
    )
    knife_edges = knife_edge_ids(
        batch.join(rate_only.select("taxsimid"), on="taxsimid").sort(_oracle_last(), maintain_order=True),
        lambda frame: calculate_taxes(frame, mtr=85, idtl=2, taxsim_names=True),
        RATE_COLUMNS,
    )
    total_knife_edges = 0

    # Worksheet-only mismatches on records whose itemizing and standard
    # totals tie exactly: TAXSIM's round-off decides which worksheet it
    # prints. They count as round-off when itemizing on ties reproduces it.
    worksheet_only = results.join(expected, on="taxsimid").filter(
        pl.all_horizontal(_close(c) for c in ("siitax", "srate")),
        ~pl.all_horizontal(_close(c) for c in STATE_DETAIL_COLUMNS),
    )
    itemize_ties: set[int] = set()
    if not worksheet_only.is_empty():
        tie_frame = batch.join(worksheet_only.select("taxsimid"), on="taxsimid")
        tie_tolerance = federal_state._TIE_TOLERANCE
        federal_state._TIE_TOLERANCE = -tie_tolerance
        try:
            itemized = calculate_taxes(tie_frame, mtr=85, idtl=2, taxsim_names=True).select(
                "taxsimid", *COMPARED_COLUMNS
            )
        finally:
            federal_state._TIE_TOLERANCE = tie_tolerance
        itemize_ties = set(
            itemized.join(expected, on="taxsimid")
            .filter(pl.all_horizontal(_close(c) for c in COMPARED_COLUMNS))
            .get_column("taxsimid")
            .to_list()
        )
    total_itemize_ties = 0
    # A tax mismatch on a record sitting exactly on a threshold where TAXSIM
    # computes in single precision (for example the pre-1987 two-earner
    # deduction's `.1*wife`) is round-off when the records a dollar above and
    # a dollar below both agree.
    tax_mismatch = results.join(expected, on="taxsimid").filter(~_close("siitax"))
    if not tax_mismatch.is_empty():
        edge_frame = batch.join(tax_mismatch.select("taxsimid"), on="taxsimid").sort(_oracle_last(), maintain_order=True)
        columns = list(dict.fromkeys(COMPARED_COLUMNS))
        above = knife_edge_ids(
            edge_frame,
            lambda frame: calculate_taxes(frame, mtr=85, idtl=2, taxsim_names=True),
            columns,
        )
        below = knife_edge_ids(
            edge_frame,
            lambda frame: calculate_taxes(frame, mtr=85, idtl=2, taxsim_names=True),
            columns,
            shift=-1.0,
        )
        knife_edges |= above & below
    # The remaining worksheet-only mismatches: TAXSIM prints the worksheet of
    # its downward marginal-rate run when single-precision round-off at an
    # exact threshold puts the upward rate out of range; counted as round-off
    # when they agree a dollar away.
    if not worksheet_only.is_empty():
        edge_frame = batch.join(worksheet_only.select("taxsimid"), on="taxsimid").filter(
            ~pl.col("taxsimid").is_in(list(itemize_ties))
        )
        knife_edges |= knife_edge_ids(
            edge_frame.sort(_oracle_last(), maintain_order=True),
            lambda frame: calculate_taxes(frame, mtr=85, idtl=2, taxsim_names=True),
            list(dict.fromkeys([*RATE_COLUMNS, *STATE_DETAIL_COLUMNS])),
        )

    for state_name in states_to_run:
        cases = batch.filter(pl.col("validation_state") == state_name)
        comparison = results.join(cases.select("taxsimid"), on="taxsimid").join(expected, on="taxsimid")
        # Cases marked `oracle_divergent` use real-law values where the
        # oracle's own parameter is known to be wrong; their mismatches are
        # counted in one summary line instead of printed as failures.
        flags = cases.select(
            "taxsimid",
            "year",
            case_divergent=(
                pl.col("oracle_divergent").fill_null(False) if "oracle_divergent" in cases.columns else pl.lit(False)
            ),
        )
        comparison = comparison.join(flags, on="taxsimid").with_columns(
            oracle_divergent=pl.col("case_divergent") | _federal_divergence()
        )
        # A missing result counts as a mismatch.
        diff_exprs = [
            (
                (pl.col(c) - pl.col(f"{c}_expected")).abs()
                # Detail outputs of projected years carry the oracle's
                # round-off from deflating large amounts.
                - (DETAIL_RELATIVE_TOLERANCE * pl.col(f"{c}_expected").abs() if c in STATE_DETAIL_COLUMNS else 0.0)
            ).fill_null(float("inf")).fill_nan(float("inf")).alias(f"{c}_diff")
            for c in COMPARED_COLUMNS
        ]
        comparison = comparison.with_columns(diff_exprs)
        is_mismatch = pl.any_horizontal([pl.col(f"{c}_diff") > MISMATCH_TOLERANCE for c in COMPARED_COLUMNS])
        divergent = comparison.filter(is_mismatch & pl.col("oracle_divergent"))
        mismatches = comparison.filter(is_mismatch & ~pl.col("oracle_divergent"))
        knife = mismatches.filter(pl.col("taxsimid").is_in(list(knife_edges)))
        mismatches = mismatches.filter(~pl.col("taxsimid").is_in(list(knife_edges)))
        ties = mismatches.filter(pl.col("taxsimid").is_in(list(itemize_ties)))
        mismatches = mismatches.filter(~pl.col("taxsimid").is_in(list(itemize_ties)))

        print(f"[{state_name}] Ran {comparison.height} test cases across {COMPARED_COLUMNS}.")
        if not divergent.is_empty():
            print(
                f"[{state_name}] {divergent.height} mismatches are in cases using real-law values "
                "where the oracle is known to be wrong - expected, not printed individually."
            )
        if not knife.is_empty():
            print(
                f"[{state_name}] {knife.height} rate or worksheet mismatches sit on a threshold where "
                "TAXSIM's single-precision round-off shows and agree a dollar away - not printed individually."
            )
        if not ties.is_empty():
            print(
                f"[{state_name}] {ties.height} worksheet mismatches are exact itemizing/standard ties that "
                "TAXSIM's round-off breaks toward itemizing - not printed individually."
            )
        total_knife_edges += knife.height
        total_itemize_ties += ties.height
        total += comparison.height
        total_failures += mismatches.height
        if mismatches.is_empty():
            print(f"[{state_name}] All cases match {TAXSIM_EXE.name} exactly.")
        else:
            print(f"[{state_name}] {mismatches.height} FAILURES:")
            for row in mismatches.iter_rows(named=True):
                failed = [c for c in COMPARED_COLUMNS if row[f"{c}_diff"] > MISMATCH_TOLERANCE]
                detail = ", ".join(f"{c}: got {row[c]}, expected {row[f'{c}_expected']}" for c in failed)
                print(f"  [{row['description']}] {detail}")

    print(
        f"\nTotal: {total} cases, {total_failures} failures across {len(states_to_run)} state(s) "
        f"({total_knife_edges} threshold round-off mismatches and {total_itemize_ties} itemizing ties "
        "not counted)."
    )


if __name__ == "__main__":
    main()
