"""Social Security + Medicare payroll tax on wages and self-employment
income, per spouse. TAXSIM reports the combined employer+employee economic
burden (`fica`), the taxpayer's own marginal payroll-tax rate (`ficar`),
and the primary taxpayer's own liability alone (`tfica`, "ssmed" in the
source) - see taxsim_2022_10_21.f:22166-22457.

All functions take one spouse's wages/SE income at a time; callers sum
across spouses for household totals (`fica`) or combine as documented for
household-level items (Additional Medicare Tax, marginal rate).

Self-employment income shares the *same* OASDI wage base as wages, with
wages counted first: `remaining_room_oasdi_tax` implements this as a single
continuous clip rather than the source's three-way branch
(taxsim_2022_10_21.f:22308-22320) - deliberately, not just for style. That
branch leaves `oasb1`/`hib1` (the SE-income OASDI/HI amounts) unassigned
in the "wages alone already exceed the cap" case, and since they live in a
COMMON block that persists across records, `tfica` for such a record can
silently pick up a stale value left over from whatever unrelated prior
record happened to run before it in the same batch - confirmed empirically
(taxsim_2022_10_21.f, wages=$200k + $20k self-employment income, single):
tfica=$12,715.86 run in isolation, $13,583.86 (off by exactly the prior
record's stale oasb1) when run right after another self-employment-income
record in the same batch. This implementation always computes the
correct, order-independent $12,715.86-style answer - it does not replicate
that bug.
"""

import polars as pl


def oasdi_tax(wages: pl.Expr, wage_base: float, rate_combined: float) -> pl.Expr:
    return wages.clip(0, wage_base) * rate_combined


def hi_tax(wages: pl.Expr, rate_combined: float) -> pl.Expr:
    return wages.clip(0, None) * rate_combined


def remaining_room_oasdi_tax(
    income: pl.Expr, wage_base_already_used: pl.Expr, wage_base: float, rate_combined: float
) -> pl.Expr:
    """OASDI tax on `income` (self-employment net earnings) given that
    `wage_base_already_used` (wages) has first claim on the wage base."""
    remaining_room = (wage_base - wage_base_already_used).clip(0, None)
    return income.clip(0, None).clip(0, remaining_room) * rate_combined


def additional_medicare_tax(household_earnings: pl.Expr, threshold: pl.Expr, rate: float) -> pl.Expr:
    return (household_earnings - threshold).clip(0, None) * rate


def marginal_oasdi_rate(wages: pl.Expr, wage_base: float, rate_combined: float) -> pl.Expr:
    return pl.when(wages < wage_base).then(rate_combined).otherwise(0.0)


def self_employment_tax(
    se_net_earnings: pl.Expr,
    wages: pl.Expr,
    wage_base: float,
    oasdi_rate: float,
    hi_rate: float,
) -> pl.Expr:
    """Total Schedule-SE-style self-employment tax for one spouse (OASDI on
    whatever wage-base room is left after wages, plus uncapped HI) - the
    amount half of which is deductible for AGI, and which the taxpayer (not
    an employer) bears in full. `se_net_earnings` should already be the
    92.35%-adjusted net earnings, not gross self-employment income."""
    oasdi = remaining_room_oasdi_tax(se_net_earnings, wages, wage_base, oasdi_rate)
    hi = hi_tax(se_net_earnings, hi_rate)
    return oasdi + hi


def household_self_employment_tax(
    gross_se_income_primary: pl.Expr,
    gross_se_income_secondary: pl.Expr,
    wages_primary: pl.Expr,
    wages_secondary: pl.Expr,
    net_earnings_factor: float,
    wage_base: float,
    se_oasdi_rate: float,
    se_hi_rate: float,
) -> pl.Expr:
    """Household total self-employment tax - the figure federal.py needs for
    the half-SE-tax AGI deduction (computed independently of payroll.py's
    own per-spouse fica/tfica breakdown, since AGI is computed before
    payroll tax runs in the pipeline)."""
    se_net_1 = gross_se_income_primary.clip(0, None) * net_earnings_factor
    se_net_2 = gross_se_income_secondary.clip(0, None) * net_earnings_factor
    setax_1 = self_employment_tax(se_net_1, wages_primary, wage_base, se_oasdi_rate, se_hi_rate)
    setax_2 = self_employment_tax(se_net_2, wages_secondary, wage_base, se_oasdi_rate, se_hi_rate)
    return setax_1 + setax_2
