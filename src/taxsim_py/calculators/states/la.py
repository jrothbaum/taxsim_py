"""Louisiana individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_single, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.payroll_tax import payroll_items
from taxsim_py.engine.state import allocate_pension, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

LA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "la" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")


def compute_la_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, LA_PARAMS)
    p = YearParams(LA_PARAMS, effective_year)

    df = df.with_columns(
        la_sep=separate_divisor(),
        la_txp=pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"])).then(2.0).otherwise(1.0),
    )
    is_single_or_sep = pl.col("filing_status").is_in(["single", "married_separate"])
    is_joint = files_joint()
    is_hoh = files_head_of_household()

    df = deflate_for_extrapolation(df, flate)

    if effective_year >= 2021 and not behavior.compute_louisiana_tax_per_statute:
        return _taxsim_from_2021(df, effective_year, flate, p, behavior)

    # --- AGI --- less exempt retirement income and Social Security, net of
    # the federal tax attributable to them.
    penexc = (
        pl.col("pensions").clip(0, p.num("aged_pension_exemption") * aged_count()) if p.in_effect("aged_pension_exemption") else pl.lit(0.0)
    )
    ssi = pl.col("taxable_social_security") if effective_year >= 1985 else pl.lit(0.0)
    subtr = penexc + ssi
    fedtax = pl.col("fiitax") + (pl.col("cares") if effective_year >= 2020 else 0.0)
    eit = p["exempt_income_tax"]
    ftadd1 = pl.when(subtr <= eit["break"]).then(
        eit["rate_low"] * (subtr - eit["floor"]).clip(0, None)
    ).otherwise(eit["base_high"] + eit["rate_high"] * (subtr - eit["break"]))
    ftadd2 = fedtax * pl.min_horizontal(pl.lit(1.0), subtr / pl.col("agi"))
    ftadd = pl.when((pl.col("agi") > 0) & (fedtax > 0) & (subtr > 0)).then(pl.min_horizontal(ftadd1, ftadd2)).otherwise(0.0)
    if not p.value("federal_tax_deduction"):
        ftadd = pl.lit(0.0)
    df = df.with_columns(la_agi=pl.col("agi") - (subtr - ftadd))

    # --- "Excess federal itemized deductions" ---
    deduc = pl.lit(0.0)
    if p.in_effect("excess_itemized_share"):
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
        share = p.num("excess_itemized_share")
        if p.in_effect("excess_itemized_property_tax_addition"):
            cap_pf = p.num("excess_itemized_property_tax_addition")
            pded = pl.when(pl.col("proptax") > 0).then(pl.min_horizontal(cap_pf * taxpayer_count(), pl.col("proptax"))).otherwise(0.0)
            deduc_real = share * (pl.col("itemized_deduction") - (fedbas + pded)).clip(0, None)
        else:
            deduc_real = share * excess
        deduc = pl.when(itemizing).then(deduc_real).otherwise(0.0)

    df = df.with_columns(la_deduc=deduc)

    # --- Federal income tax deduction ---
    # Through 1979 regular tax before credits; 1980-1986 federal income tax
    # before the EITC; from 1987 regular tax less nonrefundable credits, plus
    # AMT.
    if effective_year <= 1979:
        la_fedtax = pl.col("regular_tax").clip(0, None)
    elif effective_year <= 1986:
        la_fedtax = (pl.col("fiitax") + pl.col("pre1987_earncr")).clip(0, None)
    else:
        la_fedtax = (pl.col("regular_tax") - pl.col("nonrefundable_credits")).clip(0, None) + pl.col("amt").clip(0, None)
        if not behavior.exclude_niit_from_louisiana_federal_tax_deduction:
            la_fedtax = la_fedtax + pl.col("niit")
    if not p.value("federal_tax_deduction"):
        la_fedtax = pl.lit(0.0)

    df = df.with_columns(la_taxinc=(pl.col("la_agi") - pl.col("la_deduc") - la_fedtax).clip(0, None))

    # --- Combined standard deduction and exemption ---
    exemption_credit = pl.lit(0.0)
    if p.in_effect("standard_deduction"):
        sd = p.value("standard_deduction")
        stxmp = (
            pl.when(files_single() | (pl.col("filing_status") == "married_separate")).then(float(sd["single"]))
            .when(is_hoh).then(float(sd["head_of_household"])).otherwise(float(sd["married_joint"]))
        )
        df = df.with_columns(la_taxinc=(pl.col("la_taxinc") - stxmp).clip(0, None), la_exemp=stxmp)
    else:
        combined = p.value("combined_deduction_exemption")
        stxmp = pl.when(pl.col("la_txp") == 2).then(float(combined["two"])).otherwise(float(combined["one"]))
        if p.in_effect("additional_exemption"):
            stxmp = stxmp + p.num("additional_exemption") * (pl.col("depx") + aged_count())
            # Exemptions reduce tax at the lowest rate instead of income.
            exemption_credit = float(p.value("brackets")["single"][0][1]) * stxmp
            df = df.with_columns(la_taxinc=pl.col("la_taxinc"), la_exemp=stxmp)
        else:
            df = df.with_columns(
                la_taxinc=(pl.col("la_taxinc") - stxmp).clip(0, None),
                la_exemp=stxmp,
            )

    # --- Tax ---
    xmpd = p.num("dependent_tax_reduction_amount") if p.in_effect("dependent_tax_reduction_amount") else 0.0
    # Dependents and taxpayers 65 or older each count toward the reduction.
    dependents = pl.col("depx") + aged_count() if p.in_effect("dependent_tax_reduction_amount") else pl.lit(0.0)
    brackets = p.value("brackets") if p.in_effect("brackets") else None
    if p.in_effect("taxsim_brackets") and not behavior.use_louisiana_brackets_per_statute:
        brackets = p.value("taxsim_brackets")
    if brackets is None:
        rate = p.num("flat_rate")
        statax = rate * pl.col("la_taxinc")
        rate_expr = pl.lit(rate)
    elif isinstance(brackets, list):
        statax = bracket_tax(pl.col("la_taxinc"), brackets)
        rate_expr = bracket_rate(pl.col("la_taxinc"), brackets)
    else:
        single_table, married_table, hoh_table = brackets["single"], brackets["married_joint"], brackets["head_of_household"]
        tax_single = (bracket_tax(pl.col("la_taxinc"), single_table) - p["dependent_credit_rate"] * xmpd * dependents).clip(0, None)
        married_reductions = 1.0 if behavior.reduce_louisiana_joint_tax_once_for_dependents else (2.0 if effective_year <= 2021 else 1.0)
        tax_married = bracket_tax(pl.col("la_taxinc"), married_table)
        for _ in range(int(married_reductions)):
            tax_married = (tax_married - p["dependent_credit_rate"] * xmpd * dependents).clip(0, None)
        if not p.in_effect("dependent_tax_reduction_amount"):
            tax_hoh = bracket_tax(pl.col("la_taxinc"), hoh_table)
        elif "first" in p.value("dependent_credit_hoh"):
            hoh_rates = p.value("dependent_credit_hoh")
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (
                    hoh_rates["first"] * dependents.clip(0, 1)
                    + hoh_rates["additional"] * (dependents - 1).clip(0, None)
                )
            ).clip(0, None)
        else:
            hoh_rates = p.value("dependent_credit_hoh")
            tax_hoh = (
                bracket_tax(pl.col("la_taxinc"), hoh_table)
                - xmpd * (
                    hoh_rates["first_three"] * dependents.clip(0, 3)
                    + hoh_rates["fourth"] * (dependents - 4).clip(0, 1)
                    + hoh_rates["additional"] * (dependents - 5).clip(0, None)
                )
            ).clip(0, None)
        statax = pl.when(is_single_or_sep).then(tax_single).when(is_joint).then(tax_married).otherwise(tax_hoh)
        rate_expr = (
            pl.when(is_single_or_sep).then(bracket_rate(pl.col("la_taxinc"), single_table))
            .when(is_joint).then(bracket_rate(pl.col("la_taxinc"), married_table))
            .otherwise(bracket_rate(pl.col("la_taxinc"), hoh_table))
        )
        if p.in_effect("additional_exemption"):
            statax = (statax - exemption_credit).clip(0, None)
    df = df.with_columns(la_statax=statax)

    # --- Credits ---
    edcr = pl.lit(0.0)
    if p.in_effect("education_credit_per_dependent"):
        amt_ed = p.num("education_credit_per_dependent")
        edcr = pl.when(pl.col("depx") >= 1).then(amt_ed * pl.col("depx")).otherwise(0.0)

    if not behavior.allow_louisiana_federal_credit_share_per_statute:
        # TAXSIM overwrites the credit: a tenth of the federal nonrefundable
        # credits and earned income credit in 1980-1984, otherwise nothing.
        fedcr = 0.1 * (pl.col("credit") + pl.col("pre1987_earncr")) if 1980 <= effective_year <= 1984 else pl.lit(0.0)
    elif p.in_effect("federal_credit_share"):
        # From 2022 Louisiana has no credit for the federal elderly credit.
        elder = pl.lit(0.0) if effective_year >= 2022 else pl.col("federal_elder")
        fedcr = p.num("federal_credit_share") * (pl.col("federal_chcr") + elder)
        if p.in_effect("federal_credit_cap"):
            fedcr = fedcr.clip(0, p.num("federal_credit_cap"))
    else:
        fedcr = pl.lit(0.0)

    chcr = pl.lit(0.0)
    chcref = pl.lit(0.0)
    if p.in_effect("child_care_credit"):
        cc = p.value("child_care_credit")
        taxsim_credit = not behavior.use_louisiana_child_care_credit_per_statute
        base = pl.col("agi").clip(0, None) if taxsim_credit else pl.col("la_agi").clip(0, None)
        child_care = pl.col("federal_chcr") if taxsim_credit else pl.col("ccc")
        cap1_over60 = float(cc["top_cap"])
        tiers = cc["tiers"]
        ceiling1, rate1, refundable1 = tiers[0]
        ceiling2, rate2, refundable2 = tiers[1]
        ceiling3, rate3, refundable3 = tiers[2]
        chcref = pl.when(base <= ceiling1).then(rate1 * child_care).otherwise(0.0)
        chcr = (
            pl.when((base > ceiling1) & (base <= ceiling2)).then(rate2 * child_care)
            .when((base > ceiling2) & (base <= ceiling3)).then(rate3 * child_care)
            .when(base > ceiling3).then(pl.min_horizontal(cc["top_rate"] * child_care, cap1_over60))
            .otherwise(0.0)
        )

    credit = fedcr + edcr + chcr
    df = df.with_columns(la_statax=(pl.col("la_statax") - credit).clip(0, None))

    earncr = pl.lit(0.0)
    if p.in_effect("eitc_federal_share"):
        earncr = p.num("eitc_federal_share") * pl.col("eitc")
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



def _taxsim_from_2021(df: pl.DataFrame, year: int, flate: float, p: YearParams, behavior: BehaviorProfile) -> pl.DataFrame:
    """The 2026 TAXSIM build's Louisiana tax from 2021 (`latax21`)."""
    txp = taxpayer_count()
    primary_aged = (pl.col("page") >= 65)
    spouse_aged = (pl.col("sage") >= 65)
    fed_agi = pl.col("agi")

    # Retirement income exemption, per taxpayer 65 or older.
    limit = p.num("aged_pension_exemption")
    pension = pl.col("pensions")
    pent_all, pens_all = allocate_pension(pension, 55.0)
    aged = aged_count()
    single_aged = (txp == 1) & (aged == 1)
    pent = pl.when(single_aged).then(pension.clip(0, limit)).when((aged > 0) & primary_aged).then(pent_all.clip(0, limit)).otherwise(0.0)
    pens = pl.when((aged > 0) & ~single_aged & spouse_aged).then(pens_all.clip(0, limit)).otherwise(0.0)
    subtr = pent + pens + pl.col("taxable_social_security")

    # The federal income tax deduction exists in 2021 only.
    if year == 2021:
        fedtax = pl.col("regular_tax") + pl.col("amt") + pl.col("niit") - pl.col("federal_chcr")
        ftadd = pl.when((fed_agi > 0) & (fedtax > 0) & (subtr > 0)).then(fedtax * pl.min_horizontal(pl.lit(1.0), subtr / fed_agi)).otherwise(0.0)
    else:
        fedtax = pl.lit(0.0)
        ftadd = pl.lit(0.0)
    agi = fed_agi - subtr + ftadd

    is_hoh = files_head_of_household()
    if year <= 2024:
        taxinc = (agi - fedtax).clip(0, None)
        rates = (0.02, 0.04, 0.06) if year == 2021 else (0.0185, 0.035, 0.0425)
        aa = taxinc
        ab = pl.min_horizontal(12_500.0 * txp, aa)
        exemption = pl.when(is_hoh).then(9000.0).otherwise(4500.0 * txp) + (pl.col("depx") + aged) * 1000.0
        aft = (ab - exemption) * rates[0]
        agt = pl.when(aa - ab > 0).then(pl.min_horizontal(aa - ab, 37_500.0 * txp) * rates[1]).otherwise(0.0)
        aht = pl.when(aa - 50_000.0 * txp > 0).then((aa - 50_000.0 * txp) * rates[2]).otherwise(0.0)
        taxbc = aft + agt + aht
        exemp = exemption
    else:
        stded = pl.when(files_joint() | is_hoh).then(25_000.0).otherwise(12_500.0)
        taxinc = agi - stded
        taxbc = 0.03 * taxinc
        exemp = stded

    # Credits.
    agip = (fed_agi - 0.02).clip(0, None)
    child_care = pl.col("federal_chcr")
    chcrnoref = (
        pl.when(agip <= 25_000).then(0.0)
        .when(agip <= 35_000).then(0.3 * child_care)
        .when(agip <= 60_000).then(0.1 * child_care)
        .otherwise(pl.min_horizontal(0.1 * child_care, pl.lit(25.0)))
    )
    # The blindness credit needs a blind-taxpayer input TAXSIM does not have.
    blindcr = pl.lit(0.0)
    earncr = 0.05 * pl.col("eitc")

    wages, businesses = [], []
    for names in payroll_items(behavior):
        wage = pl.col(names[0]).clip(0, None)
        self_employed = pl.sum_horizontal(pl.col(name).clip(0, None) for name in names[1:])
        wages.append(wage)
        businesses.append(wage + 0.9235 * self_employed)
    earnmin = pl.when(pl.col("mstat") == 2).then(pl.min_horizontal(businesses[0], businesses[1])).otherwise(businesses[0])
    if year == 2021:
        expense = pl.min_horizontal(pl.col("childcare"), 8000.0 * pl.col("dep13").clip(None, 2))
        chcref = pl.when(fed_agi <= 25_000).then(0.25 * pl.min_horizontal(expense, earnmin)).otherwise(0.0)
    else:
        frac = 0.35 - 0.05 * (agip - 15_000).clip(0, None) / 10_000
        chcref = pl.when((agip <= 25_000) & (pl.col("dep13") > 0)).then(
            pl.min_horizontal(earnmin, 0.5 * frac * pl.col("ccc_expense"))
        ).otherwise(0.0)
    nonrefundable = pl.min_horizontal(taxbc, blindcr + chcrnoref)
    credit = nonrefundable + earncr + chcref
    statax = taxbc - credit
    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        taxable_income=taxinc,
        child_care_credit=chcref + chcrnoref,
        eic=earncr,
        credits=credit,
        rate=pl.lit(0.0),
    )
