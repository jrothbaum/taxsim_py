"""Washington: no income tax; the refundable Working Families Tax Credit (statutory mode)."""

import polars as pl

from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.eitc import eitc_age_eligible
from taxsim_py.engine.inputs import aged_count, is_dependent_filer
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

WA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "wa" / "income_tax.yaml")


def compute_wa_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Return the negative of the Working Families Tax Credit, or zero in TAXSIM mode."""
    effective_year, flate = resolve_state_year(year, behavior, WA_PARAMS)
    p = YearParams(WA_PARAMS, effective_year)
    # In a projected year the last coded law runs on incomes deflated by the projection factor, and the credit is inflated back.
    df = deflate_for_extrapolation(df, flate)
    if not behavior.add_washington_working_families_credit or not p.in_effect("working_families_credit_amount"):
        return df.with_columns(siitax=pl.lit(0.0))
    amounts = [float(v) for v in p.value("working_families_credit_amount")]
    below = [float(v) for v in WA_PARAMS["working_families_credit_phaseout_start_below_ceiling"]]
    minimum = float(WA_PARAMS["working_families_credit_minimum"])

    children = pl.col("num_children").clip(0, 3)
    maximum = pl.lit(0.0)
    reduction_band = pl.lit(1.0)
    for count in range(3, -1, -1):
        maximum = pl.when(children == count).then(amounts[count]).otherwise(maximum)
        reduction_band = pl.when(children == count).then(below[count]).otherwise(reduction_band)

    # Income ceiling: the federal EITC maximum qualifying income (the Department's published table from 2022 to 2025).
    ceiling = pl.col("phaseout_start") + pl.col("max_credit") / pl.col("rate_out")
    if p.in_effect("working_families_credit_income_ceiling"):
        table = p.value("working_families_credit_income_ceiling")
        published = pl.lit(0.0)
        for count in range(3, -1, -1):
            joint = float(table["joint"][count])
            single = float(table["single"][count])
            published = pl.when(children == count).then(
                pl.when(pl.col("filing_status") == "married_joint").then(joint).otherwise(single)
            ).otherwise(published)
        ceiling = published
    income = pl.max_horizontal(pl.col("earned_income"), pl.col("agi"))
    if behavior.phase_out_washington_working_families_credit_per_statute:
        # RCW 82.08.0206(3)(b): the credit falls from its maximum starting 5,000 (2,500 with no children) below the ceiling,
        # at a rate set each year so the minimum credit is reached at the ceiling; rounded to the nearest dollar.
        reduced = maximum - (income - (ceiling - reduction_band)).clip(0, None) * (maximum - minimum) / reduction_band
        credit = pl.max_horizontal(pl.lit(minimum), reduced.round(0, mode="half_away_from_zero"))
        eligible = (pl.col("eitc") > 0) & (income < ceiling) & (pl.col("earned_income") > 0)
    else:
        # TAXSIM: its own rates from the same start, on federal AGI, with a floor of the minimum below the ceiling.
        rates = [float(v) for v in p.value("taxsim_working_families_credit_phaseout_rate")] if p.in_effect(
            "taxsim_working_families_credit_phaseout_rate"
        ) else [m / b for m, b in zip(amounts, below)]
        rate = pl.lit(0.0)
        for count in range(3, -1, -1):
            rate = pl.when(children == count).then(rates[count]).otherwise(rate)
        reduced = (maximum - (pl.col("agi") - (ceiling - reduction_band)).clip(0, None) * rate).clip(0, None)
        credit = pl.when(pl.col("agi") < ceiling).then(pl.max_horizontal(pl.lit(minimum), reduced)).otherwise(reduced)
        # Eligibility: earnings of a taxpayer aged 25 to 64 (or any taxpayer with children or one 65 or older), not a dependent
        # filer, under the federal investment income limit; it does not need a positive federal credit.
        in_range = lambda age: (age >= 25) & (age <= 64)  # noqa: E731
        any_child_or_aged = (pl.col("depx") > 0) | (aged_count() > 0)
        primary_ok = in_range(pl.col("page")) | any_child_or_aged
        spouse_ok = in_range(pl.col("sage")) | any_child_or_aged
        earned = pl.when(primary_ok).then((pl.col("pwages") + pl.col("psemp")).clip(0, None)).otherwise(0.0) + pl.when(spouse_ok).then(
            (pl.col("swages") + pl.col("ssemp")).clip(0, None)
        ).otherwise(0.0)
        age_eligible = eitc_age_eligible(pl.col("num_children") == 0, 2022, behavior)
        eligible = (earned > 0) & age_eligible & (pl.col("eitc_investment_disqualified") <= 0) & ~is_dependent_filer()
    return df.with_columns(siitax=-pl.when(eligible).then(credit).otherwise(0.0) * flate)
