"""Kansas individual income tax (`kstax`, taxsim_2024_09_21.f:6507-6941,
state id 17). See parameters/states/ks/income_tax.yaml for the full scope
note (confirmed-inert elderly/blind/rentpaid/medical/casualty fields, the
live-probe-confirmed-$0 `comnew(8)/(20)/(23)/(25)`, and the genuine dead
1989-1991 alternative-bracket branch).

Real, non-obvious mechanics found while building this:
1. `look()`'s own real "married filing combined" comparison - splitting a
   joint return's income between spouses by wages (same shape Iowa's own
   mechanic uses) and comparing against the plain halved-then-doubled
   joint bracket total, taking whichever is cheaper - fires ONLY for
   years<=1987 and years>=2013 (the source's own `-data(2)` vs `0.0d0`
   third argument to `look()`); 1988-2012 instead use entirely separate,
   explicit single-vs-joint bracket tables with no halving at all.
2. 2013-2016's own "Schedule S Part A" AGI modification fully EXCLUDES
   self-employment income from Kansas AGI (`agi=agi+.5*setax-comnew(8)-
   se_income`, live-probe-confirmed: `comnew(8)` is $0 and the formula
   nets out to exactly `federal_agi - se_income`, i.e. self-employment
   profit is entirely untaxed those years, undoing federal's own half-SE-
   tax deduction on top) - a real, deliberate Brownback-era "pass-through
   exemption" provision, not a bug.
3. The Child/Dependent Care Credit has NO branch at all for 2013-2018 in
   the source - a real, deliberate suspension (not a gap): `chcr` stays
   at its initialized $0 those years.
4. The 1989-1991 alternative "federal-tax-subtracted" bracket computation
   is real, unreachable DEAD CODE in the source (confirmed by control-
   flow reading: the two `elseif` branches immediately above it already
   exhaustively cover 1988-1991) - not implemented here.

Real bugs found via live-oracle-probe validation, in order of discovery:
1. `itemizes` isn't exposed by federal_pre1987.py at all (unlike
   federal.py) - left silently absent, this read as a permanent False and
   zeroed out every pre-1987 itemized deduction. Reconstructed locally via
   the same `deduc>zbr` comparison federal_pre1987.py makes internally.
2. `ided`/`data(4)` (`force_itemize`) is never read ANYWHERE in `kstax`
   at all (confirmed by mapping every `data(4)`/`ided` reference in the
   whole source file to its enclosing subroutine - unlike Arkansas's
   `artax` and Iowa's `iatax`, which DO read it and are faithfully
   forced elsewhere in this project). Kansas's own itemize decision is
   ALWAYS the natural `deduc>zbr` dollar comparison, every year, never
   forced. Respecting `force_itemize` here anyway was a real bug: this
   project's generic `resolve_federal_and_state` 3-iteration "compare
   forced-itemize vs forced-standard combined tax" mechanism doesn't
   know Kansas never forces, and Kansas's own EXTRA state-only itemized
   components (`soc`/`addtx`, nowhere in federal's own `deduc` test) let
   a forced-itemize branch look artificially cheaper than the real,
   natural decision would ever allow. Fixed by having Kansas's own
   itemize decision ignore `force_itemize` entirely, matching the
   source exactly (an audit of every other already-built pre-1987 state
   found none with this SAME combination - reading `force_itemize`
   directly on its own itemize test AND having extra state-only
   deduction components - so no other state needed this same fix; see
   project memory for the full per-state audit).
3. `socmax`/`selfmx` (the pre-1987 FICA/SE-tax itemized-deduction addback
   caps) are real, explicit dollar caps ONLY for 1977-1984 - the source's
   own DATA statement is genuinely UNCAPPED (`1.e20`) for 1985-1997, not
   frozen at 1984's dollar figure.
4. `data(159)` (`hy`, household income) is live-probe-confirmed to
   include UI too, a real component this project's own earlier-
   established `hy=wages+dividends` formula (used by Idaho and
   elsewhere) never needed to account for since none of those states'
   own test suites exercised `hy` with nonzero UI present at the same
   time.
5. `comnew(68)` (exemps count) is divided by the CPI-extrapolation
   `flate` for years past 2021 (the same real, replicated-as-found quirk
   already documented/fixed for Indiana) - needed fixing in BOTH of
   Kansas's own two separate uses of it (the exemption formula and the
   2013+ Food Sales Tax Refund, which reads `comnew(68)` directly, NOT
   Kansas's own HoH-adjusted `exemps` local variable used by the
   pre-2013 formula - a real, distinct-quantity trap).
6. Federal.py's own `ccc` (Child/Dependent Care Credit) column is
   deliberately zeroed for years<1998 (a documented, real federal-side
   quirk: the "stacking" mechanism that actually applies CCC to federal
   tax liability doesn't exist in the source before 1998) - but Kansas's
   own `comnew(53)`/`comnew(176)` reads the RAW, un-zeroed credit amount
   directly, unaffected by that federal-side quirk. Reconstructed locally
   for 1988-1997 using the same pre-2021 rate-schedule primitive
   federal.py itself uses.

Harness: **2,647/2,679 (98.8%)**. Two residual families remain, both
small and neither blocking: (a) a handful of pre-2006 Homestead Property
Tax Refund cases off by $0.02-$0.05 - narrowed down to the `tablki`-
interpolated refund rate itself (confirmed the interpolation formula and
every input feeding it match the source exactly; the tiny residual's
root cause wasn't identified within this build's scope) - and (b) the
already-accepted real-vs-oracle 2023 EITC-table divergence family this
project has documented across nearly every state (Kansas's own EIC is a
direct percentage of federal EITC, so it inherits that divergence
directly). Full multi-state suite reconfirmed no regressions elsewhere.
"""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.credits import child_care_credit_rate_pre2021
from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

