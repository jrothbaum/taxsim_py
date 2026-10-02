"""Washington: no income tax; the refundable Working Families Tax Credit (statutory mode)."""

import polars as pl

from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

WA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "wa" / "income_tax.yaml")


def compute_wa_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Return the negative of the Working Families Tax Credit, or zero in TAXSIM mode."""
    if behavior.mode.value != "statutory" or year < 2022:
        return df.with_columns(siitax=pl.lit(0.0))
    amounts = [float(v) for v in resolve_year(WA_PARAMS["working_families_credit_amount"], year)]
    below = [float(v) for v in WA_PARAMS["working_families_credit_phaseout_start_below_ceiling"]]
    minimum = float(WA_PARAMS["working_families_credit_minimum"])

    children = pl.col("num_children").clip(0, 3)
    maximum = pl.lit(0.0)
    reduction_band = pl.lit(1.0)
    for count in range(3, -1, -1):
        maximum = pl.when(children == count).then(amounts[count]).otherwise(maximum)
        reduction_band = pl.when(children == count).then(below[count]).otherwise(reduction_band)

    # Income ceiling of the federal EITC (the credit ends where the EITC does).
    ceiling = pl.col("phaseout_start") + pl.col("max_credit") / pl.col("rate_out")
    income = pl.max_horizontal(pl.col("earned_income"), pl.col("agi"))
    reduced = maximum - (income - (ceiling - reduction_band)).clip(0, None) * maximum / reduction_band
    credit = pl.when(reduced > 0).then(pl.max_horizontal(pl.lit(minimum), reduced.round(0))).otherwise(0.0)
    eligible = (pl.col("eitc") > 0) & (income < ceiling) & (pl.col("earned_income") > 0)
    return df.with_columns(siitax=-pl.when(eligible).then(credit).otherwise(0.0))
