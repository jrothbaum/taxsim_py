"""Preferential capital gains and dividend tax calculations."""

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
    """Calculate tax on the preferential-rate portion of income."""
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
