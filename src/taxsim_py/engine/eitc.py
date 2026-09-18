"""Earned Income Tax Credit: the standard trapezoid credit shape (phase in,
plateau at the max, phase out) shared by federal EITC and several state
EITC-style credits.

credit = min(
    clip(rate_in * earned_income, 0, max_credit),
    clip(max_credit - rate_out * (max(earned_income, agi) - phaseout_start), 0, None),
)

The reduction is subtracted from `max_credit` (the ceiling), then the
*smaller* of that and the phase-in amount is taken - NOT "phase_in minus
reduction, clipped at 0" (taxsim_2022_10_21.f:27716-27725: `eicpo=...;
earncr=min(earncr,max(0,crm-eicpo))`, where `earncr` at that point already
holds the phase-in amount). These two formulations are only equivalent
once the phase-in amount has already reached max_credit before the
phaseout starts - true for every filing-status/num_children combination
under ordinary wage-driven income, but NOT when AGI is inflated by
non-earned income (e.g. unemployment compensation) well past
phaseout_start while earned income (and so the phase-in amount) stays
near zero. Found via a real 2/4045 test mismatch once unemployment income
was added: 0-child credit, single, $0 wages, $10,200 UI - a childless
filer's own phase-in and phaseout rates are equal (7.65%), so the two
formulations only diverge in exactly this earned-income-decoupled-from-AGI
case.

Parameters (rate_in, max_credit, phaseout_start, rate_out) vary by
(year, num_children, filing_status) - see parameters/national/eitc.csv.
Married-filing-separately is not included: confirmed dead code in the
source even for 2021 (its own `nearn` eligibility gate zeroes the credit
for a separate return regardless of the `lawyr.ne.2021` exception right
below it - taxsim_2022_10_21.f:27696-27697,27750) - separate filers get no
credit in any year, matching the source's actual behavior over its own
comment's stated intent.
"""

import polars as pl


def trapezoid_credit(
    earned_income: pl.Expr,
    agi: pl.Expr,
    rate_in: float,
    max_credit: float,
    phaseout_start: float,
    rate_out: float,
) -> pl.Expr:
    phase_in = (earned_income * rate_in).clip(0, max_credit)
    phaseout_income = pl.max_horizontal(earned_income, agi)
    reduction = ((phaseout_income - phaseout_start) * rate_out).clip(0, None)
    ceiling_after_phaseout = (max_credit - reduction).clip(0, None)
    return pl.min_horizontal(phase_in, ceiling_after_phaseout).clip(0, None)
