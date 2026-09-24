"""Kentucky individual income tax (`kytax`, taxsim_2024_09_21.f:6945-7219,
state id 18). See parameters/states/ky/income_tax.yaml for the full scope
note (confirmed-inert Social-Security/pension/IRA/self-employed-health-
insurance/charity/investment-interest fields, the genuinely vestigial
Income Gap Tax Credit, and the real 2010-2012 phaseout-suspension bug).

Real, non-obvious mechanics found while building this:
1. For years<=1989, Kentucky's own AGI SUBTRACTS federal tax liability
   itself (`agi=comnew(2)-fedtax`, not a taxable-income deduction) -
   `fedtax` here is Kentucky's OWN reconstruction
   (`fiitax-setax-max(0,amt-0)`), not `comnew(1)`/`fiitax` directly. For
   1990 specifically, a DIFFERENT (simpler) subtraction applies
   (`agi=fed_agi-fiitax`, using raw `fiitax`, not the setax/amt-adjusted
   `fedtax`) - a real, narrow one-year transition quirk between the
   pre-1990 and 1991+ AGI regimes.
2. A real 60% long-term-capital-gains exclusion for 1987-1989 only.
3. The 1990-2004 Low Income Credit and 2005+ Family Size Tax Credit are
   mutually exclusive ERAS of the SAME underlying idea (not layered) -
   but BOTH run alongside the separately-computed Personal Tax Credit
   (`gcred`), which is computed and subtracted from `statax` FIRST,
   unconditionally, before either era-specific credit's own subtraction.
4. Kentucky's own "married filing combined" income split (own explicit
   `agih`/`agiw` computation, NOT the `look()`-wrapper `-data(2)`
   mechanic Kansas's own build just used) only runs through 2017 - 2018+
   is a flat 5% rate, so bracket-compression (the whole reason the split
   exists) can't happen there at all.

Real bugs found via live-oracle-probe validation, in order of discovery:
1. The Low Income Credit's own AGI-bracket rate lookup was built as a
   cascading overwrite (checking brackets broadest-first), which let
   every broader/later bracket clobber a narrower/earlier match instead
   of stopping at the first true one - since a LOW AGI satisfies every
   bracket's own ceiling test simultaneously, this collapsed the whole
   schedule down to always the LAST (highest-ceiling, $0-rate) bracket,
   silently zeroing the credit for every filer. Fixed by iterating the
   brackets narrowest-first so a true match from a later (narrower)
   iteration correctly overrides an earlier (broader) one.
2. The 1987-1989 "keep the old federal dividend exclusion" line
   (`agi=agi-divexc(...)`) turned out to be REDUNDANT with, not
   additional to, the real `agi -= min(dividends,100*txp)` line right
   below it in the source (which already covers 1987-1989 as part of its
   own law<=1997 range) - live-probe-confirmed via a single-filer
   dividends case where implementing both stacked two $100 exclusions
   instead of one. Not implemented as a separate term.
3. Kentucky's own reconstructed `fedtax` (`fiitax-setax-max(0,amt)`) and
   1990's own separate `subtra` addition both wrongly clipped `fiitax`
   to non-negative before using it - but the source's ONLY clamp-to-zero
   wraps the WHOLE expression, not `fiitax` alone, and a genuinely
   negative (refundable) `fiitax` really does add back to AGI. Caught via
   a live-probe mismatch on a 1990/HoH/$5,000-wages/1-dependent case with
   `fiitax`=-$700: AGI needed the full +$700 added back, not $0.

Harness: **2,583/2,585 (99.9%)**. The 2 residuals are both 2022/2023
(CPI-extrapolation years) Family Size Tax Credit cases, small in
magnitude - every input feeding the credit's own modified-AGI comparison
was confirmed correctly deflated, but the tiny residual's exact source
wasn't identified within this build's scope (flagged for a future
revisit, same as Kansas's own small Homestead Refund residual). Full
multi-state suite reconfirmed no regressions elsewhere.
"""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.calculators.federal import compute_regular_tax
from taxsim_py.engine.credits import child_care_credit_rate_pre2021
from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

KY_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ky" / "income_tax.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")

