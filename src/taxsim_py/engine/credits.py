"""Federal tax credit calculations."""

import polars as pl


def child_care_credit_rate(
    agi: pl.Expr,
    first_phase_start: float,
    first_phase_ceiling: float,
    first_phase_step_amount: float,
    top_rate: float,
    mid_rate: float,
    second_phase_start: float,
    second_phase_ceiling: float,
    second_phase_step_amount: float,
) -> pl.Expr:
    """Calculate the post-2020 child care credit rate."""
    first_phase_rate = top_rate - ((agi - first_phase_start) / first_phase_step_amount).clip(0, None) * 0.01
    second_phase_rate = mid_rate - (
        (agi - second_phase_start) / second_phase_step_amount
    ).clip(0, None) * 0.01
    return (
        pl.when(agi <= first_phase_start)
        .then(top_rate)
        .when(agi < first_phase_ceiling)
        .then(first_phase_rate)
        .when(agi <= second_phase_start)
        .then(mid_rate)
        .when(agi < second_phase_ceiling)
        .then(second_phase_rate)
        .otherwise(0.0)
    )


def child_care_credit_rate_pre2021(
    agi: pl.Expr,
    phase_start: float,
    top_rate: float,
    floor_rate: float,
    step_amount: float,
) -> pl.Expr:
    """Calculate the pre-2021 child care credit rate."""
    stepped_down = top_rate - ((agi - phase_start) / step_amount).clip(0, None) * 0.01
    return pl.max_horizontal(stepped_down, pl.lit(floor_rate))


def phased_out_nonrefundable_credit(
    base_credit: pl.Expr,
    agi: pl.Expr,
    phaseout_threshold: pl.Expr,
    phaseout_rate_per_1000: float,
    tax_before_credit: pl.Expr,
) -> pl.Expr:
    reduction = ((agi - phaseout_threshold).clip(0, None) / 1000.0) * phaseout_rate_per_1000
    after_phaseout = (base_credit - reduction).clip(0, None)
    return pl.min_horizontal(after_phaseout, tax_before_credit.clip(0, None))
