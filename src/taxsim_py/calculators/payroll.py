"""Payroll tax calculator: produces TAXSIM's `fica`, `ficar`, `tfica`
columns, for wages and self-employment income (`psemp`/`ssemp`).

Confirmed against taxsim_2022_10_21.f:22166-22457 (function `sstax`) and
:21281-21295 (how the top-level `fica`/`ficar` columns are assembled from
sstax's outputs). Only `mtr=85` ("marginal rate with respect to the
taxpayer's own earnings", TAXSIM's default) is implemented for `ficar` -
the `mtr=86` (spouse) and `mtr=11` (non-wage) variants are not.

`ficar` here only reflects the OASDI/HI marginal rate on wages, not on
self-employment income specifically (the source's `r1o`/`r1h` do the same
- there's no separate SE-income marginal-rate term). Not itemized/business
income (pbusinc/pprofinc/sbusinc/sprofinc) - deferred like dividends and
capital gains.
"""

import polars as pl

from taxsim_py.engine.payroll_tax import (
    additional_medicare_tax,
    hi_tax,
    marginal_oasdi_rate,
    oasdi_tax,
    remaining_room_oasdi_tax,
)
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

PAYROLL_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def compute_payroll_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    for col in ("psemp", "ssemp"):
        if col not in df.columns:
            df = df.with_columns(pl.lit(0.0).alias(col))

    wage_base = float(resolve_year(PAYROLL_TAX_PARAMS["oasdi_wage_base"], year))
    oasdi_rate = float(resolve_year(PAYROLL_TAX_PARAMS["oasdi_rate_combined"], year))
    hi_rate = float(resolve_year(PAYROLL_TAX_PARAMS["hi_rate_combined"], year))
    addmed_rate = float(resolve_year(PAYROLL_TAX_PARAMS["additional_medicare_rate"], year))
    net_earnings_factor = float(resolve_year(PAYROLL_TAX_PARAMS["se_net_earnings_factor"], year))
    se_oasdi_rate = float(resolve_year(PAYROLL_TAX_PARAMS["se_oasdi_rate"], year))
    se_hi_rate = float(resolve_year(PAYROLL_TAX_PARAMS["se_hi_rate"], year))

    addmed_threshold_by_status = {
        status: resolve_year(PAYROLL_TAX_PARAMS["additional_medicare_threshold"][status], year)
        for status in FILING_STATUSES
    }
    threshold_expr = pl.lit(None, dtype=pl.Float64)
    for status, value in addmed_threshold_by_status.items():
        threshold_expr = (
            pl.when(pl.col("filing_status") == status)
            .then(pl.lit(float(value)))
            .otherwise(threshold_expr)
        )

    wage1 = pl.col("pwages")
    wage2 = pl.col("swages")
    household_wages = wage1 + wage2
    se_net1 = pl.col("psemp").clip(0, None) * net_earnings_factor
    se_net2 = pl.col("ssemp").clip(0, None) * net_earnings_factor
    household_se_net = se_net1 + se_net2

    oasw1 = oasdi_tax(wage1, wage_base, oasdi_rate)
    oasw2 = oasdi_tax(wage2, wage_base, oasdi_rate)
    oasb1 = remaining_room_oasdi_tax(se_net1, wage1, wage_base, se_oasdi_rate)
    oasb2 = remaining_room_oasdi_tax(se_net2, wage2, wage_base, se_oasdi_rate)
    hiw1 = hi_tax(wage1, hi_rate)
    hiw2 = hi_tax(wage2, hi_rate)
    hib1 = hi_tax(se_net1, se_hi_rate)
    hib2 = hi_tax(se_net2, se_hi_rate)

    # Additional Medicare Tax: wages claim the threshold first (household
    # total, not per-spouse), self-employment earnings get whatever's left.
    addmtx = additional_medicare_tax(
        household_wages, threshold_expr, addmed_rate
    ) + additional_medicare_tax(
        household_se_net, (threshold_expr - household_wages).clip(0, None), addmed_rate
    )

    wage_slice = oasw1 + oasw2 + hiw1 + hiw2
    se_slice = oasb1 + oasb2 + hib1 + hib2

    fica = (wage_slice + se_slice + addmtx).round(2)
    # tfica: wages are half-borne by an employer, so the taxpayer's own
    # share is halved; self-employment income has no employer counterpart,
    # so the taxpayer bears that slice in full (taxsim_2022_10_21.f:22385).
    tfica = (0.5 * wage_slice + se_slice + addmtx).round(2)

    # Marginal OASDI rate wrt the taxpayer's own wages: 0 once wages + their
    # own SE income together already reach the wage base, not just wages
    # alone (found via a real test failure: at pwages=100000+psemp=100000,
    # combined earnings already exceed the $147,000 cap, so an extra dollar
    # of wages faces no further OASDI - the source's own r1o is set inside
    # the same wage-vs-cap branch as oasw1, which already accounts for bus1
    # filling the cap first in the "earn1 >= smax" branch).
    r1o = marginal_oasdi_rate(wage1 + se_net1, wage_base, oasdi_rate)
    r1h = hi_rate + pl.when(addmtx > 0).then(addmed_rate).otherwise(0.0)
    ficar = ((r1o + r1h) * 100).round(2)

    # addmed: Additional Medicare Tax as its own reported value (TAXSIM's
    # v44) - already folded into fica/tfica above, exposed separately too.
    return df.with_columns(fica=fica, ficar=ficar, tfica=tfica, addmed=addmtx.round(2))