_RAW_INPUT_COLUMNS = [
    "mstat", "depx", "dep17", "dep18", "dep6", "dep13", "pwages", "swages",
    "proptax", "otheritem", "mortgage", "childcare", "intrec", "psemp",
    "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui",
]


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


def _raw_ccc(df: pl.DataFrame, year: int) -> pl.Expr:
    """`comnew(53)`/`comnew(176)` - federal's own CCC amount BEFORE its
    nonrefundable cap. federal.py's own `ccc` column deliberately reports
    $0 for years<1998 (a real, separately-documented federal-side quirk
    about the credit-STACKING mechanism, not the credit computation
    itself) - reconstructed locally here for 1987-1997 using federal.py's
    own pre-2021 rate-schedule primitive, matching the SAME technique
    Kansas's own build just established for this exact gap."""
    ccc_p = FEDERAL_CREDITS_PARAMS["child_care_credit"]
    max_qualifying_persons = float(resolve_year(ccc_p["max_qualifying_persons"], year))
    max_expense_per_person = float(resolve_year(ccc_p["max_expense_per_person_pre2021"], year))
    ccc_rate = child_care_credit_rate_pre2021(
        pl.col("agi"),
        phase_start=float(resolve_year(ccc_p["pre2021_phase_start"], year)),
        top_rate=float(resolve_year(ccc_p["pre2021_rate_top"], year)),
        floor_rate=float(resolve_year(ccc_p["pre2021_rate_floor"], year)),
        step_amount=float(resolve_year(ccc_p["pre2021_step_amount"], year)),
    )
    num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons)
    qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person)
    ccc_earned_income_cap = pl.when(pl.col("filing_status") == "married_joint").then(
        pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
    ).otherwise(pl.col("wages"))
    ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
    return ccc_rate * ccc_expense


