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
) -> pl.Expr:
    """Returns just the tax on `ltg` (the preferential-rate slice) - the
    ordinary-income slice (`taxable_income - ltg`) still needs the regular
    bracket tax applied separately."""
    ordinary_income = (taxable_income - ltg).clip(0, None)

    amount_at_0 = (pl.min_horizontal(taxable_income, rate_0_ceiling) - ordinary_income).clip(0, None).clip(0, ltg)
    remaining_after_0 = (ltg - amount_at_0).clip(0, None)
    amount_at_15 = (
        (pl.min_horizontal(taxable_income, rate_15_ceiling) - ordinary_income - amount_at_0)
        .clip(0, None)
        .clip(0, remaining_after_0)
    )
    amount_at_20 = (ltg - amount_at_0 - amount_at_15).clip(0, None)

    return rate_15 * amount_at_15 + rate_20 * amount_at_20
