"""Earned Income Tax Credit calculations."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

_EITC_MISC = load_yaml(PARAMETERS_ROOT / "national" / "eitc_misc.yaml")


def eitc_filer_eligible(childless: pl.Expr, year: int) -> pl.Expr:
    """Whether the return may claim the EITC before the minimum-age test.

    Dependent filers may not, nor childless returns on which every taxpayer
    is 65 or older (except in 2021).
    """
    eligible = ~is_dependent_filer()
    if year != 2021:
        eligible = eligible & ~(childless & (aged_count() >= taxpayer_count()))
    return eligible


def eitc_age_eligible(childless: pl.Expr, year: int) -> pl.Expr:
    """Whether a childless return passes the EITC minimum-age test (1994 on).

    A taxpayer below the minimum age disqualifies it (outside 2021 only the
    older taxpayer, or a younger spouse of a taxpayer over 65); an
    unreported age (0) passes.
    """
    if year < 1994:
        return pl.lit(True)
    minimum_age = float(resolve_year(_EITC_MISC["childless_minimum_age"], year))
    older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
    younger = pl.min_horizontal(pl.col("page"), pl.col("sage"))

    def too_young(age: pl.Expr) -> pl.Expr:
        return (age > 0) & (age < minimum_age)

    if year == 2021:
        ineligible = too_young(older) | too_young(younger)
    else:
        ineligible = too_young(older) | ((older > _EITC_MISC["childless_aged_age"]) & too_young(younger))
    return ~(childless & ineligible)


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