def compute_ky_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = KY_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "dividends", "intrec", "depx", "dep13", "childcare"):
        df = _with_default(df, col)
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "salt_capped")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")
    df = _with_default(df, "amt")

    df = df.with_columns(
        ky_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        ky_txp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )
    is_joint = pl.col("filing_status") == "married_joint"

    # `setax` (comnew(175)) - computed at the REAL `year`'s rates on REAL
    # (undeflated) wages, same technique Alabama/Iowa/Kansas already
    # established.
    for col in ("psemp", "ssemp"):
        df = _with_default(df, col)
    wage_base = float(resolve_year(PAYROLL_PARAMS["oasdi_wage_base"], year))
    hi_wage_base = float(resolve_year(PAYROLL_PARAMS["hi_wage_base"], year))
    net_earnings_factor = float(resolve_year(PAYROLL_PARAMS["se_net_earnings_factor"], year))
    se_oasdi_rate = float(resolve_year(PAYROLL_PARAMS["se_oasdi_rate"], year))
    se_hi_rate = float(resolve_year(PAYROLL_PARAMS["se_hi_rate"], year))
    setax = household_self_employment_tax(
        pl.col("psemp"), pl.col("ssemp"), pl.col("pwages"), pl.col("swages"),
        net_earnings_factor, wage_base, se_oasdi_rate, se_hi_rate, hi_wage_base,
    )
    df = df.with_columns(ky_setax=setax)

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax", "amt", "wages",
        ],
    )

    phas92_base = float(p["itemized_phaseout_base"][1960])
    phas92 = phas92_base / pl.col("ky_sep")
    if 1992 <= effective_year <= 2017:
        aif92 = float(resolve_year(p["itemized_phaseout_aif92_1992_2017"], effective_year))
        phas92 = phas92_base * aif92 / pl.col("ky_sep")

    # Kentucky's own reconstructed "fedtax" (real for 1977-1989's AGI
    # formula and the pre-1990 low-income child deduction's `ytest` gate).
    # `fedtax=max(0,comnew(1)-comnew(175)-max(0,comnew(70)-comnew(28)))` -
    # the ONLY clamp-to-zero is the OUTER one; `comnew(1)`/`fiitax` itself
    # is NOT separately clipped (it can be genuinely negative, e.g. from a
    # refundable EITC, and that negativity matters to callers of
    # `ky_fedtax` below - clipping it here was a real bug, caught via a
    # live-probe mismatch on a 1990/HoH/$5,000-wages/1-dependent case
    # with `fiitax`=-$700: AGI should include the full +$700 back, not $0).
    ky_fedtax = (pl.col("fiitax") - pl.col("ky_setax") - pl.col("amt").clip(0, None)).clip(0, None)

    # `excli` - 1985's own interest-income exclusion.
    excli_cap = float(p["interest_exclusion_1985_cap_per_filer"][1960])
    excli = 0.15 * pl.col("intrec").clip(0, excli_cap * pl.col("ky_txp"))  # `data(57)` confirmed inert.

    # --- AGI ---
    if effective_year <= 1989:
        agi = pl.col("agi") - ky_fedtax - (excli if effective_year == 1985 else 0.0)
    else:
        # 1990's own `subtra` addition uses raw `fiitax` (`comnew(1)`)
        # directly, NOT clamped to non-negative - a genuinely negative
        # (refundable) `fiitax` really does ADD back to AGI here (same
        # live-probe finding as `ky_fedtax` above).
        subtra = 0.0 if effective_year != 1990 else pl.col("fiitax")
        agi = pl.col("agi") - subtra  # `data(124)`/`data(22)` confirmed inert.

    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    has_ui = (df.get_column("ui").abs().sum() + df.get_column("pui").abs().sum() + df.get_column("sui").abs().sum()) > 0
    if effective_year in (2009, 2020):
        if has_ui:
            df_no_ui = df.select(_RAW_INPUT_COLUMNS).with_columns(ui=pl.lit(0.0), pui=pl.lit(0.0), sui=pl.lit(0.0))
            fed_no_ui = compute_regular_tax(df_no_ui, effective_year)
            untax = pl.col("agi") - fed_no_ui.get_column("agi")
        else:
            untax = pl.lit(0.0)
        agi = agi + ui_total - untax
    if effective_year == 2020:
        # `data(58)` (charity_cash) confirmed inert - no-op.
        agi = pl.when(~pl.col("itemizes")).then(agi + pl.min_horizontal(300.0, 0.0)).otherwise(agi)
    # `comnew(79)` (SS-in-AGI, law>=1984) confirmed inert.
    # `agi=agi-divexc(...)` for 1987-1989 (source comment: "ky keeps
    # after 86 through 1989" the old pre-TRA86 federal dividend
    # exclusion) turned out to be REDUNDANT with, not additional to, the
    # real `agi -= min(dividends,100*txp)` line right below (which
    # already covers law<=1997, i.e. already includes 1987-1989) - a
    # live-probe mismatch (single/$5,000 dividends, 1987-1989: real tax
    # implies exactly ONE $100 exclusion, not two stacked) confirmed
    # `divexc()`'s own reconstruction here would have double-counted it,
    # so it's intentionally NOT implemented as a separate term.
    if effective_year <= 1997:
        agi = agi - (pl.col("dividends") + 0.001).clip(0, 100.0 * pl.col("ky_txp"))
    if 1982 <= effective_year <= 1986:
        rate_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        cap_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        lesser_wage = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        twoded = pl.when(is_joint).then((rate_2e * lesser_wage).clip(0, cap_2e)).otherwise(0.0)
        agi = agi + twoded
    # `data(29)` (IRA) confirmed inert. Retirement-income exclusion
    # (`data(20)`/`data(72)`) confirmed inert.
    if 1987 <= effective_year <= 1989:
        # 60% LTCG exclusion, gated on a positive net capital gain in AGI.
        capgn = pl.col("stcg") + pl.col("ltcg")
        ltcg_rate = float(p["ltcg_exclusion_rate_1987_1989"][1960])
        agi = pl.when(capgn > 0).then(agi - ltcg_rate * pl.col("ltcg").clip(0, None)).otherwise(agi)

    df = df.with_columns(ky_agi=agi)

    # --- Standard deduction ---
    ded_pf = float(resolve_year(p["standard_deduction_per_filer"], effective_year))
    df = df.with_columns(ky_stded=ded_pf * pl.col("ky_txp"))
    # 1982-1986 non-itemizer charitable addback (`data(58)`/`(59)`) confirmed inert.

    # --- Itemized deduction ---
    if effective_year <= 1986:
        salt_plus_mortgage = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
        itemized_deduction_local = salt_plus_mortgage
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemized_deduction_local = pl.col("itemized_deduction")

    if effective_year <= 2017:
        xitded = (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        over_thr = (pl.col("ky_agi") > phas92) & (1991 <= effective_year <= 2017)
        reduce_ = pl.min_horizontal(0.8 * xitded, 0.03 * (pl.col("ky_agi") - phas92).clip(0, None))
        if 2006 <= effective_year <= 2007:
            reduce_ = 2.0 * reduce_ / 3.0
        elif 2008 <= effective_year <= 2009:
            reduce_ = reduce_ / 3.0
        elif effective_year == 2010:
            # Real, replicated-as-found source bug: `law.eq.2010.and.
            # law.le.2012` can only both be true for law==2010, NOT
            # 2011-2012 too (almost certainly meant `law.ge.2010`).
            reduce_ = pl.lit(0.0)
        xitded = pl.when(over_thr).then(xitded - reduce_).otherwise(xitded)

        if effective_year <= 1989:
            ytest = pl.col("ky_agi") + ky_fedtax
            cap1 = float(p["low_income_child_deduction_cap_1dep"][1960])
            cap2 = float(p["low_income_child_deduction_cap_2deps"][1960])
            cap3 = float(p["low_income_child_deduction_cap_3plus_deps"][1960])
            ceiling = float(p["low_income_child_deduction_income_ceiling"][1960])
            phaseout_start = float(p["low_income_child_deduction_phaseout_start"][1960])
            raw_ccc = _raw_ccc(df, effective_year) if effective_year >= 1987 else pl.col("ccc")
            child = (
                pl.when((pl.col("depx") > 0) & (pl.col("depx") < 2)).then(pl.min_horizontal(cap1, raw_ccc))
                .when((pl.col("depx") > 1) & (pl.col("depx") < 3)).then(pl.min_horizontal(cap2, raw_ccc))
                .when(pl.col("depx") > 2).then(pl.min_horizontal(cap3, raw_ccc))
                .otherwise(0.0)
            )
            child = pl.when(ytest >= phaseout_start).then((child - (ytest - phaseout_start) / 2.0).clip(0, None)).otherwise(child)
            child = pl.when(ytest <= ceiling).then(child).otherwise(0.0)
            xitded = xitded + child
    else:
        # `comnew(23)`/`data(66)` confirmed inert.
        xitded = pl.col("mortgage")

    if force_itemize is False and effective_year == 1999:
        xitded = pl.lit(0.0)

    df = df.with_columns(ky_xitded=xitded)
    df = df.with_columns(ky_deduc=pl.max_horizontal(pl.col("ky_stded"), pl.col("ky_xitded")))
    df = df.with_columns(ky_taxinc=(pl.col("ky_agi") - pl.col("ky_deduc")).clip(0, None))

    # --- Married filing combined --- (own explicit split, real through 2017)
    is_mfc = is_joint & (pl.col("ky_agi") > 0) & (effective_year <= 2017)
    wages = pl.col("pwages") + pl.col("swages")
    agih = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + 0.5 * (pl.col("ky_agi") - wages)
    agiw = pl.col("ky_agi") - agih
    xitdh = pl.when(pl.col("ky_agi") != 0).then(pl.col("ky_xitded") * agih / pl.col("ky_agi")).otherwise(0.0)
    xitdw = pl.col("ky_xitded") - xitdh
    dedh = pl.max_horizontal(pl.col("ky_stded"), xitdh)
    dedw = pl.max_horizontal(pl.col("ky_stded"), xitdw)
    taxinh = pl.when(is_mfc).then((agih - dedh).clip(0, None)).otherwise(pl.lit(0.0))
    taxinw = pl.when(is_mfc).then((agiw - dedw).clip(0, None)).otherwise(pl.lit(0.0))
    df = df.with_columns(ky_taxinh=taxinh, ky_taxinw=taxinw)

    # --- Bracket tax ---
    if effective_year <= 2004:
        table = p["brackets_pre2005"]
        statax = bracket_tax(pl.col("ky_taxinc"), table)
        stath = bracket_tax(pl.col("ky_taxinh"), table)
        statw = bracket_tax(pl.col("ky_taxinw"), table)
        statax = pl.when(is_mfc).then(pl.min_horizontal(statax, stath + statw)).otherwise(statax)
    elif effective_year <= 2017:
        table = p["brackets_2005_2017"]
        statax = bracket_tax(pl.col("ky_taxinc"), table)
        stath = bracket_tax(pl.col("ky_taxinh"), table)
        statw = bracket_tax(pl.col("ky_taxinw"), table)
        statax = pl.when(is_mfc).then(pl.min_horizontal(statax, stath + statw)).otherwise(statax)
    else:
        flat_rate = float(p["flat_rate_2018plus"][1960])
        statax = flat_rate * pl.col("ky_taxinc")
    df = df.with_columns(ky_statax=statax)

    # --- Credits ---
    if effective_year <= 1989:
        # 1984-1986 solar credit (`data(38)`) confirmed inert.
        df = df.with_columns(ky_statax=pl.col("ky_statax").clip(0, None))
    else:
        if effective_year < 2004:
            amt = float(p["personal_credit_amount_pre2004"][1960])
            count = pl.col("ky_txp") + pl.col("depx")  # elderly/blind terms confirmed inert.
            gcred = amt * count
        elif 2014 <= effective_year <= 2017:
            amt = float(p["personal_credit_amount_2014_2017"][1960])
            gcred = amt * (pl.col("ky_txp") + pl.col("depx"))
        else:
            gcred = pl.lit(0.0)  # 2018+ elderly/blind-only credit confirmed inert.
        df = df.with_columns(ky_statax=(pl.col("ky_statax") - gcred).clip(0, None))

        raw_ccc_now = _raw_ccc(df, effective_year) if effective_year <= 1997 else pl.col("ccc")
        chcr_rate = float(p["low_income_care_credit_rate"][1960])
        chcr = raw_ccc_now * chcr_rate

        if 1990 <= effective_year <= 2004:
            # Brackets checked narrowest-first (a real elif chain, not a
            # cascading overwrite - broader/later brackets must NOT clobber
            # a narrower/earlier match, since every ceiling above the
            # true one is ALSO satisfied by a low AGI).
            rate_lc = pl.lit(0.0)
            for ceiling, rate in reversed(p["low_income_credit_brackets"]):
                rate_lc = pl.when(pl.col("ky_agi") <= ceiling).then(pl.lit(float(rate))).otherwise(rate_lc)
            lowcrd = pl.col("ky_statax") * rate_lc
            credit = chcr + lowcrd
            df = df.with_columns(ky_statax=(pl.col("ky_statax") - credit).clip(0, None))
        elif effective_year >= 2005:
            num = pl.min_horizontal(pl.col("ky_txp") + pl.col("depx"), 4.0)  # elderly term confirmed inert.
            modagi = pl.max_horizontal(pl.col("ky_agi"), pl.col("agi"))
            aif1 = float(resolve_year(p["family_size_credit_aif_1person"], effective_year))
            aif2 = float(resolve_year(p["family_size_credit_aif_2person"], effective_year))
            aif3 = float(resolve_year(p["family_size_credit_aif_3person"], effective_year))
            aif4 = float(resolve_year(p["family_size_credit_aif_4person"], effective_year))
            perc = (
                pl.when(num == 1).then(_tablki(modagi / aif1, p["family_size_credit_table_1person"]))
                .when(num == 2).then(_tablki(modagi / aif2, p["family_size_credit_table_2person"]))
                .when(num == 3).then(_tablki(modagi / aif3, p["family_size_credit_table_3person"]))
                .when(num == 4).then(_tablki(modagi / aif4, p["family_size_credit_table_4person"]))
                .otherwise(0.0)
            )
            famcr = pl.col("ky_statax") * perc
            # The 2019-2020 Income Gap Tax Credit is real but genuinely
            # vestigial for `siitax` - see module docstring.
            df = df.with_columns(ky_statax=(pl.col("ky_statax") - famcr - chcr).clip(0, None))

    df = df.with_columns(siitax=pl.col("ky_statax") * flate)
    return df
