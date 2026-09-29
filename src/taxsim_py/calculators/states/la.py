"""Louisiana individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_single, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

LA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "la" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")


def compute_la_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(LA_PARAMS, effective_year)

    df = df.with_columns(
        la_sep=separate_divisor(),
        la_txp=pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"])).then(2.0).otherwise(1.0),
    )
    is_single_or_sep = pl.col("filing_status").is_in(["single", "married_separate"])
    is_joint = files_joint()
    is_hoh = files_head_of_household()

    df = deflate_for_extrapolation(df, flate)

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
            std_p = YearParams(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"], effective_year)
            married_val = std_p.num("married_joint")
            single_val = std_p.num("single")
            hoh_val = std_p.num("head_of_household")
            fedbas = (
                pl.when(files_single()).then(single_val)
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
            # 2003-2006: TAXSIM has no excess itemized deduction.
            deduc_real = pl.lit(0.0)
        deduc = pl.when(itemizing).then(deduc_real).otherwise(0.0)

    df = df.with_columns(la_deduc=deduc)

    # --- Federal income tax deduction ---
    # Through 1979 `comnew(28)`, regular tax before credits. From 1980
    # `comnew(52)`: federal income tax through 1986, regular tax from 1987.
    if effective_year <= 1979:
        la_fedtax = pl.col("regular_tax").clip(0, None)
    elif effective_year <= 1986:
        # `comnew(52)` is after nonrefundable credits but before the EITC;
        # `fiitax` has already subtracted that refundable credit.
        la_fedtax = (pl.col("fiitax") + pl.col("pre1987_earncr")).clip(0, None)
    else:
        # `max(0,comnew(52)-comnew(58))+comnew(70)`: regular tax less nonrefundable credits, plus AMT.
        la_fedtax = (pl.col("regular_tax") - pl.col("nonrefundable_credits")).clip(0, None) + pl.col("amt").clip(0, None)

    df = df.with_columns(la_taxinc=(pl.col("la_agi") - pl.col("la_deduc") - la_fedtax).clip(0, None))

    # --- Combined standard-deduction/exemption + taxable income ---
    stxmp1 = p.num("combined_stded_exemption_txp1")
    stxmp2 = p.num("combined_stded_exemption_txp2")
    stxmp = pl.when(pl.col("la_txp") == 2).then(stxmp2).otherwise(stxmp1)
    df = df.with_columns(
        la_taxinc=(pl.col("la_taxinc") - stxmp).clip(0, None),
        la_exemp=stxmp,
    )

    # --- Bracket tax ---
    xmpd = p.num("dependent_tax_reduction_amount")
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

        tax_single = (bracket_tax(pl.col("la_taxinc"), single_table) - p["dependent_credit_rate"] * xmpd * dependents).clip(0, None)
        tax_married = (bracket_tax(pl.col("la_taxinc"), married_table) - p["dependent_credit_rate"] * xmpd * dependents).clip(0, None)
        if effective_year <= 2002:
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (
                    p["dependent_credit_hoh_pre2003"]["first"] * dependents.clip(0, 1)
                    + p["dependent_credit_hoh_pre2003"]["additional"] * (dependents - 1).clip(0, None)
                )
            ).clip(0, None)
        else:
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (
                    p["dependent_credit_hoh_2003plus"]["first_three"] * dependents.clip(0, 3)
                    + p["dependent_credit_hoh_2003plus"]["fourth"] * (dependents - 4).clip(0, 1)
                    + p["dependent_credit_hoh_2003plus"]["additional"] * (dependents - 5).clip(0, None)
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
        # A share of the federal child care and elderly credits (`comnew(53)`, `comnew(54)`).
        ccc_for_fedcr = pl.col("federal_chcr") + pl.col("federal_elder")
        pct = float(p["federal_credit_pct"][1960])
        fedcr = pct * ccc_for_fedcr
        if effective_year >= 1986:
            cap = float(p["federal_credit_cap_1986plus"][1960])
            fedcr = fedcr.clip(0, cap)

    bcr = pl.lit(0.0)  # The blind credit (`data(10)`) has no TAXSIM input.

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
            .when(base > ceiling3).then(pl.min_horizontal(p["child_care_credit_top_tier_rate"] * pl.col("ccc"), cap1_over60))
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
