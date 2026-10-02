"""New Hampshire interest and dividends tax calculator."""

import polars as pl

from taxsim_py.engine.inputs import aged_count, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import with_state_detail, dividend_input_adjustment
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NH_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nh" / "income_tax.yaml")


def compute_nh_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate New Hampshire interest and dividends tax for each row."""
    state_year = "nh" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(NH_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = df.with_columns(nh_income=pl.col("dividends") + dividend_adjustment + pl.col("intrec"))
    df = deflate_for_extrapolation(df, flate, extra=("nh_income",))

    exemption = taxpayer_count() * p.num("exemption_per_taxpayer") + aged_count() * p.num("exemption_per_aged_taxpayer")
    taxinc = (pl.col("nh_income") - exemption).clip(0, None)
    rate = p.num("rate")
    return with_state_detail(
        df.with_columns(siitax=rate * taxinc * flate),
        agi=pl.col("nh_income"),
        exemptions=exemption,
        taxable_income=taxinc,
        rate=rate,
    )
