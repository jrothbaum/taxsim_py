"""New Hampshire interest and dividends tax calculator."""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NH_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nh" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(NH_PARAMS[name], year))


def compute_nh_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate New Hampshire interest and dividends tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(df, ("dividends", "intrec"))
    dividend_adjustment = float(
        resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], y)
    )
    df = df.with_columns(nh_income=pl.col("dividends") + dividend_adjustment + pl.col("intrec"))
    df = deflate_for_extrapolation(df, flate, ["nh_income"])

    taxpayers = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
    taxinc = (pl.col("nh_income") - taxpayers * _p("exemption_per_taxpayer", y)).clip(0, None)
    return df.with_columns(siitax=_p("rate", y) * taxinc * flate)
