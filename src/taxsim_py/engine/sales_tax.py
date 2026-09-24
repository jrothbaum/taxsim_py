"""Optional state and local sales tax deduction calculations."""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml


def sales_tax_deduction(
    household_resources: pl.Expr, family_size: pl.Expr, a: float, b: float, c: float
) -> pl.Expr:
    return (
        pl.when((household_resources > 0) & (family_size > 0))
        .then((a + b * household_resources.log() + c * family_size.log()).exp())
        .otherwise(0.0)
    )


_SALES_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "sales_tax_deduction.yaml")
_CPI_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_cpi_extrapolation.yaml")


def state_sales_tax_deduction(
    household_resources: pl.Expr, family_size: pl.Expr, state_code: pl.Expr, year: int
) -> pl.Expr:
    """TAXSIM's estimated general sales tax deduction (`saletx`) for each state."""
    if year < int(_SALES_TAX_PARAMS["first_year"]):
        return pl.lit(0.0)
    coefficient_year = min(year, int(_SALES_TAX_PARAMS["last_coefficient_year"]))
    xndxa = _CPI_PARAMS["xndxa"]
    factor = float(xndxa[coefficient_year]) / float(xndxa[year])
    coefficients = _SALES_TAX_PARAMS["coefficients"][coefficient_year]
    a = b = c = pl.lit(0.0)
    for code, (a_value, b_value, c_value) in coefficients.items():
        is_state = state_code == int(code)
        a = pl.when(is_state).then(pl.lit(float(a_value))).otherwise(a)
        b = pl.when(is_state).then(pl.lit(float(b_value))).otherwise(b)
        c = pl.when(is_state).then(pl.lit(float(c_value))).otherwise(c)
    in_table = state_code.is_between(1, len(coefficients))
    deflated = factor * household_resources
    return (
        pl.when(in_table & (household_resources > 0) & (family_size > 0))
        .then((a + b * deflated.log() + c * family_size.log()).exp() / factor)
        .otherwise(0.0)
    )