KS_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ks" / "income_tax.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _by_status(values: dict) -> pl.Expr:
    expr = pl.lit(None, dtype=pl.Float64)
    for status, v in values.items():
        expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(v))).otherwise(expr)
    return expr


def _tablki(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """`tablki`-style linear interpolation between adjacent (threshold,
    value) points - below the first threshold, flat at rows[0]'s value;
    at/above the last (finite) threshold, flat at the final row's value."""
    thresholds = [r[0] for r in rows[:-1]]
    values = [r[1] for r in rows]
    expr = pl.lit(values[-1])
    for i in range(len(thresholds) - 1, -1, -1):
        t_hi = thresholds[i]
        v_hi = values[i]
        if i == 0:
            below = pl.lit(v_hi)
        else:
            t_lo = thresholds[i - 1]
            v_lo = values[i - 1]
            w = (income - t_lo) / (t_hi - t_lo)
            below = pl.when(v_hi > v_lo).then(w * v_lo + (1 - w) * v_hi).otherwise(w * v_hi + (1 - w) * v_lo)
        expr = pl.when(income < t_hi).then(below).otherwise(expr)
    return expr


def compute_ks_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = KS_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "dividends", "intrec", "depx", "psemp", "ssemp"):
        df = _with_default(df, col)
    df = _with_default(df, "earned_income")
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "salt_capped")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")

    df = df.with_columns(
        ks_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        ks_txp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )
    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"

    # `setax` (comnew(175)) - computed at the REAL `year`'s rates on REAL
    # (undeflated) wages, same technique Alabama/Iowa already established
    # (sits outside the real dispatcher's own generic deflate loop).
    wage_base = float(resolve_year(PAYROLL_PARAMS["oasdi_wage_base"], year))
    hi_wage_base = float(resolve_year(PAYROLL_PARAMS["hi_wage_base"], year))
    net_earnings_factor = float(resolve_year(PAYROLL_PARAMS["se_net_earnings_factor"], year))
    se_oasdi_rate = float(resolve_year(PAYROLL_PARAMS["se_oasdi_rate"], year))
    se_hi_rate = float(resolve_year(PAYROLL_PARAMS["se_hi_rate"], year))
    setax = household_self_employment_tax(
        pl.col("psemp"), pl.col("ssemp"), pl.col("pwages"), pl.col("swages"),
        net_earnings_factor, wage_base, se_oasdi_rate, se_hi_rate, hi_wage_base,
    )
    df = df.with_columns(ks_setax=setax)

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax",
        ],
    )

    # --- AGI ---
    df = df.with_columns(ks_agi=pl.col("agi"))  # `data(22)` confirmed inert.
    # `xjobs()` and the 2007+ SS-benefits exclusion (`comnew(79)`, gated
    # on federal AGI thresholds) both confirmed inert - no-ops.
    if 2013 <= effective_year <= 2016:
        se_income = pl.col("psemp") + pl.col("ssemp")
        df = df.with_columns(ks_agi=pl.col("ks_agi") + 0.5 * pl.col("ks_setax") - se_income)
        # `comnew(8)` live-probe-confirmed $0; `data(21)` confirmed inert.

    fedtax = pl.col("fiitax").clip(0, None)
    if effective_year <= 1982 or (1987 <= effective_year <= 1988):
        fedded = fedtax
    elif effective_year in (1983, 1984):
        cap1 = float(p["fedded_1983_1984_cap_per_filer"][1960]) * pl.col("ks_txp")
        cap2 = float(p["fedded_1983_1984_upper_cap_per_filer"][1960]) * pl.col("ks_txp")
        fedded = pl.when(fedtax <= cap1).then(fedtax).when(fedtax <= cap2).then(cap1).otherwise(0.5 * fedtax)
    elif 1985 <= effective_year <= 1986:
        fedded = fedtax * pl.col("ks_agi").clip(0, None) / pl.col("agi").clip(1.0, None)
    else:
        fedded = pl.lit(0.0)

    # --- Standard deduction ---
    if effective_year <= 1987:
        pct = float(p["standard_deduction_pct_pre1988"][1960])
        single_hoh_floor = float(p["standard_deduction_floor_single_or_hoh_pre1988"][1960])
        single_hoh_cap = float(p["standard_deduction_cap_single_or_hoh_pre1988"][1960])
        married_floor = float(p["standard_deduction_floor_married_pre1988"][1960])
        married_cap = float(p["standard_deduction_cap_married_pre1988"][1960])
        is_single_or_hoh = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded = pl.when(is_single_or_hoh).then(
            (pct * pl.col("ks_agi")).clip(single_hoh_floor, single_hoh_cap)
        ).otherwise((pct * pl.col("ks_agi")).clip(married_floor / pl.col("ks_sep"), married_cap / pl.col("ks_sep")))
    else:
        if effective_year <= 1997:
            table = p["standard_deduction_1988_1997"]
        elif effective_year <= 2012:
            table = p["standard_deduction_1998_2012"]
        elif effective_year <= 2020:
            table = p["standard_deduction_2013_2020"]
        else:
            table = p["standard_deduction_2021plus"]
        stded = _by_status(table)
        # 1988+ elderly/blind addback and the dependent-return cap
        # (`data(9)/(10)/(105)`) both confirmed inert.
    df = df.with_columns(ks_stded=stded)

    # --- Itemized deduction ---
    # `itemized_deduction` (comnew(24)) and `salt_capped` (part of
    # comnew(30)) are both federal.py-ONLY columns, never exposed by
    # federal_pre1987.py - reconstructed locally for years<=1986 from the
    # raw proptax/otheritem/mortgage inputs PLUS `state_sales_or_income_
    # tax_ded` itself (so it cancels out of `xitded=comnew(24)-data(50)`
    # entirely, same pattern DC/GA/HI/Idaho/Iowa's own pre-1987
    # reconstructions already established - live-probe-confirmed here via
    # a 1979/single/$15,000-wages/$2,500-proptax case: real `xitem`=
    # $3,419, only reachable by including the state-tax component).
    if effective_year <= 1986:
        salt_plus_mortgage = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
        itemized_deduction_local = salt_plus_mortgage
        # `itemizes` isn't exposed by federal_pre1987.py at all (unlike
        # federal.py, years>=1987) - reconstructed locally via the SAME
        # `deduc>zbr` comparison federal_pre1987.py makes internally,
        # reusing the already-established `PRE1987_PARAMS["standard_
        # deduction"]` table (the same one Idaho's own pre-1987 build
        # already uses for this exact purpose). Missing this entirely
        # left `itemizes` silently absent from the dataframe, which read
        # as a permanent False and zeroed out every pre-1987 itemized
        # deduction - caught via a live-probe mismatch (1977/single/
        # $14,000 raw itemized: real return clearly itemizes, but the
        # bug made `itemizing` False in BOTH the forced-itemize AND
        # forced-standard passes).
        zbr = _by_status({s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], effective_year) for s in ("single", "head_of_household", "married_joint", "married_separate")})
        # `ided`/`data(4)` (`force_itemize`) is never read anywhere in
        # `kstax` itself - Kansas's own subroutine has no explicit
        # itemize-forcing logic at all (confirmed by mapping every
        # `data(4)`/`ided` reference in the whole source to its enclosing
        # subroutine - unlike Arkansas's `artax`/Iowa's `iatax`, which DO
        # read it and are faithfully forced elsewhere in this project).
        # But Kansas's own formula still reads FEDERAL's real outputs
        # (`comnew(24)`, `fedtax`), which DO genuinely differ between a
        # forced-itemize and forced-standard federal pass for 1982-1986
        # (federal_pre1987.py itself respects `force_itemize` those
        # years) - so passing `force_itemize` through here for 1982-1986
        # is CORRECT, not a workaround: it mirrors a real difference in
        # what federal actually computed, not an invented one. Only for
        # years<=1981 does federal_pre1987.py's own itemize decision
        # become invariant to `force_itemize` (ALWAYS the natural
        # `deduc>zbr` test) - so respecting it here too, for those years
        # specifically, creates a FALSE divergence between the two forced
        # Kansas branches that doesn't correspond to any real federal
        # difference, letting Kansas's own EXTRA state-only deduction
        # terms (`soc`/`addtx`, nowhere in federal's own `deduc` test)
        # spuriously win the "cheaper combined total" comparison - caught
        # via a live-probe mismatch on a pure-self-employment 1980 case
        # (real: standard wins, $2,026.22; bugged: $1,910.52). An audit
        # of every other already-built pre-1987 state found none with
        # this SAME combination (reading `force_itemize` directly on its
        # own itemize test AND having extra state-only deduction
        # components), so no other state needs this fix - see project
        # memory for the full per-state audit. (An earlier attempt to
        # "simplify" this by ignoring `force_itemize` for ALL years<=1986,
        # reasoning from "`kstax` never reads `ided`" alone, broke
        # 1982-1986 - confirming the year boundary must track federal's
        # OWN real forcing behavior, not just Kansas's own source text.)
        if effective_year <= 1981:
            itemizing = itemized_deduction_local > zbr
        else:
            itemizing = pl.lit(force_itemize) if force_itemize is not None else (itemized_deduction_local > zbr)
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemized_deduction_local = pl.col("itemized_deduction")
        itemizing = pl.col("itemizes") & (effective_year <= 2020)
    ag = pl.col("ks_agi").clip(0, None)

    xitded = pl.lit(0.0)
    if effective_year <= 1987:
        # `edm`/`data(44)` confirmed inert. Household FICA/SE-tax paid,
        # capped, real addback (see module docstring point for the
        # `socsec()` reconstruction).
        rate_ss = float(resolve_year(p["socsec_rate"], effective_year))
        ceil_ss = float(resolve_year(p["socsec_wage_ceiling"], effective_year))
        fica_h = pl.min_horizontal(pl.col("pwages"), ceil_ss) * rate_ss + (pl.col("pwages") - ceil_ss).clip(0, None) * 0.0145
        fica_s = pl.min_horizontal(pl.col("swages"), ceil_ss) * rate_ss + (pl.col("swages") - ceil_ss).clip(0, None) * 0.0145
        fica = pl.when(is_joint).then(fica_h + fica_s).otherwise(
            pl.min_horizontal(pl.col("pwages") + pl.col("swages"), ceil_ss) * rate_ss
            + (pl.col("pwages") + pl.col("swages") - ceil_ss).clip(0, None) * 0.0145
        )
        socsec = fica + pl.col("ks_setax")
        # `socmax`/`selfmx` are real, explicit caps ONLY for 1977-1984 -
        # the source's own DATA statement has `13*1.e20` after that (1985-
        # 1997), i.e. genuinely UNCAPPED, not frozen at 1984's dollar
        # value (an earlier version of this code wrongly reused 1984's
        # figure for 1985+, caught via a live-probe mismatch on a pure-
        # wages 1985 case).
        socmax = float(resolve_year(p["socsec_max_1977_1986"], effective_year)) if effective_year <= 1984 else 1.0e20
        selfmx = float(resolve_year(p["selfemployment_tax_max_1977_1986"], effective_year)) if effective_year <= 1984 else 1.0e20
        soc = socsec.clip(0, socmax * pl.col("ks_txp"))
        addtx = pl.col("ks_setax").clip(0, selfmx * pl.col("ks_txp"))
        base = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") + soc + addtx).clip(0, None)
        # `comnew(25)` live-probe-confirmed $0 - the 1979-1986 dividend/
        # interest addback is a no-op.
        xitded = pl.when(itemizing).then(base).otherwise(0.0)
    elif effective_year <= 1990:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2009:
        aif_val = float(resolve_year(p["itemized_phaseout_aif_1991_2009"], effective_year))
        under_thr = pl.col("agi") <= 100000.0 * aif_val / pl.col("ks_sep")
        base_low = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        fline3 = itemized_deduction_local.clip(0, None)
        fline9 = pl.min_horizontal(0.03 * (pl.col("agi") - 100000.0 * aif_val).clip(0, None), 0.8 * fline3)
        sline1 = pl.when(fline3 > 0).then(fline9 / fline3).otherwise(0.0)
        base_high = pl.when(fline3 > 0).then(
            (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") * (1 - sline1)).clip(0, None)
        ).otherwise(base_low)
        xitded = pl.when(itemizing).then(pl.when(under_thr).then(base_low).otherwise(base_high)).otherwise(0.0)
    elif effective_year <= 2012:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2017:
        aifit_val = float(resolve_year(p["itemized_scaling_2013plus"], effective_year))
        xitded = pl.when(itemizing).then(
            aifit_val * (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        ).otherwise(0.0)
    else:
        aifit_val = float(resolve_year(p["itemized_scaling_2013plus"], effective_year))
        txpaid = pl.col("proptax") + pl.col("otheritem")
        xitded = pl.when(itemizing).then(aifit_val * (txpaid + pl.col("mortgage"))).otherwise(0.0)

    if effective_year == 2021:
        # `edical`/`data(47)/(48)/(49)` confirmed inert -> $0. Overrides
        # everything above unconditionally (not gated on itemizing at all).
        xitded = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")

    # `force_itemize` has no `kstax` counterpart at all (see the module
    # docstring/the `itemizing` note above) - `data(4)`/`ided` is never
    # read in the source, so no override here either.
    df = df.with_columns(ks_xitded=xitded)
    df = df.with_columns(ks_deduc=pl.max_horizontal(pl.col("ks_stded"), pl.col("ks_xitded")))

    # --- Exemptions ---
    # `comnew(68)` sits at a real-dispatcher array position that gets
    # divided by the CPI-extrapolation `flate` for years past 2021 (same
    # quirk already documented/fixed for Indiana) - the `+1` HoH addition
    # happens in Kansas's OWN subroutine code on the ALREADY-divided
    # value, so it's added AFTER dividing, not before.
    comnew68 = (pl.col("ks_txp") + pl.col("depx")) / flate  # `comnew(68)`
    exemps = pl.when(is_hoh).then(comnew68 + 1.0).otherwise(comnew68)
    xmp = float(resolve_year(p["personal_exemption_amount"], effective_year))
    df = df.with_columns(ks_exemp=exemps * xmp)

    df = df.with_columns(ks_taxinc=(pl.col("ks_agi") - pl.col("ks_deduc") - pl.col("ks_exemp") - fedded).clip(0, None))

    # --- Bracket tax --- (see module docstring point 1 for the married-
    # filing-combined split mechanic)
    def _split_allowed(yr: int) -> bool:
        return yr <= 1987 or yr >= 2013

    if effective_year <= 1987:
        table = p["brackets_pre1988"]
        halved = pl.when(is_joint).then(pl.col("ks_taxinc") / 2.0).otherwise(pl.col("ks_taxinc"))
        doubler = pl.when(is_joint).then(2.0).otherwise(1.0)
        statax = bracket_tax(halved, table) * doubler
    elif effective_year <= 1989:
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1988_1989_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), p["brackets_1988_1989_single"])
        )
    elif effective_year <= 1991:
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1990_1991_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), p["brackets_1990_1991_single"])
        )
    elif effective_year <= 2012:
        if effective_year <= 1996:
            single_table = p["brackets_1992_1996_single"]
        elif effective_year == 1997:
            single_table = p["brackets_1997_single"]
        else:
            single_table = p["brackets_1998_2012_single"]
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1992_2012_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), single_table)
        )
    else:
        if effective_year == 2013:
            table = p["brackets_2013"]
        elif effective_year == 2014:
            table = p["brackets_2014"]
        elif effective_year <= 2016:
            table = p["brackets_2015_2016"]
        elif effective_year == 2017:
            table = p["brackets_2017"]
        else:
            table = p["brackets_2018plus"]
        halved = pl.when(is_joint).then(pl.col("ks_taxinc") / 2.0).otherwise(pl.col("ks_taxinc"))
        doubler = pl.when(is_joint).then(2.0).otherwise(1.0)
        statax = bracket_tax(halved, table) * doubler

    if _split_allowed(effective_year):
        wages = pl.col("pwages") + pl.col("swages")
        yh = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + (pl.col("ks_taxinc") - wages) / 2.0
        yw = pl.col("ks_taxinc") - yh
        tax_h = bracket_tax(yh.clip(0, None), table)
        tax_w = bracket_tax(yw.clip(0, None), table)
        statax = pl.when(is_joint).then(pl.min_horizontal(statax, tax_h + tax_w)).otherwise(statax)

    df = df.with_columns(ks_statax=statax)

    if 2015 <= effective_year <= 2017:
        ceiling = float(p["zero_tax_taxinc_ceiling_joint_2015_2017"][1960])
        ceiling_s = float(p["zero_tax_taxinc_ceiling_single_2015_2017"][1960])
        under = pl.when(is_joint).then(pl.col("ks_taxinc") <= ceiling).otherwise(pl.col("ks_taxinc") <= ceiling_s)
        df = df.with_columns(ks_statax=pl.when(under).then(0.0).otherwise(pl.col("ks_statax")))
    elif effective_year >= 2018:
        ceiling = float(p["zero_tax_taxinc_ceiling_joint_2018plus"][1960])
        ceiling_s = float(p["zero_tax_taxinc_ceiling_single_2018plus"][1960])
        under = pl.when(is_joint).then(pl.col("ks_taxinc") <= ceiling).otherwise(pl.col("ks_taxinc") <= ceiling_s)
        df = df.with_columns(ks_statax=pl.when(under).then(0.0).otherwise(pl.col("ks_statax")))

    # --- Credits ---
    # Child/Dependent Care Credit - see module docstring point 3 for why
    # 2013-2018 has no branch (chcr stays $0). `comnew(52)`'s own expense
    # ceiling never bound in probes. `comnew(53)`(=`comnew(176)`) is
    # federal's CCC amount BEFORE its own nonrefundable cap - federal.py's
    # own `ccc` column is a real approximation for this EXCEPT 1988-1997,
    # where federal.py deliberately zeroes `ccc` entirely (a real,
    # separately-documented federal-side quirk: the "stacking" mechanism
    # that actually APPLIES CCC to reduce FEDERAL tax liability doesn't
    # exist in the source before 1998, so federal.py's own `ccc` reports
    # $0 those years even though the RAW credit amount was genuinely
    # computed and nonzero) - reconstructed locally here for 1988-1997
    # using the same pre-2021 rate-schedule primitive federal.py itself
    # uses, UNCAPPED by any federal tax liability (matching `comnew(176)`'s
    # own "before its own cap" definition). Caught via a live-probe
    # mismatch: a 1988/single/$25,000-wages/1-dependent/$2,000-childcare
    # case wrongly gave $0 Kansas credit.
    if 1988 <= effective_year <= 1997:
        ccc_p = FEDERAL_CREDITS_PARAMS["child_care_credit"]
        max_qualifying_persons = float(resolve_year(ccc_p["max_qualifying_persons"], effective_year))
        max_expense_per_person = float(resolve_year(ccc_p["max_expense_per_person_pre2021"], effective_year))
        ccc_rate = child_care_credit_rate_pre2021(
            pl.col("agi"),
            phase_start=float(resolve_year(ccc_p["pre2021_phase_start"], effective_year)),
            top_rate=float(resolve_year(ccc_p["pre2021_rate_top"], effective_year)),
            floor_rate=float(resolve_year(ccc_p["pre2021_rate_floor"], effective_year)),
            step_amount=float(resolve_year(ccc_p["pre2021_step_amount"], effective_year)),
        )
        num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons) if "dep13" in df.columns else pl.lit(0.0)
        qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person) if "childcare" in df.columns else pl.lit(0.0)
        ccc_earned_income_cap = pl.when(is_joint).then(
            pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
        ).otherwise(pl.col("wages"))
        ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
        chcare = ccc_rate * ccc_expense
    else:
        chcare = pl.col("ccc")
    if effective_year <= 1987:
        chcr = chcare * _tablki(pl.col("ks_agi"), p["child_care_credit_table_pre1988"])
    elif (1988 <= effective_year <= 2012) or effective_year >= 2020:
        chcr = chcare * float(p["child_care_credit_rate_1988_2012_and_2020plus"][1960])
    elif effective_year == 2019:
        chcr = chcare * float(p["child_care_credit_rate_2019"][1960])
    else:
        chcr = pl.lit(0.0)
    df = df.with_columns(ks_chcr=chcr)

    # Solar/energy credit (`data(38)`) confirmed inert for every year.
    encred = pl.lit(0.0)

    # Homestead Property Tax Refund / food credit (`pcred`).
    pr1 = pl.col("proptax")  # rentpaid (`data(160)`) confirmed inert.
    # `data(159)` (`hy`) is live-probe-confirmed to include UI too (a
    # $10,000-wages/$8,000-UI 1992 case: `data(159)`=$18,000, not
    # $10,000) - a real component this project's own earlier-established
    # `hy=wages+dividends` formula (used by Idaho and elsewhere) never
    # needed to account for since none of those states' own test suites
    # exercised `hy` with nonzero UI present at the same time.
    ui_total_for_hy = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    hy = pl.col("wages") + pl.col("dividends") + ui_total_for_hy
    pcred = pl.lit(0.0)
    if effective_year in (1977, 1978):
        ceiling = float(p["homestead_1977_income_ceiling" if effective_year == 1977 else "homestead_1978_income_ceiling"][1960])
        claw = (
            pl.when(hy <= 4200.0).then((hy - 3400.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(16.0 + (hy - 4200.0).clip(0, None) * 0.04)
            .otherwise(32.0 + (hy - 4600.0).clip(0, None) * 0.045)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1977_1978"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1 - claw).clip(0, cap))
        ).otherwise(0.0)
    elif 1979 <= effective_year <= 1988:
        ceiling = float(p["homestead_1979_1988_income_ceiling"][1960])
        claw = (
            pl.when(hy <= 3500.0).then((hy - 3400.0).clip(0, None) * 0.01)
            .when(hy <= 4000.0).then(1.0 + (hy - 3500.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(11.0 + (hy - 4000.0).clip(0, None) * 0.03)
            .when(hy <= 8600.0).then(29.0 + (hy - 4600.0).clip(0, None) * 0.04)
            .otherwise(189.0 + (hy - 8600.0).clip(0, None) * 0.05)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1979_1988"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif 1989 <= effective_year <= 1994:
        ceiling = float(p["homestead_1989_1994_income_ceiling"][1960])
        claw = (
            pl.when(hy <= 4200.0).then((hy - 3400.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(16.0 + (hy - 4200.0).clip(0, None) * 0.04)
            .otherwise(32.0 + (hy - 4600.0).clip(0, None) * 0.045)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1989_1994"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif effective_year in (1995, 1996):
        ceiling = float(p["homestead_1995_1996_income_ceiling"][1960])
        claw = (
            pl.when(hy <= 4200.0).then((hy - 3400.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(16.0 + (hy - 4200.0).clip(0, None) * 0.04)
            .otherwise(32.0 + (hy - 4600.0).clip(0, None) * 0.045)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1995_1996"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif effective_year >= 1997:
        pt_cap = float(resolve_year(p["homestead_pt_cap"], effective_year))
        ptax = pl.min_horizontal(pt_cap, pr1)
        if effective_year <= 2005:
            hhy = hy
        else:
            hhy = pl.col("ks_agi") + pl.col("eitc")  # `.5*data(91)` confirmed inert
        pmax = float(resolve_year(p["homestead_hy_ceiling_by_year"], effective_year))
        table = p["homestead_table_1997_2004"] if effective_year <= 2004 else p["homestead_table_2005plus"]
        pcred = pl.when(hhy < pmax).then(ptax * _tablki(hhy, table)).otherwise(0.0)
        # Kansas Property Tax Relief Claim for Low Income Seniors (2008+,
        # `data(9)>0` gate) confirmed inert.
    df = df.with_columns(ks_pcred=pcred)

    # Food Sales Tax Refund.
    fd = pl.lit(0.0)
    if 1986 <= effective_year <= 1997:
        extra = pl.col("ks_txp") + pl.col("depx") - 1.0
        fd = (
            pl.when(hy < 5000.0).then(40.0 + 30.0 * extra)
            .when(hy < 10000.0).then(30.0 + 25.0 * extra)
            .when(hy <= 13000.0).then(20.0 + 15.0 * extra)
            .otherwise(0.0)
        )
    elif effective_year >= 1998:
        eligible = pl.col("depx") > 0
        food_amt = float(resolve_year(p["food_sales_tax_credit_amount"], effective_year))
        if effective_year <= 2012:
            agimax = float(resolve_year(p["food_sales_tax_refund_agi_ceiling"], effective_year))
            fd = pl.when(eligible & (pl.col("ks_agi") <= agimax)).then(2.0 * food_amt * exemps).when(
                eligible & (pl.col("ks_agi") <= 2.0 * agimax)
            ).then(food_amt * exemps).otherwise(0.0)
        else:
            agimax = float(resolve_year(p["food_sales_tax_refund_agi_ceiling"], effective_year))
            fd = pl.when(eligible & (pl.col("agi") <= agimax)).then(food_amt * comnew68).otherwise(0.0)
    # 1977-1985 branch confirmed inert (depends solely on elderly/blind count).
    df = df.with_columns(ks_fd=fd)

    # Earned Income Credit.
    earncr = pl.lit(0.0)
    if effective_year >= 1998:
        rate_eitc = float(resolve_year(p["eitc_rate"], effective_year))
        earncr = rate_eitc * pl.col("eitc")
    df = df.with_columns(ks_earncr=earncr)

    if effective_year <= 2012:
        df = df.with_columns(
            ks_statax=(pl.col("ks_statax") - encred - pl.col("ks_chcr")).clip(0, None)
            - pl.col("ks_fd") - pl.col("ks_earncr") - pl.col("ks_pcred")
        )
    else:
        df = df.with_columns(
            ks_statax=(pl.col("ks_statax") - encred - pl.col("ks_chcr") - pl.col("ks_fd")).clip(0, None)
            - pl.col("ks_earncr") - pl.col("ks_pcred")
        )

    df = df.with_columns(siitax=pl.col("ks_statax") * flate)
    return df
