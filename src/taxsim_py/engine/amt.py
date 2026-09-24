"""Alternative Minimum Tax: exemption with phaseout, flat two-bracket rate
on the AMT base, compared against regular tax. General shape (not
2022-specific) - see parameters/national/amt.yaml for the year's constants.

Qualified dividends and net long-term capital gains (`ltg`) get
preferential treatment under AMT too (Form 6251 Part III), but NOT the
same 0%/15%/20% structure regular tax uses. Traced line-by-line against
the source's own Form-6251-worksheet transcription and confirmed against
several live oracle probes (a genuinely dense area of the source - two
separate, layered computations, taxsim_2022_10_21.f:25280-25396 then
:25397-25456, whose SUM is the real answer, not either alone):

- The 0%-bracket ROOM (`part15` in the source) is
  `max(0, rate_0_ceiling - max(0, regular_taxable_income - ltg))` - based
  on *regular* ordinary income (`taxinc - ltg`), not the AMT base's own
  ordinary-income split (`amt_base - ltg_capped`). These two genuinely
  differ whenever AMTI exceeds regular taxable income (e.g. a
  non-itemizer's disallowed standard deduction) - confirmed by a real 2017
  mismatch this surfaced (AMT was provably always $0 for 2018-2022 in this
  model's wage+SALT+mortgage scope, so this was never actually exercised
  with a nonzero result before).
- Above that 0% room, the *next* slice up to AMT's own 15%/20% breakpoint
  (`cg_rate_15_ceiling` - a separate copy of the breakpoint from
  capital_gains.yaml's regular-tax one) is taxed at a flat 15% - so far,
  the same two-tier shape as the fix above. But past THAT breakpoint,
  there's a real third tier: taxsim_2022_10_21.f:25434-25453 computes an
  `alm20` slice (whatever's left of `ltg` above the 15% breakpoint) and
  adds `0.05 * alm20` as a surtax ON TOP OF the 15% that slice already got
  from the simpler two-tier calculation - netting to an effective 20% on
  that top slice, layered as "15% + a separate 5% add-on" rather than a
  clean 20% bracket. Found by back-solving a real $3,830 mismatch
  ($0.05 * $76,600) against a live oracle probe once a high-income,
  multi-source-of-preferential-income case exposed it - the earlier
  two-tier fix was incomplete, not wrong, for every case that doesn't
  reach this third tier.
- Missing preferential treatment entirely (taxing all of `ltg` at the
  ordinary 26%/28% AMT rate) initially overstated AMT dramatically for any
  return with large dividends/capital gains (confirmed empirically:
  $459,750 in dividends alone, single, wages=0, 2022 - true AMT liability
  is $0, an earlier version of this function computed $103,356).

`ltg`/the preferential-rate params are optional so wages-only callers
don't need to pass them.
"""

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
