"""Kansas individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.credits import child_care_credit_rate_pre2021
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
    effective_year, flate = resolve_state_year(year)
    p = YearParams(KS_PARAMS, effective_year)

    df = df.with_columns(
        ks_sep=separate_divisor(),
        ks_taxpayers=taxpayer_count(),
    )
    is_joint = files_joint()
    is_hoh = files_head_of_household()

    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    df = df.with_columns(
        ks_setax=setax,
        ks_household_income=household_income(),
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
    elif effective_year in (1983, 1984):
        cap1 = float(p["fedded_1983_1984_cap_per_filer"][1960]) * pl.col("ks_taxpayers")
        cap2 = float(p["fedded_1983_1984_upper_cap_per_filer"][1960]) * pl.col("ks_taxpayers")
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
        stded = by_filing_status(table)
        if effective_year >= 1998:
            limit = pl.max_horizontal(pl.lit(float(p["dependent_standard_deduction_minimum"])), pl.col("earned_income"))
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
        itemizing = pl.col("itemizes") & (effective_year <= 2020)

    xitded = pl.lit(0.0)
    if effective_year <= 1987:
        # Payroll and self-employment tax paid, capped, are deductible.
        socsec = taxsim_socsec(effective_year, pl.col("ks_setax"))
        # `socmax`/`selfmx` cap the addback through 1984 only.
        socmax = p.num("socsec_max_1977_1986") if effective_year <= 1984 else 1.0e20
        selfmx = p.num("selfemployment_tax_max_1977_1986") if effective_year <= 1984 else 1.0e20
        soc = socsec.clip(0, socmax * pl.col("ks_taxpayers"))
        addtx = pl.col("ks_setax").clip(0, selfmx * pl.col("ks_taxpayers"))
        base = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") + soc + addtx).clip(0, None)
        xitded = pl.when(itemizing).then(base).otherwise(0.0)
    elif effective_year <= 1990:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2009:
        aif_val = p.num("itemized_phaseout_aif_1991_2009")
        threshold = p.num("itemized_phaseout_threshold") * aif_val
        under_thr = pl.col("agi") <= threshold / pl.col("ks_sep")
        base_low = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        fline3 = itemized_deduction_local.clip(0, None)
        fline9 = pl.min_horizontal(
            p.num("itemized_phaseout_rate") * (pl.col("agi") - threshold).clip(0, None),
            p.num("itemized_phaseout_cap_rate") * fline3,
        )
        sline1 = pl.when(fline3 > 0).then(fline9 / fline3).otherwise(0.0)
        base_high = pl.when(fline3 > 0).then(
            (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") * (1 - sline1)).clip(0, None)
        ).otherwise(base_low)
        xitded = pl.when(itemizing).then(pl.when(under_thr).then(base_low).otherwise(base_high)).otherwise(0.0)
    elif effective_year <= 2012:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2017:
        aifit_val = p.num("itemized_scaling_2013plus")
        xitded = pl.when(itemizing).then(
            aifit_val * (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        ).otherwise(0.0)
    else:
        aifit_val = p.num("itemized_scaling_2013plus")
        txpaid = pl.col("proptax") + pl.col("otheritem")
        xitded = pl.when(itemizing).then(aifit_val * (txpaid + pl.col("mortgage"))).otherwise(0.0)

    if effective_year == 2021:
        # 2021: itemized inputs, whether or not the return itemizes.
        xitded = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")

    df = df.with_columns(ks_xitded=xitded)
    df = df.with_columns(ks_deduc=pl.max_horizontal(pl.col("ks_stded"), pl.col("ks_xitded")))

    # --- Exemptions ---
    # Federal exemption count (`comnew(68)`), deflated in projected years
    # before Kansas adds one for head of household.
    if effective_year <= 1986:
        comnew68 = (pl.col("ks_taxpayers") + pl.col("depx") + aged_count()) / flate
    else:
        comnew68 = pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ks_taxpayers") + pl.col("depx")) / flate
    exemps = pl.when(is_hoh).then(comnew68 + 1.0).otherwise(comnew68)
    xmp = p.num("personal_exemption_amount")
    df = df.with_columns(ks_exemp=exemps * xmp)

    df = df.with_columns(ks_taxinc=(pl.col("ks_agi") - pl.col("ks_deduc") - pl.col("ks_exemp") - fedded).clip(0, None))

    # --- Bracket tax --- Joint returns may use the lower of the joint tax
    # and the tax on each earner's share.
    def _split_allowed(yr: int) -> bool:
        return yr <= 1987 or yr >= 2013

    if effective_year <= 1987:
        table = p["brackets_pre1988"]
        halved = pl.when(is_joint).then(pl.col("ks_taxinc") / 2.0).otherwise(pl.col("ks_taxinc"))
        doubler = pl.when(is_joint).then(2.0).otherwise(1.0)
        statax = bracket_tax(halved, table) * doubler
        rate_expr = bracket_rate(pl.col("ks_taxinc"), table)
    elif effective_year <= 1989:
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1988_1989_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), p["brackets_1988_1989_single"])
        )
        rate_expr = pl.when(is_joint).then(
            bracket_rate(pl.col("ks_taxinc"), p["brackets_1988_1989_joint"])
        ).otherwise(bracket_rate(pl.col("ks_taxinc"), p["brackets_1988_1989_single"]))
    elif effective_year <= 1991:
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1990_1991_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), p["brackets_1990_1991_single"])
        )
        rate_expr = pl.when(is_joint).then(
            bracket_rate(pl.col("ks_taxinc"), p["brackets_1990_1991_joint"])
        ).otherwise(bracket_rate(pl.col("ks_taxinc"), p["brackets_1990_1991_single"]))
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
        rate_expr = pl.when(is_joint).then(
            bracket_rate(pl.col("ks_taxinc"), p["brackets_1992_2012_joint"])
        ).otherwise(bracket_rate(pl.col("ks_taxinc"), single_table))
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
        rate_expr = bracket_rate(pl.col("ks_taxinc"), table)

    if _split_allowed(effective_year):
        yh = higher_earner_share(pl.col("ks_taxinc"))
        yw = pl.col("ks_taxinc") - yh
        tax_h = bracket_tax(yh.clip(0, None), table)
        tax_w = bracket_tax(yw.clip(0, None), table)
        statax = pl.when(is_joint).then(pl.min_horizontal(statax, tax_h + tax_w)).otherwise(statax)
        # TAXSIM's shared bracket-rate variable is left by the first
        # (higher-earner) half of the split-return calculation.
        rate_expr = pl.when(is_joint).then(bracket_rate(yh.clip(0, None), table)).otherwise(rate_expr)

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
    # Child/Dependent Care Credit, none 2013-2018. It is a share of the
    # federal credit before its liability limit (`comnew(176)`); federal
    # `ccc` is 0 for 1988-1997, so those years recompute it.
    if 1988 <= effective_year <= 1997:
        ccc_p = YearParams(FEDERAL_CREDITS_PARAMS["child_care_credit"], effective_year)
        max_qualifying_persons = ccc_p.num("max_qualifying_persons")
        max_expense_per_person = ccc_p.num("max_expense_per_person_pre2021")
        ccc_rate = child_care_credit_rate_pre2021(
            pl.col("agi"),
            phase_start=ccc_p.num("pre2021_phase_start"),
            top_rate=ccc_p.num("pre2021_rate_top"),
            floor_rate=ccc_p.num("pre2021_rate_floor"),
            step_amount=ccc_p.num("pre2021_step_amount"),
        )
        num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons)
        qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person)
        ccc_earned_income_cap = pl.when(is_joint).then(
            pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
        ).otherwise(pl.col("wages"))
        ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
        chcare = ccc_rate * ccc_expense
    else:
        chcare = pl.col("federal_chcr")
    # Kansas uses `min(comnew(53), max(0, comnew(52)-data(34)))` rather
    # than the uncapped federal credit. `data(34)` is unavailable through
    # TAXSIM's public input schema and therefore zero here.
    chcare = pl.min_horizontal(chcare, pl.col("regular_tax").clip(0, None))
    if effective_year <= 1987:
        chcr = chcare * interpolate_table(pl.col("ks_agi"), p["child_care_credit_table_pre1988"])
    elif (1988 <= effective_year <= 2012) or effective_year >= 2020:
        chcr = chcare * float(p["child_care_credit_rate_1988_2012_and_2020plus"][1960])
    elif effective_year == 2019:
        chcr = chcare * float(p["child_care_credit_rate_2019"][1960])
    else:
        chcr = pl.lit(0.0)
    df = df.with_columns(ks_chcr=chcr)

    # The energy credit (`data(38)`) has no TAXSIM input.
    encred = pl.lit(0.0)

    # Homestead Property Tax Refund / food credit (`pcred`).
    rent_share = p.num("homestead_rent_share")
    pr1 = pl.col("proptax") + rent_share * pl.col("rentpaid")
    hy = pl.col("ks_household_income")
    pcred = pl.lit(0.0)
    if effective_year <= 1996:
        claw = bracket_tax(hy, p.value("homestead_reduction_schedule"))
        full_refund = hy <= p.num("homestead_full_refund_income")
    if effective_year in (1977, 1978):
        ceiling = float(p["homestead_1977_income_ceiling" if effective_year == 1977 else "homestead_1978_income_ceiling"][1960])
        under = hy <= ceiling
        cap = float(p["homestead_cap_1977_1978"][1960])
        pcred = pl.when(under).then(
            pl.when(full_refund).then(pr1).otherwise((pr1 - claw).clip(0, cap))
        ).otherwise(0.0)
    elif 1979 <= effective_year <= 1988:
        ceiling = float(p["homestead_1979_1988_income_ceiling"][1960])
        under = hy <= ceiling
        cap = float(p["homestead_cap_1979_1988"][1960])
        pcred = pl.when(under).then(
            pl.when(full_refund).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif 1989 <= effective_year <= 1994:
        ceiling = float(p["homestead_1989_1994_income_ceiling"][1960])
        under = hy <= ceiling
        cap = float(p["homestead_cap_1989_1994"][1960])
        pcred = pl.when(under).then(
            pl.when(full_refund).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif effective_year in (1995, 1996):
        ceiling = float(p["homestead_1995_1996_income_ceiling"][1960])
        if effective_year == 1996:
            # TAXSIM's test reads `1996 or (1995 and income <= ceiling)`.
            ceiling = 1.0e20
        under = hy <= ceiling
        cap = float(p["homestead_cap_1995_1996"][1960])
        pcred = pl.when(under).then(
            pl.when(full_refund).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif effective_year >= 1997:
        pt_cap = p.num("homestead_pt_cap")
        ptax = pl.min_horizontal(pt_cap, pr1)
        if effective_year <= 2005:
            hhy = hy
        else:
            hhy = pl.col("ks_agi") + pl.col("eitc") + 0.5 * pl.col("gssi")
        pmax = p.num("homestead_hy_ceiling_by_year")
        table = p["homestead_table_1997_2004"] if effective_year <= 2004 else p["homestead_table_2005plus"]
        pcred = pl.when(hhy < pmax).then(ptax * interpolate_table(hhy, table)).otherwise(0.0)
        if effective_year >= 2008:
            # Property Tax Relief for low income seniors replaces it.
            share = p.num("senior_property_relief_share")
            limit = p.num("senior_property_relief_income_limit")
            senior = (aged_count() > 0) & (pl.col("proptax") > 0) & (hy < limit)
            pcred = pl.when(senior).then(share * pl.col("proptax")).otherwise(pcred)
    df = df.with_columns(ks_pcred=pcred)

    # Food Sales Tax Refund.
    fd = pl.lit(0.0)
    if effective_year <= 1985:
        per_aged = float(p["food_refund_aged_pre1986"])
        fd = pl.when(hy <= p["food_refund_aged_income_limit_pre1986"]).then(
            per_aged * pl.min_horizontal(aged_count(), pl.col("ks_taxpayers") + pl.col("depx"))
        ).otherwise(0.0)
    elif 1986 <= effective_year <= 1997:
        extra = pl.col("ks_taxpayers") + pl.col("depx") - 1.0
        tiers = p["food_refund_1986_1997"]
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
