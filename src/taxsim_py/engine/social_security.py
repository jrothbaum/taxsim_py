"""Taxation of Social Security benefits."""

import polars as pl


def taxable_social_security(
    benefits: pl.Expr,
    provisional_income: pl.Expr,
    base_amount: pl.Expr,
    first_tier_width: pl.Expr,
    first_tier_rate: float,
    second_tier_rate: float,
    two_tiers: bool,
) -> pl.Expr:
    """Benefits included in AGI (TAXSIM `ssagi`).

    `provisional_income` already includes half the benefits. With one tier the
    taxable amount is `first_tier_rate` of the lesser of benefits and income
    over the base; with two tiers income over the base plus `first_tier_width`
    is taxed at `second_tier_rate`, up to that share of benefits.
    """
    over_base = (provisional_income - base_amount).clip(0, None)
    if not two_tiers:
        taxable = first_tier_rate * pl.min_horizontal(benefits, over_base)
    else:
        over_width = (over_base - first_tier_width).clip(0, None)
        first_tier = pl.min_horizontal(first_tier_rate * pl.min_horizontal(over_base, first_tier_width), first_tier_rate * benefits)
        taxable = pl.min_horizontal(second_tier_rate * benefits, first_tier + second_tier_rate * over_width)
    return pl.when(benefits > 0).then(taxable).otherwise(0.0)
