"""Louisiana individual income tax (`latax`, taxsim_2024_09_21.f:7224-7431,
state id 19). See parameters/states/la/income_tax.yaml for the full scope
note (confirmed-inert Social-Security/pension/blind fields, and the
acknowledged `comnew(54)` gap).

Real, non-obvious mechanics found while building this:
1. `comnew(52)` is live-probe-confirmed to be federal's own `regular_tax`
   (NOT `tax_before_credits`, which already bakes AMT in) - `comnew(70)`
   (AMT) is added separately in the same "federal income tax deduction"
   formula, so using `tax_before_credits` here would double-count AMT.
2. `comnew(58)` is live-probe-confirmed to be the COMBINED CCC+ODC/CTC
   nonrefundable-credit total (federal.py's own `ccc`+`odc` columns
   summed), not either alone - confirmed via a case where the
   nonrefundable CTC was capped by remaining tax liability after CCC,
   and `comnew(58)` exactly equaled `ccc + capped_odc`.
3. The entire retirement-income/Social-Security-benefits AGI exclusion
   mechanism, though real in the source, is confirmed permanently inert
   for this schema on TWO independent grounds at once (both `data(20)`/
   `data(72)`/`comnew(79)` confirmed inert AND the outer gate itself,
   `subtr.gt.0`, can never fire since `subtr` is built entirely from
   those same inert quantities) - collapsing AGI down to simply federal
   AGI unconditionally.
4. The "Excess federal Itemized deductions" scaling has NO branch at all
   for 2003-2006 - a real, deliberate gap in the source itself (deduc
   stays $0 those years even while itemizing), matching the same "no
   branch = no-op" pattern already documented for Kentucky's own Child
   Care Credit suspension.
5. Louisiana combines its standard deduction and personal exemption into
   ONE flat dollar figure by filing status, and separately gives a real,
   distinctive PER-DEPENDENT reduction applied directly to the computed
   tax itself (not to AGI or the deduction) - with a genuinely different,
   more complex three-tier formula for head_of_household specifically
   (2003+) than the simple single-tier one single/married/HoH(pre-2003)
   all share.
6. The refundable Earned Income Credit (2008+) has NO floor at $0 - it
   can drive `statax` negative, unlike every earlier credit in the same
   subroutine, which are all explicitly capped at $0.
7. `comnew(52)` is a genuinely DIFFERENT quantity across federal
   vintages despite being the same array slot: for 1980-1986
   (federal_pre1987.py) it equals `fiitax` directly; for 1987+
   (federal.py) it equals `regular_tax` instead. Not a single uniform
   formula across the whole `law>=1980` range the source's own `if`
   groups together.
8. ARPA made BOTH the Child Care Credit and Child Tax Credit fully
   refundable for 2021 only, with no tax-liability cap at all - so
   NONE of `ccc`/`odc` actually reduced `regular_tax` that year, even
   though the columns still report their full (refundable) amounts.
   This carve-out is gated on the RAW requested year, not
   `effective_year` - for 2022/2023 (CPI-extrapolated), federal.py
   itself still computes `ccc`/`odc` at the real requested year's own
   ordinary (non-ARPA) rules, so the normal subtraction is still
   correct there even though the STATE formula runs at `effective_
   year=2021`.

Real bugs found via live-oracle-probe validation, in order of discovery:
1. `comnew(28)` (<=1979) is federal's own `regular_tax`, NOT `fiitax` -
   caught via a live-probe mismatch showing a real EITC gap between the
   two that LA's own deduction must not reflect.
2. `itemized_deduction` (comnew(24)) isn't exposed by federal_pre1987.py
   for years<=1986 - left at its silent $0 default, this made the
   "excess federal itemized deductions" term permanently $0 for
   1980-1986 regardless of `force_itemize`, since neither forced branch
   could ever produce a real itemized total. Reconstructed locally, same
   pattern every other pre-1987 state build already established.
3. Federal.py's own `ccc` column is deliberately zeroed for years<1998
   (a federal-side quirk, not a computation gap) - but Louisiana's own
   10%-of-CCC credit component reads the RAW, un-zeroed amount, needing
   the same local 1987-1997 reconstruction Kansas/Kentucky already
   established, PLUS a further substitution for years<=1986 specifically
   (federal_pre1987.py's own `credit` column already IS the CCC-
   equivalent there, reused directly rather than reconstructed).
4. See module docstring point 8 - the 2021 ARPA full-refundability
   carve-out.

Harness: **2,666/2,679 (99.5%)**. The 13 residuals are all 2023-only,
small, and match the already-accepted real-vs-oracle 2023 EITC-table
divergence family documented across nearly every state built this
session. Full multi-state suite reconfirmed no regressions elsewhere.
"""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.credits import child_care_credit_rate_pre2021
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

