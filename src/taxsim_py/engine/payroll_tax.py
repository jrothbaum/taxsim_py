"""Payroll and self-employment tax calculations."""

import polars as pl


def oasdi_tax(wages: pl.Expr, wage_base: float, rate_combined: float) -> pl.Expr:
    return wages.clip(0, wage_base) * rate_combined


def hi_tax(wages: pl.Expr, rate_combined: float, wage_base: float = 1.0e15) -> pl.Expr:
    """Calculate Medicare tax."""
    return wages.clip(0, wage_base) * rate_combined


def remaining_room_oasdi_tax(
    income: pl.Expr, wage_base_already_used: pl.Expr, wage_base: float, rate_combined: float
) -> pl.Expr:
    """Calculate OASDI tax after wages use part of the wage base."""
    remaining_room = (wage_base - wage_base_already_used).clip(0, None)
    return income.clip(0, None).clip(0, remaining_room) * rate_combined


def capped_se_tax(
    wages: pl.Expr,
    se_gross: pl.Expr,
    wage_base: float,
    se_rate_combined: float,
    net_earnings_factor: float,
    rate_includes_netting: bool = False,
) -> pl.Expr:
    """Calculate one spouse's self-employment payroll tax."""
    n = net_earnings_factor
    se_gross_c = se_gross.clip(0, None)
    wages_alone_capped = wages > wage_base
    se_net = se_gross_c * n
    combined_over_cap = (wages + se_gross_c) * n > wage_base
    se_tax_when_under = se_net * se_rate_combined
    if rate_includes_netting:
        se_tax_when_over = (n * se_rate_combined * (se_gross_c * (1 - n) + wage_base - n * wages)).clip(0, None)
    else:
        se_tax_when_over = (se_rate_combined * (wage_base - n * wages)).clip(0, None)
    return (
        pl.when(wages_alone_capped)
        .then(0.0)
        .when(combined_over_cap)
        .then(se_tax_when_over)
        .otherwise(se_tax_when_under)
    )


def additional_medicare_tax(household_earnings: pl.Expr, threshold: pl.Expr, rate: float) -> pl.Expr:
    return (household_earnings - threshold).clip(0, None) * rate


def marginal_oasdi_rate(wages: pl.Expr, wage_base: float, rate_combined: float) -> pl.Expr:
    """Calculate the OASDI rate on the last dollar earned."""
    return pl.when(wages <= wage_base).then(rate_combined).otherwise(0.0)


def marginal_wage_rate_with_se(
    wages: pl.Expr,
    se_gross: pl.Expr,
    wage_base: float,
    rate_combined: float,
    net_earnings_factor: float,
) -> pl.Expr:
    """Calculate the marginal payroll rate on wages."""
    wages_alone_capped = wages > wage_base
    combined_over_cap = (wages + se_gross.clip(0, None)) * net_earnings_factor > wage_base
    return pl.when(wages_alone_capped | combined_over_cap).then(0.0).otherwise(rate_combined)


def self_employment_tax(
    se_gross: pl.Expr,
    wages: pl.Expr,
    wage_base: float,
    oasdi_rate: float,
    hi_rate: float,
    net_earnings_factor: float,
    hi_wage_base: float = 1.0e15,
) -> pl.Expr:
    """Calculate one spouse's total self-employment tax."""
    oasdi = capped_se_tax(wages, se_gross, wage_base, oasdi_rate, net_earnings_factor)
    hi = capped_se_tax(wages, se_gross, hi_wage_base, hi_rate, net_earnings_factor, rate_includes_netting=True)
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
    hi_wage_base: float = 1.0e15,
) -> pl.Expr:
    """Calculate household self-employment tax."""
    setax_1 = self_employment_tax(
        gross_se_income_primary, wages_primary, wage_base, se_oasdi_rate, se_hi_rate, net_earnings_factor, hi_wage_base
    )
    setax_2 = self_employment_tax(
        gross_se_income_secondary, wages_secondary, wage_base, se_oasdi_rate, se_hi_rate, net_earnings_factor, hi_wage_base
    )
    return setax_1 + setax_2
