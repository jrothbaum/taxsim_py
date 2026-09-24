"""Earned Income Tax Credit calculations."""

import polars as pl


def trapezoid_credit(
    earned_income: pl.Expr,
    agi: pl.Expr,
    rate_in: float,
    max_credit: float,
    phaseout_start: float,
    rate_out: float,
) -> pl.Expr:
    phase_in = (earned_income * rate_in).clip(0, max_credit)
    phaseout_income = pl.max_horizontal(earned_income, agi)
    reduction = ((phaseout_income - phaseout_start) * rate_out).clip(0, None)
    ceiling_after_phaseout = (max_credit - reduction).clip(0, None)
    return pl.min_horizontal(phase_in, ceiling_after_phaseout).clip(0, None)


def federal_eitc(
    earned_income: pl.Expr,
    agi: pl.Expr,
    disqualified_income: pl.Expr,
    filing_status: pl.Expr,
    num_children: pl.Expr,
    params: pl.DataFrame,
    investment_income_limit: float | None,
) -> pl.Expr:
    """Federal EITC from one year's parameter rows (TAXSIM `eitcr`).

    `params` holds that year's rows of `parameters/national/eitc.csv`;
    statuses without a row (married separate) receive no credit.
    """
    credit = pl.lit(0.0)
    for row in params.to_dicts():
        applies = (filing_status == row["filing_status"]) & (num_children == row["num_children"])
        amount = trapezoid_credit(
            earned_income, agi, row["rate_in"], row["max_credit"], row["phaseout_start"], row["rate_out"]
        )
        credit = pl.when(applies).then(amount).otherwise(credit)
    if investment_income_limit is not None:
        credit = credit - (disqualified_income - investment_income_limit).clip(0, None)
    return credit.clip(0, None)
