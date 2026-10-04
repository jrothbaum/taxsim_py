"""Alternative minimum tax calculations."""


from __future__ import annotations
import polars as pl


def separate_return_amt_income(
    amt_income: pl.Expr, sepret: pl.Expr | float, cap: float, threshold: float, rate: float
) -> pl.Expr:
    """AMT income with the married-filing-separately addback (1990 on)."""
    addback = pl.min_horizontal(float(cap), rate * (amt_income - float(threshold)).clip(0, None))
    return pl.when(sepret == 2.0).then(amt_income + addback).otherwise(amt_income)


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
    cg_top_rate_addition: float = 0.0,
    separate_return_addback_cap: float | None = None,
    separate_return_addback_threshold: float | None = None,
    separate_return_addback_rate: float = 0.0,
    exemption_cap: pl.Expr | None = None,
) -> pl.Expr:
    # Separate returns add back part of AMT income from 1990
    # (taxsim_2022_10_21.f:25214-25217) before the exemption phaseout.
    if separate_return_addback_cap is not None:
        amt_income = separate_return_amt_income(
            amt_income, sepret, separate_return_addback_cap, separate_return_addback_threshold, separate_return_addback_rate
        )

    exemption_after_phaseout = (
        exemption - exemption_phaseout_rate * (amt_income - exemption_phaseout_threshold).clip(0, None)
    ).clip(0, None)
    if exemption_cap is not None:
        exemption_after_phaseout = pl.min_horizontal(exemption_after_phaseout, exemption_cap.clip(0, None))
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
        # Gains are taxed at 0% up to the room left by regular ordinary
        # income, 15% up to the 15% ceiling, and 20% (15% plus 5%) above.
        regular_ordinary_income = (regular_taxable_income - ltg).clip(0, None)
        zero_pct_room = (cg_rate_0_ceiling - regular_ordinary_income).clip(0, None)
        zero_pct_amount = pl.min_horizontal(zero_pct_room, ltg_capped)
        remaining_after_zero = ltg_capped - zero_pct_amount
        fifteen_pct_room = (cg_rate_15_ceiling - regular_ordinary_income - zero_pct_room).clip(0, None)
        top_slice = (remaining_after_zero - fifteen_pct_room).clip(0, None)
        tentative_ltg_tax = cg_rate_0 * zero_pct_amount + cg_rate_15 * remaining_after_zero + cg_top_rate_addition * top_slice
        tentative_minimum_tax = tentative_ordinary_tax + tentative_ltg_tax

    return (tentative_minimum_tax - regular_tax).clip(0, None)
