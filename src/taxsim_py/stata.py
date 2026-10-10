"""The taxsim_py Stata command's calculation: taxsim35's inputs in, taxsim35's outputs out.

Stata saves the input variables to a .dta file and calls `run_from_stata()` in its own
Python; the results come back as a .dta file (with taxsim35's variable labels) and, when
asked for, a file of any type `write_table` writes.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from taxsim_py.api import calculate_taxes
from taxsim_py.engine.detail import TAXSIM_FEDERAL_DETAIL_COLUMNS, TAXSIM_STATE_DETAIL_COLUMNS
from taxsim_py.engine.inputs import COUNT_INPUTS, DEPENDENT_DEFAULT_INPUTS, TAXSIM_INPUTS
from taxsim_py.io.tables import read_table, write_table

# Inputs read from the Stata data. `idtl` and `mtr` are set by the command's options, as in
# taxsim35, so variables of those names in the data are not used.
STATA_INPUTS = tuple(c for c in TAXSIM_INPUTS if c not in ("idtl", "mtr"))
# Inputs that must hold whole numbers (codes and counts).
INTEGER_INPUTS = ("year", "state", "mstat", *COUNT_INPUTS, *DEPENDENT_DEFAULT_INPUTS)
# taxsim35's outputs, in its order; `full` adds the detailed worksheets.
OUTPUTS = ("taxsimid", "year", "state", "fiitax", "siitax", "fica", "frate", "srate", "ficar", "tfica")
FULL_OUTPUTS = (*TAXSIM_FEDERAL_DETAIL_COLUMNS, *TAXSIM_STATE_DETAIL_COLUMNS)

# taxsim35's variable labels (taxsimlocal35.ado), plus labels for outputs it leaves unlabelled.
LABELS = {
    "fiitax": "Federal Income Tax",
    "siitax": "State Income Tax",
    "fica": "OASDI and HI Payroll Tax",
    "frate": "IIT marginal rate",
    "srate": "state marginal rate",
    "ficar": "SS marginal rate",
    "tfica": "Taxpayer share of payroll tax",
    "credits": "Federal Nonrefundable Credits",
    "v10": "Federal AGI",
    "v11": "UI in AGI",
    "v12": "Social Security in AGI",
    "v13": "Zero Bracket Amount",
    "v14": "Personal Exemptions",
    "v15": "Exemption Phaseout",
    "v16": "Deduction Phaseout",
    "v17": "Deductions allowed",
    "v18": "Federal Taxable Income",
    "v19": "Federal Regular Tax",
    "v20": "Exemption Surtax",
    "v21": "General Tax Credit",
    "v22": "Child Tax Credit (as adjusted)",
    "v23": "Refundable Part of Child Tax Credit",
    "v24": "Child Care Credit",
    "v25": "Earned Income Credit",
    "v26": "Income for the Alternative Minimum Tax",
    "v27": "AMT Liability (addition to regular tax)",
    "v28": "Income Tax before Credits",
    "v29": "FICA",
    "v30": "State Household Income",
    "v31": "State Rent Payments",
    "v32": "State AGI",
    "v33": "State Exemption amount",
    "v34": "State Standard Deduction",
    "v35": "State Itemized Deductions",
    "v36": "State Taxable Income",
    "v37": "State Property Tax Credit",
    "v38": "State Child Care Credit",
    "v39": "State EITC",
    "v40": "State Total Credits",
    "v41": "State Bracket Rate",
    "v42": "Earned Self-Employment Income for FICA",
    "v43": "Medicare Tax on Unearned Income",
    "v44": "Medicare Tax on Earned Income",
    "v45": "CARES act Recovery Rebates",
    "staxbc": "State Tax before Credits",
}


class StataInputError(ValueError):
    """Input data the calculation cannot use, described for a Stata user."""


def _rows(mask: pl.Series) -> str:
    rows = (mask.arg_true() + 1).to_list()  # Stata observation numbers
    shown = ", ".join(str(r) for r in rows[:5])
    return f"{len(rows)} observation{'s' if len(rows) != 1 else ''} (first: {shown})"


def prepare_inputs(frame: pl.DataFrame, *, missing_to_zero: bool = False) -> pl.DataFrame:
    """taxsim35's input variables, as numbers, ready for `calculate_taxes`.

    Strings that convert to numbers without loss are converted; codes and counts must be
    whole numbers. Missing values are an error unless `missing_to_zero`, which sets them
    to 0. `taxsimid` only identifies rows, so it passes through as it is (a number or a
    string) and must never be missing. Absent variables are left to
    `calculate_taxes`, which fills them as TAXSIM does; an absent `taxsimid` is the
    observation number and an absent `state` is 0 (no state tax).
    """
    names = [c for c in STATA_INPUTS if c in frame.columns]
    problems: list[str] = []
    columns: list[pl.Series] = []
    for name in names:
        series = frame.get_column(name)
        if name == "taxsimid":
            missing = series.is_null()
            if series.dtype == pl.String:
                missing = missing | (series.str.strip_chars() == "")
            elif series.dtype.is_float():
                missing = missing | series.is_nan()
            if missing.any():
                problems.append(f"taxsimid has missing values in {_rows(missing)}")
            columns.append(series)
            continue
        if series.dtype == pl.String:
            text = series.str.strip_chars()
            series = text.cast(pl.Float64, strict=False)
            unconvertible = series.is_null() & text.is_not_null() & (text != "")
            if unconvertible.any():
                problems.append(f"{name} has text that is not a number in {_rows(unconvertible)}")
        elif not series.dtype.is_numeric():
            problems.append(f"{name} must be numeric (it is {series.dtype})")
            continue
        if series.dtype.is_float():
            series = series.fill_nan(None)
        if name in INTEGER_INPUTS and series.dtype.is_float():
            fractional = series.is_not_null() & (series != series.round(0))
            if fractional.any():
                problems.append(f"{name} must be a whole number but is not in {_rows(fractional)}")
        missing = series.is_null()
        if missing.any():
            if missing_to_zero:
                series = series.fill_null(0)
            else:
                problems.append(f"{name} has missing values in {_rows(missing)} (or use the missing_to_zero option)")
        columns.append(series.alias(name))
    if problems:
        raise StataInputError("\n".join(problems))
    inputs = pl.DataFrame(columns) if columns else pl.DataFrame(schema={})
    if "taxsimid" not in inputs.columns:
        inputs = inputs.with_row_index("taxsimid", offset=1)
    if "state" not in inputs.columns:
        inputs = inputs.with_columns(state=pl.lit(0))
    return inputs


def calculate_for_stata(
    frame: pl.DataFrame,
    *,
    mtr: int = 85,
    full: bool = False,
    statutory: bool = False,
    missing_to_zero: bool = False,
    max_workers: int | None = None,
) -> pl.DataFrame:
    """taxsim35's outputs for `frame`, its dollar amounts rounded to cents as TAXSIM reports them."""
    inputs = prepare_inputs(frame, missing_to_zero=missing_to_zero)
    result = calculate_taxes(
        inputs,
        mtr=mtr,
        idtl=2 if full else None,
        taxsim_names=True,
        calculation_mode="statutory" if statutory else "taxsim",
        max_year_workers=max_workers,
    )
    wanted = (*OUTPUTS, *(FULL_OUTPUTS if full else ()))
    return result.select(
        pl.col(c).round(2) if result.schema[c].is_float() and c not in ("taxsimid", "year", "state") else pl.col(c)
        for c in wanted
        if c in result.columns
    )


