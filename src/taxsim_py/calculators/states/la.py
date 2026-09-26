"""Louisiana individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, with_default as _with_default, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

LA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "la" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")


def compute_la_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = LA_PARAMS
    df = with_defaults(df, ("federal_chcr", "proptax", "depx", "childcare"))
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "odc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")
    df = _with_default(df, "regular_tax")
    df = _with_default(df, "amt")
    df = with_defaults(df, ("pensions", "taxable_social_security", "cares"))

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
            "federal_chcr",
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "eitc", "ccc", "odc",
            "itemized_deduction", "fiitax", "regular_tax", "amt", "pensions",
            "taxable_social_security", "cares",
        ],
    )

    # --- AGI --- less exempt retirement income and Social Security, net of
    # the federal tax attributable to them.
    penexc = (
        pl.col("pensions").clip(0, p["aged_pension_exemption"] * aged_count()) if effective_year >= 1981 else pl.lit(0.0)
    )
    ssi = pl.col("taxable_social_security") if effective_year >= 1985 else pl.lit(0.0)
    subtr = penexc + ssi
    fedtax = pl.col("fiitax") + (pl.col("cares") if effective_year >= 2020 else 0.0)
    ftadd1 = pl.when(subtr <= p["exempt_income_tax_break"]).then(
        p["exempt_income_tax_rate_low"] * (subtr - p["exempt_income_tax_floor"]).clip(0, None)
    ).otherwise(p["exempt_income_tax_base_high"] + p["exempt_income_tax_rate_high"] * (subtr - p["exempt_income_tax_break"]))
    ftadd2 = fedtax * pl.min_horizontal(pl.lit(1.0), subtr / pl.col("agi"))
    ftadd = pl.when((pl.col("agi") > 0) & (fedtax > 0) & (subtr > 0)).then(pl.min_horizontal(ftadd1, ftadd2)).otherwise(0.0)
    df = df.with_columns(la_agi=pl.col("agi") - (subtr - ftadd))

    # --- "Excess federal itemized deductions" ---
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
            fedbas = pl.col("pre1987_zbr")
            itemized_deduction_local = pl.col("pre1987_deduc")

        excess = (itemized_deduction_local - fedbas).clip(0, None)
        if effective_year <= 1986:
            itemizing = pl.col("pre1987_itemizes")
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
            pded = pl.when(pl.col("proptax") > 0).then(pl.min_horizontal(cap_pf * taxpayer_count(), pl.col("proptax"))).otherwise(0.0)
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
        # `comnew(52)` is after nonrefundable credits but before the EITC;
        # `fiitax` has already subtracted that refundable credit.
        la_fedtax = (pl.col("fiitax") + pl.col("pre1987_earncr")).clip(0, None)
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
    # Dependents and taxpayers 65 or older each count toward the reduction.
    dependents = pl.col("depx") + aged_count()
    if effective_year <= 1979:
        table = p["brackets_1977_1979"]
        statax = bracket_tax(pl.col("la_taxinc"), table)
        rate_expr = bracket_rate(pl.col("la_taxinc"), table)
    elif effective_year <= 1982:
        table = p["brackets_1980_1982"]
        statax = bracket_tax(pl.col("la_taxinc"), table)
        rate_expr = bracket_rate(pl.col("la_taxinc"), table)
    else:
        if effective_year <= 2002:
            single_table, married_table, hoh_table = p["brackets_1983_2002_single"], p["brackets_1983_2002_married"], p["brackets_1983_2002_hoh"]
        elif effective_year <= 2008:
            single_table, married_table, hoh_table = p["brackets_2003_2008_single"], p["brackets_2003_2008_married"], p["brackets_2003_2008_hoh"]
        else:
            single_table, married_table, hoh_table = p["brackets_2009plus_single"], p["brackets_2009plus_married"], p["brackets_2009plus_hoh"]

        tax_single = (bracket_tax(pl.col("la_taxinc"), single_table) - 0.02 * xmpd * dependents).clip(0, None)
        tax_married = (bracket_tax(pl.col("la_taxinc"), married_table) - 0.02 * xmpd * dependents).clip(0, None)
        if effective_year <= 2002:
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (0.02 * dependents.clip(0, 1) + 0.04 * (dependents - 1).clip(0, None))
            ).clip(0, None)
        else:
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (
                    0.02 * dependents.clip(0, 3)
                    + 0.03 * (dependents - 4).clip(0, 1)
                    + 0.04 * (dependents - 5).clip(0, None)
                )
            ).clip(0, None)
        statax = pl.when(is_single_or_sep).then(tax_single).when(is_joint).then(tax_married).otherwise(tax_hoh)
        rate_expr = (
            pl.when(is_single_or_sep).then(bracket_rate(pl.col("la_taxinc"), single_table))
            .when(is_joint).then(bracket_rate(pl.col("la_taxinc"), married_table))
            .otherwise(bracket_rate(pl.col("la_taxinc"), hoh_table))
        )

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
        # A share of the federal child care credit (`comnew(53)`).
        ccc_for_fedcr = pl.col("federal_chcr")
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
    return with_state_detail(
        df,
        agi=pl.col("la_agi"),
        exemptions=pl.col("la_exemp"),
        taxable_income=pl.col("la_taxinc"),
        child_care_credit=chcr + chcref,
        eic=earncr,
        credits=credit + earncr + chcref,
        rate=rate_expr,
    )
