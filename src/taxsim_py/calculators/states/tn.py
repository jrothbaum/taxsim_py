"""Tennessee Hall income tax calculator."""

import polars as pl

from taxsim_py.engine.inputs import aged_count, files_joint, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import household_income, with_state_detail, dividend_input_adjustment
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

TN_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "tn" / "income_tax.yaml")


def compute_tn_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Tennessee income tax on interest and dividends for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(TN_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = df.with_columns(tn_household_income=household_income())
    df = deflate_for_extrapolation(df, flate, extra=("tn_household_income",))

    rate = p.num("rate")
    taxpayers = taxpayer_count()
    income = pl.col("dividends") + dividend_adjustment + pl.col("intrec")
    exemp = pl.lit(0.0)
    if y <= 1985:
        taxinc = income
        statax = pl.when(income <= p["minimum_income_1985"] * taxpayers).then(0.0).otherwise(rate * income)
    elif y <= 2020:
        exemp = p["exemption"] * taxpayers
        taxinc = (income - exemp).clip(0, None)
        statax = rate * taxinc
    else:
        # The tax is repealed; TAXSIM leaves the worksheet unset.
        taxinc = pl.lit(0.0)
        statax = pl.lit(0.0)
    limits = p["aged_household_income_limit"]
    is_joint = files_joint()
    limit = pl.when(is_joint).then(float(resolve_year(limits["married_joint"], y))).otherwise(
        float(resolve_year(limits["single"], y))
    )
    hh = pl.col("tn_household_income")
    exempt = hh <= limit if y <= 1985 else hh < limit
    statax = pl.when((aged_count() > 0) & exempt).then(0.0).otherwise(statax)

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(df, agi=income, exemptions=exemp, taxable_income=taxinc, rate=pl.lit(rate))