def write_results(results: pl.DataFrame, path: str | Path) -> None:
    """Write `results`; a .dta file carries taxsim35's variable labels."""
    if Path(path).suffix.lower() == ".dta":
        import polars_readstat

        labels = {c: LABELS[c] for c in results.columns if c in LABELS}
        polars_readstat.write_readstat(results, str(path), format="dta", variable_labels=labels)
    else:
        write_table(results, path)


def run_stata_files(
    input_path: str | Path,
    output_paths: list[str | Path],
    **options,
) -> int:
    """Read the inputs Stata saved, calculate, write each output file; returns the row count."""
    results = calculate_for_stata(read_table(input_path), **options)
    for path in output_paths:
        write_results(results, path)
    return results.height


def run_from_stata() -> None:
    """Called by taxsim_py.ado through Stata's Python, with its settings in the caller's locals.

    Reads `tpy_input`, `tpy_outputs` (paths separated by `|`), `tpy_mtr`, `tpy_full`,
    `tpy_statutory`, `tpy_missing` and `tpy_workers`; sets `tpy_rows`, or `tpy_error` with a
    message for the user.
    """
    from sfi import Macro

    def local(name: str) -> str:
        return Macro.getLocal(name) or ""

    try:
        workers = int(local("tpy_workers") or 0)
        rows = run_stata_files(
            local("tpy_input"),
            [p for p in local("tpy_outputs").split("|") if p],
            mtr=int(local("tpy_mtr") or 85),
            full=local("tpy_full") == "1",
            statutory=local("tpy_statutory") == "1",
            missing_to_zero=local("tpy_missing") == "1",
            max_workers=workers or None,
        )
        Macro.setLocal("tpy_rows", str(rows))
    except (ValueError, NotImplementedError, ImportError, OSError) as error:
        Macro.setLocal("tpy_error", str(error))
