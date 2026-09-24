"""Qualified Dividends and Capital Gain Tax Worksheet: stacks preferential-
rate income (`ltg` - qualified dividends + net long-term capital gains) on
top of ordinary taxable income, taxing each slice of `ltg` at 0%, 15%, or
20% depending on where it falls once ordinary income has filled the lower
brackets. See parameters/national/capital_gains.yaml for scope notes.
"""

import polars as pl


def preferential_rate_tax(
    taxable_income: pl.Expr,
    ltg: pl.Expr,
    rate_0_ceiling: pl.Expr,
    rate_15_ceiling: pl.Expr,
    rate_15: float,
    rate_20: float,
    rate_0: float = 0.0,
) -> pl.Expr:
    """Returns just the tax on `ltg` (the preferential-rate slice) - the
    ordinary-income slice (`taxable_income - ltg`) still needs the regular
    bracket tax applied separately.

    `rate_0` is 0% for every year this project has modeled so far except
    2003-2007, when the bottom bracket's rate was 5%, not 0% (TIPRA 2005 cut
    it to 0% starting 2008) - taxsim_2022_10_21.f:27202-27206."""
    ordinary_income = (taxable_income - ltg).clip(0, None)

    amount_at_0 = (pl.min_horizontal(taxable_income, rate_0_ceiling) - ordinary_income).clip(0, None).clip(0, ltg)
    remaining_after_0 = (ltg - amount_at_0).clip(0, None)
    amount_at_15 = (
        (pl.min_horizontal(taxable_income, rate_15_ceiling) - ordinary_income - amount_at_0)
        .clip(0, None)
        .clip(0, remaining_after_0)
    )
    amount_at_20 = (ltg - amount_at_0 - amount_at_15).clip(0, None)

    return rate_0 * amount_at_0 + rate_15 * amount_at_15 + rate_20 * amount_at_20
