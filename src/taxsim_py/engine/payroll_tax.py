"""Payroll and self-employment tax calculations."""

import polars as pl

from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR


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


# TAXSIM's `sstax` earnings items per earner: wages, self-employment income,
# then two business incomes. Its lookup table gives the primary earner both
# non-professional business incomes and the spouse both professional ones.
PAYROLL_ITEMS = (
    ("pwages", "psemp", "pbusinc", "sbusinc"),
    ("swages", "ssemp", "pprofinc", "sprofinc"),
)


def _capped_items(
    items: list[pl.Expr],
    factors: list[float],
    rates: list[float],
    cap: float,
    factor_in_rate: bool,
    coordinate_on_net_earnings: bool,
) -> tuple[list[pl.Expr], pl.Expr]:
    """Per-item tax with the cap applied to running factor-weighted earnings.

    The first item whose running total reaches the cap is reduced by the
    excess and later items pay nothing; the flag reports whether any did.
    """
    taxes = []
    running_gross = pl.lit(0.0)
    running_net = pl.lit(0.0)
    capped_before = pl.lit(False)
    for item, factor, rate in zip(items, factors, rates):
        running_gross = running_gross + item
        running_net = running_net + factor * item
        effective_rate = factor * rate if factor_in_rate else rate
        full = (item * effective_rate) if factor_in_rate else (factor * item * rate)
        # TAXSIM multiplies all prior earnings by the current item's factor.
        # Statutory mode instead coordinates each item at its own net-earnings
        # factor, so W-2 wages are never discounted by the Schedule SE factor.
        cap_earnings = running_net if coordinate_on_net_earnings else running_gross * factor
        over = cap_earnings > cap
        excess_rate = rate if coordinate_on_net_earnings else effective_rate
        reduced = full - (cap_earnings - cap) * excess_rate
        taxes.append(pl.when(capped_before).then(0.0).when(over).then(reduced).otherwise(full))
        capped_before = capped_before | over
    return taxes, capped_before


def taxsim_payroll(
    wage_base: float,
    hi_wage_base: float,
    oasdi_rate: float,
    se_oasdi_rate: float,
    hi_rate: float,
    net_earnings_factor: float,
    addmed_rate: float,
    addmed_threshold: pl.Expr,
    own_share_self_employment: float,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> dict[str, pl.Expr]:
    """Payroll and self-employment tax as TAXSIM's `sstax` computes them.

    Returns expressions for `wage_tax` (both halves of wage FICA), `setax`
    (all self-employment tax, `comnew(175)`), `setax_qbi` and `setax_sstb`
    (tax on the third and fourth items, TAXSIM `setxprof` and `setxsstb`,
    which its QBI deduction nets against non-professional and professional
    income respectively),
    `addmed`, `fica`, `tfica`, `own_fica_primary` (`comnew(183)`), and the
    primary earner's marginal wage rates `oasdi_rate_primary`/`hi_rate_primary`.
    """
    factors = [1.0, net_earnings_factor, net_earnings_factor, net_earnings_factor]
    oasdi_rates = [oasdi_rate, se_oasdi_rate, se_oasdi_rate, se_oasdi_rate]
    streams = []
    for names in PAYROLL_ITEMS:
        self_employment_items = [pl.col(name) for name in names[1:]]
        if behavior.prevent_negative_self_employment_tax:
            # Schedule SE combines each taxpayer's businesses before applying
            # its floor and minimum. Allocate the net amount over positive
            # sources so the downstream QBI categories remain meaningful.
            net_self_employment_income = pl.sum_horizontal(self_employment_items)
            taxable_self_employment_income = net_self_employment_income.clip(0, None)
            if behavior.enforce_schedule_se_minimum:
                taxable_self_employment_income = (
                    pl.when(net_earnings_factor * taxable_self_employment_income >= 400)
                    .then(taxable_self_employment_income)
                    .otherwise(0.0)
                )
            positive_self_employment_items = [
                item.clip(0, None) for item in self_employment_items
            ]
            positive_self_employment_total = pl.sum_horizontal(
                positive_self_employment_items
            )
            self_employment_items = [
                pl.when(positive_self_employment_total > 0)
                .then(
                    taxable_self_employment_income
                    * item
                    / positive_self_employment_total
                )
                .otherwise(0.0)
                for item in positive_self_employment_items
            ]
        # Compatibility mode retains TAXSIM's item order and loss treatment.
        items = [pl.col(names[0]).clip(0, None), *self_employment_items]
        oasdi, oasdi_capped = _capped_items(
            items,
            factors,
            oasdi_rates,
            wage_base,
            factor_in_rate=False,
            coordinate_on_net_earnings=behavior.coordinate_oasdi_on_net_earnings,
        )
        hi, hi_capped = _capped_items(
            items,
            factors,
            [hi_rate] * 4,
            hi_wage_base,
            factor_in_rate=True,
            coordinate_on_net_earnings=behavior.coordinate_oasdi_on_net_earnings,
        )
        streams.append(([o + h for o, h in zip(oasdi, hi)], oasdi_capped, hi_capped))

    wage_tax = streams[0][0][0] + streams[1][0][0]
    setax_self = streams[0][0][1] + streams[1][0][1]
    setax_qbi = streams[0][0][2] + streams[1][0][2]
    setax_sstb = streams[0][0][3] + streams[1][0][3]
    setax = setax_self + setax_qbi + setax_sstb
    earnings = pl.lit(0.0)
    if behavior.count_self_employment_once_for_additional_medicare:
        for names in PAYROLL_ITEMS:
            earnings = earnings + pl.col(names[0]).clip(0, None)
            earnings = earnings + net_earnings_factor * pl.sum_horizontal(
                pl.col(name) for name in names[1:]
            ).clip(0, None)
    else:
        # TAXSIM counts both earners' combined SE income in each earner loop.
        for names in PAYROLL_ITEMS:
            earnings = earnings + pl.col(names[0]).clip(0, None)
            earnings = earnings + net_earnings_factor * (pl.col("psemp") + pl.col("ssemp"))
            earnings = earnings + net_earnings_factor * (pl.col(names[2]) + pl.col(names[3]))
    addmed = additional_medicare_tax(earnings, addmed_threshold, addmed_rate)
    primary = streams[0]
    return {
        "wage_tax": wage_tax,
        "setax": setax,
        "setax_qbi": setax_qbi,
        "setax_sstb": setax_sstb,
        "addmed": addmed,
        "fica": setax + wage_tax + addmed,
        "tfica": setax + wage_tax / 2 + addmed,
        "own_fica_primary": pl.sum_horizontal(
            share * tax for share, tax in zip((1.0, *[own_share_self_employment] * 3), primary[0])
        ),
        "oasdi_rate_primary": pl.when(primary[1]).then(0.0).otherwise(oasdi_rate),
        "hi_rate_primary": pl.when(primary[2]).then(0.0).otherwise(hi_rate),
    }
