"""Nonrefundable credit with a linear phaseout, capped so it never drives
liability below zero and never refunds the unused excess. Shared shape behind
the federal Credit for Other Dependents (and generally: CTC-style credits
before their refundable portion, several state credits).
"""

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
    """The Child and Dependent Care Credit's rate schedule - a step-down
    formula, not a smooth interpolation (see parameters/national/credits.yaml
    for why it's genuinely discontinuous at the two ceilings). Both ceiling
    comparisons are strictly-less-than, confirmed empirically against
    taxsim2022.exe: AGI exactly at $183,000 or $438,000 falls to the next
    flat rate, not the phase-down formula (a boundary condition easy to
    misread as <= from the source's own if/elseif chain - verified by
    testing the exact boundary and both neighbors directly)."""
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
    """The Child and Dependent Care Credit's rate schedule for lawyr
    2003-2020: a single continuous step-down from top_rate to floor_rate
    (1 percentage point per step_amount of AGI above phase_start), which
    holds at floor_rate forever above that - unlike the 2021+ schedule,
    this is a plain max()/no branching in the source, so it's smooth with
    no discontinuity to worry about. Verified against
    taxsim_2022_10_21.f:25519-25521:
    chr = .01*max(20.0, 35. - max((agi-15000.)/2000., 0.0))"""
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