LA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "la" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")


def _raw_ccc(df: pl.DataFrame, year: int) -> pl.Expr:
    """`comnew(53)` - federal's own CCC amount. federal.py's own `ccc`
    column deliberately reports $0 for years<1998 (a real, separately-
    documented federal-side quirk about the credit-STACKING mechanism,
    not the credit computation itself) - reconstructed locally here for
    1987-1997 using federal.py's own pre-2021 rate-schedule primitive,
    matching the SAME technique Kansas/Kentucky's own builds already
    established for this exact gap."""
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


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def compute_la_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = LA_PARAMS
    for col in ("proptax", "depx", "childcare"):
        df = _with_default(df, col)
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "odc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")
    df = _with_default(df, "regular_tax")
    df = _with_default(df, "amt")

    df = df.with_columns(
        la_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        la_txp=pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"])).then(2.0).otherwise(1.0),
    )
    is_single_or_sep = pl.col("filing_status").is_in(["single", "married_separate"])
    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "eitc", "ccc", "odc",
            "itemized_deduction", "fiitax", "regular_tax", "amt",
        ],
    )

    # --- AGI --- (see module docstring point 3 - the retirement/SS
    # exclusion mechanism is confirmed permanently inert for this schema)
    df = df.with_columns(la_agi=pl.col("agi"))

    # --- "Excess federal itemized deductions" ---
    def _zbr_pre1987() -> pl.Expr:
        table = PRE1987_PARAMS["standard_deduction"]
        expr = pl.lit(None, dtype=pl.Float64)
        for status in ("single", "head_of_household", "married_joint", "married_separate"):
            v = float(resolve_year(table[status], effective_year))
            expr = pl.when(pl.col("filing_status") == status).then(pl.lit(v)).otherwise(expr)
        return expr

    deduc = pl.lit(0.0)
    if effective_year >= 1980:
        if effective_year >= 1987:
            std_p = FEDERAL_INCOME_TAX_PARAMS["standard_deduction"]
            married_val = float(resolve_year(std_p["married_joint"], effective_year))
            single_val = float(resolve_year(std_p["single"], effective_year))
            hoh_val = float(resolve_year(std_p["head_of_household"], effective_year))
            fedbas = (
                pl.when(pl.col("filing_status") == "single").then(single_val)
                .when(is_hoh).then(hoh_val)
                .otherwise(married_val / pl.col("la_sep"))
            )
            itemized_deduction_local = pl.col("itemized_deduction")
        else:
            fedbas = _zbr_pre1987()
            # `itemized_deduction` (comnew(24)) is a federal.py-ONLY
            # column, never exposed by federal_pre1987.py for years<=1986
            # - left at its silent $0 default, this made `la_deduc`
            # permanently $0 for 1980-1986 regardless of `force_itemize`
            # (caught via a live-probe mismatch on a 1980/single/
            # $120,000-wages case: both forced branches gave identical,
            # wrong results since neither could ever produce a real
            # itemized total). Reconstructed locally from raw proptax/
            # otheritem/mortgage PLUS `state_sales_or_income_tax_ded`
            # itself, same cancellation pattern DC/GA/HI/Idaho/Iowa/
            # Kansas/Kentucky's own pre-1987 reconstructions already
            # established.
            itemized_deduction_local = (
                pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
            )

        excess = (itemized_deduction_local - fedbas).clip(0, None)
        if effective_year <= 1986:
            itemizing = itemized_deduction_local > fedbas
        else:
            itemizing = pl.col("itemizes")
        if effective_year <= 1999 or effective_year >= 2009:
            deduc_real = excess
        elif effective_year <= 2001:
            deduc_real = float(p["excess_itemized_pct_2000_2001"][1960]) * excess
        elif effective_year == 2002:
            deduc_real = float(p["excess_itemized_pct_2002"][1960]) * excess
        elif effective_year == 2007:
            deduc_real = float(p["excess_itemized_pct_2007"][1960]) * excess
        elif effective_year == 2008:
            cap_pf = float(p["excess_itemized_2008_proptax_addback_cap_per_filer"][1960])
            pded = pl.when(pl.col("proptax") > 0).then(pl.min_horizontal(cap_pf * pl.col("la_txp"), pl.col("proptax"))).otherwise(0.0)
            deduc_real = float(p["excess_itemized_pct_2008"][1960]) * (pl.col("itemized_deduction") - (fedbas + pded)).clip(0, None)
        else:
            # 2003-2006: real, deliberate gap in the source - no branch,
            # deduc stays $0 even while itemizing (see module docstring).
            deduc_real = pl.lit(0.0)
        deduc = pl.when(itemizing).then(deduc_real).otherwise(0.0)

    df = df.with_columns(la_deduc=deduc)

    # --- Federal income tax deduction ---
    # `comnew(28)` (<=1979) is live-probe-confirmed to be federal's own
    # `regular_tax` (the pre-EITC/pre-credit figure), NOT `fiitax` -
    # caught via a live-probe mismatch (1977/single/$5,000 wages: real
    # `regular_tax`=$319.50 vs `fiitax`=$278.50, a real $41 EITC gap that
    # LA's own deduction must NOT reflect for THIS era).
    #
    # `comnew(52)` (law>=1980) is a genuinely DIFFERENT quantity across
    # the two federal vintages it spans, even though it's the same array
    # slot: for 1980-1986 (still federal_pre1987.py's own `law79`
    # vintage - CCC/ODC as this project models them don't exist there at
    # all, so `ccc`/`odc` default to $0 and can't explain the gap),
    # `comnew(52)` is live-probe-confirmed to equal `fiitax` directly
    # (1980/single/$50,000 wages: real `fiitax`=$17,142 exactly matches,
    # not `regular_tax`=$17,517). For 1987+ (federal.py's own `law87`
    # vintage), `comnew(52)` is `regular_tax` instead (see module
    # docstring point 1) - a real, era-specific distinction, not a single
    # uniform formula across the whole `law>=1980` range the source's own
    # `if` groups together.
    if effective_year <= 1979:
        la_fedtax = pl.col("regular_tax").clip(0, None)
    elif effective_year <= 1986:
        la_fedtax = pl.col("fiitax").clip(0, None)
    elif year == 2021:
        # ARPA made BOTH CCC and CTC/ODC fully refundable for 2021 only,
        # with no tax-liability cap at all (both already documented as
        # such on federal.py itself) - so NONE of `ccc`/`odc` actually
        # reduced `regular_tax` that year, even though the columns report
        # their full (refundable) amounts. Subtracting them here anyway
        # was a real bug, caught via a live-probe mismatch (2021/single/
        # $30,000 wages/$2,000 childcare: real federal-tax-deduction
        # equals `regular_tax` exactly, not `regular_tax-ccc-odc`).
        #
        # Gated on the RAW requested `year`, not `effective_year` - for
        # 2022/2023 (CPI-extrapolated), `effective_year` is forced to
        # 2021 for the STATE formula only, but federal.py itself still
        # computes `ccc`/`odc` at the REAL requested year's own (non-
        # ARPA, ordinary nonrefundable) rules, so the normal subtraction
        # is still correct there - caught via a live-probe mismatch on
        # 2022/2023 childcare cases after the `effective_year` version
        # of this fix wrongly applied the 2021-only carve-out to them too.
        la_fedtax = pl.col("regular_tax").clip(0, None) + pl.col("amt").clip(0, None)
    else:
        la_fedtax = (pl.col("regular_tax") - (pl.col("ccc") + pl.col("odc"))).clip(0, None) + pl.col("amt").clip(0, None)

    df = df.with_columns(la_taxinc=(pl.col("la_agi") - pl.col("la_deduc") - la_fedtax).clip(0, None))

    # --- Combined standard-deduction/exemption + taxable income ---
    stxmp1 = float(resolve_year(p["combined_stded_exemption_txp1"], effective_year))
    stxmp2 = float(resolve_year(p["combined_stded_exemption_txp2"], effective_year))
    stxmp = pl.when(pl.col("la_txp") == 2).then(stxmp2).otherwise(stxmp1)
    df = df.with_columns(
        la_taxinc=(pl.col("la_taxinc") - stxmp).clip(0, None),
        la_exemp=stxmp,
    )

    # --- Bracket tax ---
    xmpd = float(resolve_year(p["dependent_tax_reduction_amount"], effective_year))
    if effective_year <= 1979:
        statax = bracket_tax(pl.col("la_taxinc"), p["brackets_1977_1979"])
    elif effective_year <= 1982:
        statax = bracket_tax(pl.col("la_taxinc"), p["brackets_1980_1982"])
    else:
        if effective_year <= 2002:
            single_table, married_table, hoh_table = p["brackets_1983_2002_single"], p["brackets_1983_2002_married"], p["brackets_1983_2002_hoh"]
        elif effective_year <= 2008:
            single_table, married_table, hoh_table = p["brackets_2003_2008_single"], p["brackets_2003_2008_married"], p["brackets_2003_2008_hoh"]
        else:
            single_table, married_table, hoh_table = p["brackets_2009plus_single"], p["brackets_2009plus_married"], p["brackets_2009plus_hoh"]

        tax_single = (bracket_tax(pl.col("la_taxinc"), single_table) - 0.02 * xmpd * pl.col("depx")).clip(0, None)
        tax_married = (bracket_tax(pl.col("la_taxinc"), married_table) - 0.02 * xmpd * pl.col("depx")).clip(0, None)
        if effective_year <= 2002:
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (0.02 * pl.col("depx").clip(0, 1) + 0.04 * (pl.col("depx") - 1).clip(0, None))
            ).clip(0, None)
        else:
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (
                    0.02 * pl.col("depx").clip(0, 3)
                    + 0.03 * (pl.col("depx") - 4).clip(0, 1)
                    + 0.04 * (pl.col("depx") - 5).clip(0, None)
                )
            ).clip(0, None)
        statax = pl.when(is_single_or_sep).then(tax_single).when(is_joint).then(tax_married).otherwise(tax_hoh)

    df = df.with_columns(la_statax=statax)

    # --- Credits ---
    edcr = pl.lit(0.0)
    if (1979 <= effective_year <= 1985) or (1996 <= effective_year <= 1999):
        amt_ed = float(p["education_credit_per_dependent_1979_1985_1996_1999"][1960])
        edcr = pl.when(pl.col("depx") >= 1).then(amt_ed * pl.col("depx")).otherwise(0.0)
    elif effective_year in (2015, 2016):
        amt_ed = float(p["education_credit_per_dependent_2015_2016"][1960])
        edcr = pl.when(pl.col("depx") >= 1).then(amt_ed * pl.col("depx")).otherwise(0.0)

    if effective_year <= 1979:
        fedcr = pl.lit(0.0)
    else:
        # `data(34)`/`comnew(54)` - the former confirmed inert; the
        # latter an acknowledged gap (never identified within this
        # build's scope - a narrow, $25-capped 10%-of-credits component).
        # `comnew(53)` (federal's own CCC) isn't exposed as the `ccc`
        # column for years<=1986 (federal_pre1987.py doesn't compute it
        # that way) - federal_pre1987.py's OWN `credit` column already IS
        # the CCC-equivalent for that vintage ("the only nonrefundable
        # credit reachable in this scope", per its own docstring), so
        # it's reused directly rather than reconstructed - caught via a
        # live-probe mismatch (1980/single/$2,000 childcare/1 dependent
        # under 13: real credit implies the $400 CCC amount, matching
        # federal_pre1987.py's own `credit` column exactly, while `ccc`
        # defaults to $0 for this era).
        if effective_year <= 1986:
            ccc_for_fedcr = pl.col("credit")
        elif effective_year <= 1997:
            ccc_for_fedcr = _raw_ccc(df, effective_year)
        else:
            ccc_for_fedcr = pl.col("ccc")
        pct = float(p["federal_credit_pct"][1960])
        fedcr = pct * ccc_for_fedcr
        if effective_year >= 1986:
            cap = float(p["federal_credit_cap_1986plus"][1960])
            fedcr = fedcr.clip(0, cap)

    bcr = pl.lit(0.0)  # `data(10)` (blind) confirmed inert.

    chcr = pl.lit(0.0)
    chcref = pl.lit(0.0)
    if effective_year >= 2003:
        base = pl.col("la_agi").clip(0, None)
        rate1, cap1_over60 = None, float(p["child_care_credit_cap_over_60k"][1960])
        tiers = p["child_care_credit_rate_by_agi_tier"]
        ceiling1, rate1, refundable1 = tiers[0]
        ceiling2, rate2, refundable2 = tiers[1]
        ceiling3, rate3, refundable3 = tiers[2]
        chcref = pl.when(base <= ceiling1).then(rate1 * pl.col("ccc")).otherwise(0.0)
        chcr = (
            pl.when((base > ceiling1) & (base <= ceiling2)).then(rate2 * pl.col("ccc"))
            .when((base > ceiling2) & (base <= ceiling3)).then(rate3 * pl.col("ccc"))
            .when(base > ceiling3).then(pl.min_horizontal(0.1 * pl.col("ccc"), cap1_over60))
            .otherwise(0.0)
        )

    credit = fedcr + edcr + bcr + chcr
    df = df.with_columns(la_statax=(pl.col("la_statax") - credit).clip(0, None))

    earncr = pl.lit(0.0)
    if effective_year >= 2008:
        rate_eitc = float(p["eitc_rate_2008_2018"][1960]) if effective_year <= 2018 else float(p["eitc_rate_2019plus"][1960])
        earncr = rate_eitc * pl.col("eitc")
    df = df.with_columns(la_statax=pl.col("la_statax") - earncr - chcref)

    df = df.with_columns(siitax=pl.col("la_statax") * flate)
    return df
