"""Federal payroll tax calculator."""

import polars as pl

from taxsim_py.engine.payroll_tax import (
    additional_medicare_tax,
    capped_se_tax,
    hi_tax,
    marginal_wage_rate_with_se,
    oasdi_tax,
)
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

PAYROLL_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def compute_payroll_tax(
    df: pl.DataFrame | pl.LazyFrame, year: int
) -> pl.DataFrame | pl.LazyFrame:
    columns = df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns
    for col in ("psemp", "ssemp"):
        if col not in columns:
            df = df.with_columns(pl.lit(0.0).alias(col))

    wage_base = float(resolve_year(PAYROLL_TAX_PARAMS["oasdi_wage_base"], year))
    oasdi_rate = float(resolve_year(PAYROLL_TAX_PARAMS["oasdi_rate_combined"], year))
    hi_wage_base = float(resolve_year(PAYROLL_TAX_PARAMS["hi_wage_base"], year))
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
    oasb1 = capped_se_tax(wage1, pl.col("psemp"), wage_base, se_oasdi_rate, net_earnings_factor)
    oasb2 = capped_se_tax(wage2, pl.col("ssemp"), wage_base, se_oasdi_rate, net_earnings_factor)
    # Wages' own HI rate is `shrate(law)` directly (`se_hi_rate` below),
    # NOT `2*hrate(law)` - the taxsim_2024_09_21.f `sstax` rewrite applies
    # the SAME rate array to wages and self-employment income alike
    # (`erate = h(law,i)*shrate(law)` for every i, including i=1/wages).
    # These coincide from 1987+ (both .029), but genuinely diverge earlier
    # (1966: shrate=.0035 vs 2*hrate=.0070) - found via a live oracle
    # probe (1970, single, $500 wages: taxsim2024.exe's real combined
    # rate is 9.0%, not 9.6% - matching 8.4% OASDI + 0.6% HI via shrate,
    # not + 1.2% via 2*hrate).
    hiw1 = hi_tax(wage1, se_hi_rate, hi_wage_base)
    hiw2 = hi_tax(wage2, se_hi_rate, hi_wage_base)
    # rate_includes_netting=True: HI's own `erate=h(law,i)*shrate(law)`
    # bakes the netting factor into the rate itself (unlike OASDI's own
    # `erate=strate-shrate`, which doesn't) - see capped_se_tax's own
    # docstring for the closed-form consequence, found via a live probe
    # once HI's own (1988-1993-only) finite wage base cap was exercised.
    hib1 = capped_se_tax(wage1, pl.col("psemp"), hi_wage_base, se_hi_rate, net_earnings_factor, rate_includes_netting=True)
    hib2 = capped_se_tax(wage2, pl.col("ssemp"), hi_wage_base, se_hi_rate, net_earnings_factor, rate_includes_netting=True)

    # Additional Medicare Tax: a single flat `.009*max(0,e-thres)` on total
    # household earnings (taxsim_2024_09_21.f:22278-22283), NOT "wages
    # claim the threshold first, SE gets whatever's left" (an earlier,
    # pre-2024-oracle version of this code modeled it that way). `e`
    # itself has a real, confirmed quirk: it's computed in a loop BEFORE
    # self-employment income gets split per spouse, using the same
    # `d(17)` (combined psemp+ssemp) index for BOTH the taxpayer's and
    # spouse's own iteration (`j(2,1)=j(2,2)=17`) - so the COMBINED
    # self-employment net earnings get counted TWICE, regardless of filing
    # status or how psemp/ssemp individually split. Found via a live
    # oracle probe (single, $200,000 self-employment income only, 2015):
    # this project's own $0 addmtx (net earnings $184,700, under the
    # $200k threshold) vs the oracle's real $1,524.60 - which only
    # reconciles once `e` uses `2*household_se_net` instead of
    # `household_se_net` once (2*$184,700=$369,400, minus the $200k
    # threshold, times .9%, is exactly $1,524.60).
    addmtx_earnings = household_wages + 2 * household_se_net
    addmtx = additional_medicare_tax(addmtx_earnings, threshold_expr, addmed_rate)

    wage_slice = oasw1 + oasw2 + hiw1 + hiw2
    se_slice = oasb1 + oasb2 + hib1 + hib2

    fica = (wage_slice + se_slice + addmtx).round(2)
    # tfica: wages are half-borne by an employer, so the taxpayer's own
    # share is halved; self-employment income has no employer counterpart,
    # so the taxpayer bears that slice in full (taxsim_2022_10_21.f:22385).
    tfica = (0.5 * wage_slice + se_slice + addmtx).round(2)

    # Marginal OASDI rate wrt the taxpayer's own wages: 0 once wages ALONE
    # reach the wage base, or once (wages+se_gross)*net_earnings_factor
    # does - a binary switch, not a blended derivative (see
    # `marginal_wage_rate_with_se`'s own docstring - using `wage1+se_net1`
    # here instead, an earlier and subtly wrong stand-in for this exact
    # check, is what a live oracle probe against `pwages=70000,
    # psemp=21657, 2005` caught: it reported 0 OASDI marginal rate for a
    # case that isn't actually over the combined cap once computed
    # correctly).
    r1o = marginal_wage_rate_with_se(wage1, pl.col("psemp"), wage_base, oasdi_rate, net_earnings_factor)
    # Same shape reused for HI's own 1993-only wage base cap - a no-op for
    # every other year, since hi_wage_base is an effectively-infinite
    # sentinel. Additional Medicare Tax is NOT folded into the reported
    # `ficar` in the 2024+ oracle (a real reporting-convention change, not
    # a tax-amount change - `fica`/`tfica` are byte-identical between
    # oracle vintages for a $400k-wage single filer well past the addmtx
    # threshold; only `ficar` differs, 3.8 in taxsim2022.exe vs 2.9 in
    # taxsim2024.exe).
    r1h = marginal_wage_rate_with_se(wage1, pl.col("psemp"), hi_wage_base, se_hi_rate, net_earnings_factor)
    ficar = ((r1o + r1h) * 100).round(2)

    # addmed: Additional Medicare Tax as its own reported value (TAXSIM's
    # v44) - already folded into fica/tfica above, exposed separately too.
    return df.with_columns(fica=fica, ficar=ficar, tfica=tfica, addmed=addmtx.round(2))
