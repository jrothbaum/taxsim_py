"""Alternative minimum tax calculations."""

import polars as pl


def alternative_minimum_tax(
    amt_income: pl.Expr,
    regular_tax: pl.Expr,
    exemption: pl.Expr,
    exemption_phaseout_threshold: pl.Expr,
    exemption_phaseout_rate: float,
    rate_breakpoint: float,
    rate_below_breakpoint: float,
    rate_above_breakpoint: float,
    sepret: pl.Expr | float = 1.0,
    ltg: pl.Expr | None = None,
    regular_taxable_income: pl.Expr | None = None,
    cg_rate_0_ceiling: pl.Expr | None = None,
    cg_rate_15_ceiling: pl.Expr | None = None,
    cg_rate_15: float = 0.0,
    cg_rate_0: float = 0.0,
    separate_return_addback_cap: float | None = None,
    separate_return_addback_threshold: float | None = None,
) -> pl.Expr:
    # Married-filing-separately-only AMTI addback (1990+ -
    # taxsim_2022_10_21.f:25214-25217): `alminy = alminy +
    # min(cap, .25*max(0, alminy-threshold))`, applied BEFORE the
    # ordinary exemption phaseout below (it affects both the phaseout
    # and the final base) - a genuinely separate mechanism from that
    # phaseout, not just "half the joint numbers". Found via CT's own
    # AMT (which reads federal's alminy directly) mismatching for a
    # married_separate, $260k-wages, no-preference-items case.
    if separate_return_addback_cap is not None:
        addback = pl.min_horizontal(
            float(separate_return_addback_cap),
            0.25 * (amt_income - float(separate_return_addback_threshold)).clip(0, None),
        )
        amt_income = pl.when(sepret == 2.0).then(amt_income + addback).otherwise(amt_income)

    exemption_after_phaseout = (
        exemption - exemption_phaseout_rate * (amt_income - exemption_phaseout_threshold).clip(0, None)
    ).clip(0, None)
    amt_base = (amt_income - exemption_after_phaseout).clip(0, None)

    breakpoint_per_return = rate_breakpoint / sepret
    backout = (rate_above_breakpoint - rate_below_breakpoint) * rate_breakpoint / sepret

    if ltg is None:
        tentative_minimum_tax = (
            pl.when(amt_base <= breakpoint_per_return)
            .then(amt_base * rate_below_breakpoint)
            .otherwise(amt_base * rate_above_breakpoint - backout)
        )
    else:
        ltg_capped = pl.min_horizontal(ltg, amt_base)
        ordinary_amt_base = (amt_base - ltg_capped).clip(0, None)
        tentative_ordinary_tax = (
            pl.when(ordinary_amt_base <= breakpoint_per_return)
            .then(ordinary_amt_base * rate_below_breakpoint)
            .otherwise(ordinary_amt_base * rate_above_breakpoint - backout)
        )
        # Three tiers (see module docstring): 0% up to `zero_pct_room`
        # (based on *regular* ordinary income, not AMT's own), then a flat
        # 15% up to AMT's own 15%/20% breakpoint, then that top slice gets
        # a further 5% surtax on top of the 15% it already got - netting
        # to an effective 20% there, just computed as two separate pieces.
        regular_ordinary_income = (regular_taxable_income - ltg).clip(0, None)
        zero_pct_room = (cg_rate_0_ceiling - regular_ordinary_income).clip(0, None)
        zero_pct_amount = pl.min_horizontal(zero_pct_room, ltg_capped)
        remaining_after_zero = ltg_capped - zero_pct_amount
        fifteen_pct_room = (cg_rate_15_ceiling - regular_ordinary_income - zero_pct_room).clip(0, None)
        top_slice = (remaining_after_zero - fifteen_pct_room).clip(0, None)
        tentative_ltg_tax = cg_rate_0 * zero_pct_amount + cg_rate_15 * remaining_after_zero + 0.05 * top_slice
        tentative_minimum_tax = tentative_ordinary_tax + tentative_ltg_tax

    return (tentative_minimum_tax - regular_tax).clip(0, None)
