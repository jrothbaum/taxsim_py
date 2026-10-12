"""Kansas individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.credits import child_care_credit_rate
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import (
    higher_earner_share,
    by_filing_status,
    household_income,
    interpolate_table,
    taxsim_socsec,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

KS_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ks" / "income_tax.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")


def compute_ks_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, KS_PARAMS)
    p = YearParams(KS_PARAMS, effective_year)

    df = df.with_columns(
        ks_sep=separate_divisor(),
        ks_taxpayers=taxpayer_count(),
    )
    is_joint = files_joint()
    is_hoh = files_head_of_household()

    # Self-employment tax is not deflated in projected years.
    df = df.with_columns(
        ks_setax=pl.col("payroll_setax"),
        ks_household_income=household_income(behavior),
    )
    df = deflate_for_extrapolation(df, flate, extra=("ks_household_income",))

    # --- AGI ---
    df = df.with_columns(ks_agi=pl.col("agi"))
    # Social Security benefits are exempt below a federal AGI limit (2007+).
    if effective_year >= 2007:
        limit = p.num("social_security_agi_limit")
        df = df.with_columns(
            ks_agi=pl.when(pl.col("agi") <= limit).then(pl.col("ks_agi") - pl.col("taxable_social_security")).otherwise(pl.col("ks_agi"))
        )
    if 2013 <= effective_year <= 2016:
        # Self-employment, S corporation and other property income removed.
        se_income = pl.col("psemp") + pl.col("ssemp")
        schedule_e = pl.col("otherprop") + pl.col("scorp")
        df = df.with_columns(ks_agi=pl.col("ks_agi") + 0.5 * pl.col("ks_setax") - se_income - schedule_e)

    fedtax = pl.col("fiitax").clip(0, None)
    if effective_year <= 1982 or (1987 <= effective_year <= 1988):
        fedded = fedtax
    elif p.in_effect("federal_tax_deduction_caps"):
        caps = p.value("federal_tax_deduction_caps")
        cap1 = float(caps["cap"]) * pl.col("ks_taxpayers")
        cap2 = float(caps["upper_cap"]) * pl.col("ks_taxpayers")
        fedded = pl.when(fedtax <= cap1).then(fedtax).when(fedtax <= cap2).then(cap1).otherwise(0.5 * fedtax)
    elif 1985 <= effective_year <= 1986:
        fedded = fedtax * pl.col("ks_agi").clip(0, None) / pl.col("agi").clip(1.0, None)
    else:
        fedded = pl.lit(0.0)

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        is_single_or_hoh = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded = pl.when(is_single_or_hoh).then(
            (sd["rate"] * pl.col("ks_agi")).clip(float(sd["single"]["floor"]), float(sd["single"]["cap"]))
        ).otherwise((sd["rate"] * pl.col("ks_agi")).clip(float(sd["married"]["floor"]) / pl.col("ks_sep"), float(sd["married"]["cap"]) / pl.col("ks_sep")))
    else:
        stded = by_filing_status(p.value("standard_deduction"))
        if p.in_effect("dependent_standard_deduction_minimum"):
            limit = pl.max_horizontal(pl.lit(p.num("dependent_standard_deduction_minimum")), pl.col("earned_income"))
            stded = pl.when(is_dependent_filer()).then(pl.min_horizontal(stded, limit)).otherwise(stded)
        single_amount, married_amount = p.value("aged_standard_deduction")
        married_type = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        stded = stded + pl.when(married_type).then(float(married_amount)).otherwise(float(single_amount)) * aged_count()
    df = df.with_columns(ks_stded=stded)

    # --- Itemized deduction ---
    if effective_year <= 1986:
        # Federal itemized deductions (zero when not itemizing federally).
        itemized_deduction_local = pl.col("pre1987_deduc")
        itemizing = pl.col("pre1987_itemizes")
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemized_deduction_local = pl.col("itemized_deduction")
        # KS-001: TAXSIM stops letting Kansas filers itemize after 2020.
        itemizing = pl.col("itemizes") if (behavior.allow_kansas_itemizing_after_2020 or effective_year <= 2020) else pl.lit(False)

    xitded = pl.lit(0.0)
    if effective_year <= 1987:
        # Payroll and self-employment tax paid, capped, are deductible.
        socsec = taxsim_socsec(effective_year, pl.col("ks_setax"), behavior=behavior)
        caps = p.value("payroll_tax_deduction_cap") if p.in_effect("payroll_tax_deduction_cap") else {"payroll": 1.0e20, "self_employment": 1.0e20}
        socmax, selfmx = float(caps["payroll"]), float(caps["self_employment"])
        soc = socsec.clip(0, socmax * pl.col("ks_taxpayers"))
        addtx = pl.col("ks_setax").clip(0, selfmx * pl.col("ks_taxpayers"))
        base = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") + soc + addtx).clip(0, None)
        xitded = pl.when(itemizing).then(base).otherwise(0.0)
    elif effective_year <= 1990:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif p.in_effect("itemized_limitation"):
        lim = p.value("itemized_limitation")
        threshold = float(lim["threshold"]) * p.num("itemized_limitation_index")
        under_thr = pl.col("agi") <= threshold / pl.col("ks_sep")
        base_low = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        fline3 = itemized_deduction_local.clip(0, None)
        fline9 = pl.min_horizontal(
            float(lim["rate"]) * (pl.col("agi") - threshold).clip(0, None),
            float(lim["cap_rate"]) * fline3,
        )
        sline1 = pl.when(fline3 > 0).then(fline9 / fline3).otherwise(0.0)
        base_high = pl.when(fline3 > 0).then(
            (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") * (1 - sline1)).clip(0, None)
        ).otherwise(base_low)
        xitded = pl.when(itemizing).then(pl.when(under_thr).then(base_low).otherwise(base_high)).otherwise(0.0)
    elif effective_year <= 2012:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2017:
        aifit_val = p.num("itemized_scaling")
        xitded = pl.when(itemizing).then(
            aifit_val * (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        ).otherwise(0.0)
    else:
        aifit_val = p.num("itemized_scaling")
        txpaid = pl.col("proptax") + pl.col("otheritem")
        xitded = pl.when(itemizing).then(aifit_val * (txpaid + pl.col("mortgage"))).otherwise(0.0)

    if effective_year == 2021:
        # 2021: itemized inputs, whether or not the return itemizes.
        xitded = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")

    df = df.with_columns(ks_xitded=xitded)
    df = df.with_columns(ks_deduc=pl.max_horizontal(pl.col("ks_stded"), pl.col("ks_xitded")))

    # --- Exemptions ---
    # The federal exemption count, deflated in projected years before Kansas
    # adds one for head of household.
    if effective_year <= 1986:
        comnew68 = (pl.col("ks_taxpayers") + pl.col("depx") + aged_count()) / flate
    else:
        comnew68 = pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ks_taxpayers") + pl.col("depx")) / flate
    exemps = pl.when(is_hoh).then(comnew68 + 1.0).otherwise(comnew68)
    xmp = p.num("personal_exemption")
    exemption = exemps * xmp
    if p.in_effect("exemptions_by_status"):
        e = p.value("exemptions_by_status")
        exemption = pl.when(is_dependent_filer()).then(0.0).otherwise(
            by_filing_status(e["by_status"]) + float(e["dependent"]) * pl.col("depx") + pl.when(is_hoh).then(float(e["head_of_household_addition"])).otherwise(0.0)
        )
    df = df.with_columns(ks_exemp=exemption)

    df = df.with_columns(ks_taxinc=(pl.col("ks_agi") - pl.col("ks_deduc") - pl.col("ks_exemp") - fedded).clip(0, None))

    # --- Tax ---
    brackets = p.value("brackets")
    if isinstance(brackets, dict):
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), brackets["joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), brackets["other"])
        )
        rate_expr = pl.when(is_joint).then(bracket_rate(pl.col("ks_taxinc"), brackets["joint"])).otherwise(
            bracket_rate(pl.col("ks_taxinc"), brackets["other"])
        )
    else:
        # Joint returns are taxed on half their income, doubled, or on each
        # earner's share if lower.
        table = brackets
        halved = pl.when(is_joint).then(pl.col("ks_taxinc") / 2.0).otherwise(pl.col("ks_taxinc"))
        doubler = pl.when(is_joint).then(2.0).otherwise(1.0)
        statax = bracket_tax(halved, table) * doubler
        rate_expr = bracket_rate(pl.col("ks_taxinc"), table)
        yh = higher_earner_share(pl.col("ks_taxinc"))
        yw = pl.col("ks_taxinc") - yh
        tax_h = bracket_tax(yh.clip(0, None), table)
        tax_w = bracket_tax(yw.clip(0, None), table)
        statax = pl.when(is_joint).then(pl.min_horizontal(statax, tax_h + tax_w)).otherwise(statax)
        # TAXSIM's reported rate is left by the higher earner's half.
        rate_expr = pl.when(is_joint).then(bracket_rate(yh.clip(0, None), table)).otherwise(rate_expr)

    df = df.with_columns(ks_statax=statax)

    if p.in_effect("zero_tax_agi"):
        zero = p.value("zero_tax_agi")
        zero_agi = pl.when(is_joint).then(float(zero["joint"])).otherwise(float(zero["other"]))
        df = df.with_columns(
            ks_statax=pl.when((pl.col("ks_taxinc") <= 0) | (pl.col("ks_agi") <= zero_agi)).then(0.0).otherwise(pl.col("ks_statax"))
        )
    elif p.in_effect("zero_tax_taxable_income"):
        zero = p.value("zero_tax_taxable_income")
        under = pl.when(is_joint).then(pl.col("ks_taxinc") <= float(zero["joint"])).otherwise(pl.col("ks_taxinc") <= float(zero["single"]))
        df = df.with_columns(ks_statax=pl.when(under).then(0.0).otherwise(pl.col("ks_statax")))

    # --- Credits ---
    # Child care credit: a share of the federal credit, which TAXSIM leaves at
    # 0 for 1988-1997, so those years recompute it.
    if 1988 <= effective_year <= 1997:
        ccc_p = YearParams(FEDERAL_CREDITS_PARAMS["child_care_credit"], effective_year)
        max_qualifying_persons = ccc_p.num("max_qualifying_persons")
        max_expense_per_person = ccc_p.num("max_expense_per_person")
        ccc_rate = child_care_credit_rate(pl.col("agi"), ccc_p.value("rate"))
        num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons)
        qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person)
        ccc_earned_income_cap = pl.when(is_joint).then(
            pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
        ).otherwise(pl.col("wages"))
        ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
        chcare = ccc_rate * ccc_expense
    else:
        chcare = pl.col("federal_chcr")
    # Up to regular tax.
    chcare = pl.min_horizontal(chcare, pl.col("regular_tax").clip(0, None))
    share = p.value("child_care_credit_federal_share")
    if isinstance(share, list):
        chcr = chcare * interpolate_table(pl.col("ks_agi"), share)
    elif share:
        chcr = chcare * float(share)
    else:
        chcr = pl.lit(0.0)
    df = df.with_columns(ks_chcr=chcr)

    encred = pl.lit(0.0)

    # Homestead property tax refund.
    rent_share = p.num("homestead_rent_share")
    pr1 = pl.col("proptax") + rent_share * pl.col("rentpaid")
    hy = pl.col("ks_household_income")
    pcred = pl.lit(0.0)
    if p.in_effect("homestead_refund"):
        hr = p.value("homestead_refund")
        claw = bracket_tax(hy, p.value("homestead_reduction_schedule"))
        full_refund = hy <= p.num("homestead_full_refund_income")
        # TAXSIM's 1996 test reads `1996 or (1995 and income <= ceiling)`.
        ceiling = 1.0e20 if effective_year == 1996 else float(hr["income_ceiling"])
        under = hy <= ceiling
        cap = float(hr["cap"])
        if effective_year <= 1978:
            reduced = (pr1 - claw).clip(0, cap)
        else:
            reduced = (pr1.clip(0, cap) - claw).clip(0, None)
        pcred = pl.when(under).then(pl.when(full_refund).then(pr1).otherwise(reduced)).otherwise(0.0)
    else:
        ptax = pl.min_horizontal(p.num("homestead_property_tax_cap"), pr1)
        if effective_year <= 2005:
            hhy = hy
        else:
            hhy = pl.col("ks_agi") + pl.col("eitc") + 0.5 * pl.col("gssi")
        pmax = p.num("homestead_refund_income_limit")
        pcred = pl.when(hhy < pmax).then(ptax * interpolate_table(hhy, p.value("homestead_refund_share"))).otherwise(0.0)
        if p.in_effect("senior_property_relief_share"):
            share = p.num("senior_property_relief_share")
            limit = p.num("senior_property_relief_income_limit")
            senior = (aged_count() > 0) & (pl.col("proptax") > 0) & (hy < limit)
            pcred = pl.when(senior).then(share * pl.col("proptax")).otherwise(pcred)
    df = df.with_columns(ks_pcred=pcred)

    # Food Sales Tax Refund.
    fd = pl.lit(0.0)
    if p.in_effect("food_refund_aged"):
        fa = p.value("food_refund_aged")
        per_aged = float(fa["amount"])
        fd = pl.when(hy <= fa["income_limit"]).then(
            per_aged * pl.min_horizontal(aged_count(), pl.col("ks_taxpayers") + pl.col("depx"))
        ).otherwise(0.0)
    elif p.in_effect("food_refund_tiers"):
        extra = pl.col("ks_taxpayers") + pl.col("depx") - 1.0
        tiers = p.value("food_refund_tiers")
        fd = pl.lit(0.0)
        for index, (limit, base, per_extra) in reversed(list(enumerate(tiers))):
            within = hy <= limit if index == len(tiers) - 1 else hy < limit
            fd = pl.when(within).then(base + per_extra * extra).otherwise(fd)
    elif effective_year >= 1998:
        eligible = (pl.col("depx") + aged_count()) > 0
        food_amt = p.num("food_sales_tax_credit_amount")
        if effective_year <= 2012:
            agimax = p.num("food_sales_tax_refund_agi_ceiling")
            fd = pl.when(eligible & (pl.col("ks_agi") <= agimax)).then(2.0 * food_amt * exemps).when(
                eligible & (pl.col("ks_agi") <= 2.0 * agimax)
            ).then(food_amt * exemps).otherwise(0.0)
        else:
            agimax = p.num("food_sales_tax_refund_agi_ceiling")
            fd = pl.when(eligible & (pl.col("agi") <= agimax)).then(food_amt * comnew68).otherwise(0.0)
    df = df.with_columns(ks_fd=fd)

    # Earned Income Credit.
    earncr = pl.lit(0.0)
    if effective_year >= 1998:
        rate_eitc = p.num("eitc_rate")
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
    return with_state_detail(
        df,
        agi=pl.col("ks_agi"),
        exemptions=pl.col("ks_exemp"),
        standard_deduction=pl.col("ks_stded"),
        itemized_deductions=pl.col("ks_xitded"),
        taxable_income=pl.col("ks_taxinc"),
        property_credit=pl.col("ks_pcred"),
        child_care_credit=pl.col("ks_chcr"),
        eic=pl.col("ks_earncr"),
        credits=encred + pl.col("ks_chcr") + pl.col("ks_fd") + pl.col("ks_earncr") + pl.col("ks_pcred"),
        rate=rate_expr,
    )
