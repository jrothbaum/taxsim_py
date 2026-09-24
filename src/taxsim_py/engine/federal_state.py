"""Federal/state combined-tax resolution - the general mechanism every
state calculator with a real income tax must go through, discovered while
building Alabama (taxsim_2022_10_21.f:21631-21704, `tcalc`/`tcalc2`).

TAXSIM does NOT compute federal once and hand the result to the state
calculator one-directionally. Instead:

1. Federal's own SALT itemized deduction includes the STATE's income tax
   LIABILITY as a real deductible component (`data(50) = max(sales_tax_
   deduction, state_tax_liability, 0)` - taxsim_2022_10_21.f:21688), which
   the state tax itself depends on via federal AGI - a genuine circular
   dependency, resolved by ITERATING three times by default (`iters=3`,
   taxsim_2022_10_21.f:21677-21678): compute federal -> compute state ->
   feed the state tax back into federal's SALT deduction -> repeat.
2. The federal itemize-vs-standard-deduction choice is made on COMBINED
   federal+state tax, not federal tax alone (`tcalc`, taxsim_2022_10_21.f:
   21641-21661): run the WHOLE 3-iteration loop once forced to itemize
   (`data(4)=-1`), once forced to the standard deduction (`data(4)=-2`),
   sum federal+state tax for each, and keep whichever total is smaller.

Confirmed empirically, not assumed: a debug-instrumented oracle build for
a $260,000-wages/single/2003/Alabama case showed federal `fiitax` itself
differing depending on which state was passed in (matching this project's
own federal-only port only when state has NO income tax, e.g. TX) - traced
to this exact mechanism, not a bug in either calculator.

Scope/simplification: the real `saletx` (federal sales-tax-deduction
election) alternative to state income tax is only built for TX in this
project so far (parameters/states/tx/sales_tax_deduction.yaml) - other
states' own sales-tax tables aren't built yet, so `salt_ded` here is just
`max(state_tax_liability, 0)`, no sales-tax alternative considered. Safe
for now: the sales-tax election only ever wins for filers whose state
income tax liability is small or zero, which isn't the states/scope this
project is validating against yet - revisit if a real state-tax-liability
case turns out to lose to the sales-tax alternative.
"""

import polars as pl

from taxsim_py.calculators.federal import compute_regular_tax
from taxsim_py.calculators.payroll import compute_payroll_tax

ITERATIONS = 3


def _run_forced(raw_df: pl.DataFrame, year: int, force_itemize: bool, compute_state_tax_fn) -> pl.DataFrame:
    cur = raw_df
    out = None
    for _ in range(ITERATIONS):
        fed_out = compute_regular_tax(cur, year, force_itemize=force_itemize)
        fed_out = compute_payroll_tax(fed_out, year)
        # Most state calculators don't need to know which branch is
        # active (their own itemize-vs-standard choice is independent of
        # federal's), but a few (e.g. Arkansas, whose own `ided`-gated
        # table selection literally mirrors `data(4)`) do - pass it along
        # when the calculator accepts it, no-op otherwise.
        try:
            out = compute_state_tax_fn(fed_out, year, force_itemize=force_itemize)
        except TypeError:
            out = compute_state_tax_fn(fed_out, year)
        salt_ded = out.get_column("siitax").clip(0, None)
        cur = raw_df.with_columns(state_sales_or_income_tax_ded=salt_ded)
    return out


def resolve_federal_and_state(raw_df: pl.DataFrame, year: int, compute_state_tax_fn) -> pl.DataFrame:
    """Run the full fed<->state fixed-point loop for BOTH forced-itemize and
    forced-standard-deduction, then keep whichever gives the smaller
    COMBINED federal+state tax - matching `tcalc` exactly. `raw_df` should
    hold only the raw federal input columns (no pre-existing federal/state
    output columns) - each iteration re-derives everything from scratch.
    Returns one merged dataframe with the winning branch's columns.
    """
    out_itemize = _run_forced(raw_df, year, True, compute_state_tax_fn)
    out_standard = _run_forced(raw_df, year, False, compute_state_tax_fn)

    combined_itemize = out_itemize.get_column("fiitax") + out_itemize.get_column("siitax")
    combined_standard = out_standard.get_column("fiitax") + out_standard.get_column("siitax")
    itemize_wins = combined_itemize < combined_standard

    result_cols = {
        col: out_itemize.get_column(col).zip_with(itemize_wins, out_standard.get_column(col))
        for col in out_itemize.columns
    }
    return pl.DataFrame(result_cols)
