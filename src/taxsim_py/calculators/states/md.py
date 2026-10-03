"""Maryland individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    pre1987_federal_itemizing,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MD_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "md" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _tiered_replace(income: pl.Expr, tiers: list[list[float]]) -> pl.Expr:
    """Select a replacement value from an income tier."""
    expr = pl.lit(None, dtype=pl.Float64)
    for lo, hi, value in tiers:
        cond = (income > lo) & (income <= hi)
        expr = pl.when(cond).then(pl.lit(float(value))).otherwise(expr)
    return expr


def compute_md_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    state_year = "md" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(MD_PARAMS, effective_year)

    is_joint = files_joint()
    is_hoh_status = pl.col("filing_status") == "head_of_household"
    is_hoh = files_head_of_household()
    is_sep = files_separate()
    is_single = files_single()
    is_single_or_separate = is_single | is_sep
    df = df.with_columns(
        md_sep=pl.when(is_sep).then(2.0).otherwise(1.0),
        md_taxpayers=taxpayer_count(),
    )
    df = df.with_columns(md_txpded=pl.col("md_taxpayers") + pl.when(is_hoh).then(1.0).otherwise(0.0))

    df = deflate_for_extrapolation(df, flate)

    # --- AGI ---
    chnum = pl.col("depx").clip(0, p["child_care_subtraction_max_children"])
    if effective_year <= 1977:
        chexp = pl.lit(0.0)
    else:
        chexp = pl.min_horizontal(p.num("child_care_subtraction_per_child") * chnum, pl.col("childcare"))
    # Pension exclusion per taxpayer 65 or older, less Social Security benefits.
    pex = p.num("pension_exclusion")
    penexc = pl.col("pensions").clip(0, (pex * aged_count() - pl.col("gssi")).clip(0, None))
    md_agi = pl.col("agi") - chexp - penexc
    if effective_year >= 1985:
        md_agi = md_agi - pl.col("taxable_social_security")

    if effective_year <= 1986:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        excl_cap = by_filing_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc_base = pl.col("dividends") + pl.col("intrec") if effective_year == 1981 else pl.col("dividends")
        divexc = pl.min_horizontal(divexc_base, excl_cap)
        md_agi = md_agi + divexc
    if effective_year <= 1986:
        # Half of federal preference income (`comnew(36)`) over $10,000 per taxpayer.
        pref = pl.col("pre1987_pref")
        md_agi = md_agi + p.num("preference_addback_share") * (
            pref - p.num("preference_exclusion_per_taxpayer") * pl.col("md_taxpayers")
        ).clip(0, None)
    if effective_year == 1980:
        md_agi = md_agi - pl.min_horizontal(pl.col("intrec"), p["interest_exclusion_per_taxpayer_1980"] * pl.col("md_taxpayers")).clip(0, None)
    if 1982 <= effective_year <= 1986:
        md_agi = md_agi + pl.col("pre1987_twoded")

    df = df.with_columns(md_agi=md_agi)

    # --- Maryland's own two-earner subtraction (1992+, joint returns only) ---
    wages_total = pl.col("pwages") + pl.col("swages")
    agih = pl.col("pwages") + 0.5 * (pl.col("agi") - wages_total)
    agiw = pl.col("swages") + 0.5 * (pl.col("agi") - wages_total)
    # Exclusions are split evenly, except that one aged spouse's pension
    # exclusion comes from the spouse's share.
    ss_subtracted = pl.col("taxable_social_security") if effective_year >= 1985 else pl.lit(0.0)
    shared = chexp + ss_subtracted
    one_aged_pension = (aged_count() == 1) & (penexc > 0)
    twoh = pl.when(one_aged_pension).then(agih - 0.5 * shared).otherwise(agih - 0.5 * (shared + penexc)).clip(0, None)
    twow = pl.when(one_aged_pension).then(agiw - 0.5 * shared - penexc).otherwise(agiw - 0.5 * (shared + penexc)).clip(0, None)
    if effective_year <= 1991:
        twoear = pl.lit(0.0)
    elif effective_year <= 1994:
        twoear = pl.when(pl.col("agi") <= p["two_earner_high_income_agi_1992_1994"]).then(
            p.num("two_earner_subtraction")
        ).otherwise(float(p["two_earner_subtraction_high_income_1992_1994"]))
        twoear = pl.min_horizontal(twoear, twoh, twow)
    else:
        twoear = pl.min_horizontal(pl.lit(p.num("two_earner_subtraction")), twoh, twow)
    two_earner_eligible = is_joint & (pl.col("agi") > 0) & (agih * agiw > 0)
    twoear = pl.when(two_earner_eligible).then(twoear).otherwise(0.0)
    df = df.with_columns(md_agi=(pl.col("md_agi") - twoear).clip(0, None))

    # --- Capital gains (discontinued 1992) ---
    capgn = pl.col("stcg") + pl.col("ltcg")
    if 1987 <= effective_year <= 1990:
        md_agi_cg = pl.when(capgn > 0).then(pl.col("md_agi") - p["capital_gains_exclusion_share_1987_1990"] * capgn).otherwise(pl.col("md_agi"))
        df = df.with_columns(md_agi=md_agi_cg)
    elif effective_year == 1991:
        cg = p["capital_gains_exclusion_1991"]
        cap = pl.when(is_joint).then(float(cg["cap"]["married_joint"])).otherwise(float(cg["cap"]["other"]))
        threshold = pl.when(is_joint).then(float(cg["income_threshold"]["married_joint"])).otherwise(
            float(cg["income_threshold"]["other"])
        )
        astep = pl.min_horizontal(cg["share"] * capgn, cap)
        bstep = ((pl.col("agi") - capgn) - threshold).clip(0, None) * cg["reduction_rate"]
        capded = (astep - bstep).clip(0, None)
        df = df.with_columns(md_agi=pl.when(capgn > 0).then(pl.col("md_agi") - capded).otherwise(pl.col("md_agi")))

    # --- Standard deduction ---
    agi_pos = pl.col("md_agi").clip(0, None)
    std_rate = p.num("standard_deduction_rate")
    if effective_year <= 1986:
        stded = pl.min_horizontal(pl.col("md_txpded") * p.num("standard_deduction_cap_per_exemption"), std_rate * agi_pos)
    elif effective_year <= 1989:
        floor_mult = float(p["standard_deduction_floor_multiplier_1987_1989"][1960])
        ceil_mult = float(p["standard_deduction_ceiling_multiplier_1987_1989"][1960])
        stded = (std_rate * pl.col("md_agi")).clip(pl.col("md_txpded") * floor_mult, pl.col("md_txpded") * ceil_mult)
        stded = stded + float(p["standard_deduction_aged_addition_1987_1989"]) * aged_count()
    elif effective_year <= 2017:
        floor_mult = float(p["standard_deduction_floor_multiplier_1990_2017"][1960])
        ceil_mult = float(p["standard_deduction_ceiling_multiplier_1990_2017"][1960])
        stded = (std_rate * pl.col("md_agi")).clip(pl.col("md_txpded") * floor_mult, pl.col("md_txpded") * ceil_mult)
    elif effective_year == 2018:
        floor_mult = float(p["standard_deduction_floor_multiplier_2018"][1960])
        ceil_mult = float(p["standard_deduction_ceiling_multiplier_2018"][1960])
        stded = (std_rate * pl.col("md_agi")).clip(pl.col("md_txpded") * floor_mult, pl.col("md_txpded") * ceil_mult)
    else:
        floor_ss = p.num("standard_deduction_single_separate_floor_2019_2021")
        ceil_ss = p.num("standard_deduction_single_separate_ceiling_2019_2021")
        floor_jh = p.num("standard_deduction_joint_hoh_floor_2019_2021")
        ceil_jh = p.num("standard_deduction_joint_hoh_ceiling_2019_2021")
        stded = pl.when(is_joint | is_hoh).then((std_rate * pl.col("md_agi")).clip(floor_jh, ceil_jh)).otherwise(
            (std_rate * pl.col("md_agi")).clip(floor_ss, ceil_ss)
        )

    if effective_year >= 2025 and behavior.mode.value == "statutory":
        flat = resolve_year(p["standard_deduction_flat_2025plus"], effective_year)
        stded = by_filing_status({k: float(v) for k, v in flat.items()})
    df = df.with_columns(md_stded=stded)

    # --- Itemized deduction ---
    if effective_year <= 1986:
        gross, _, zbr = pre1987_federal_itemizing(effective_year)
        xitded = pl.max_horizontal(gross - pl.col("state_sales_or_income_tax_ded"), zbr)
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemizing_gate = pl.col("itemizes") & (salt_plus_mortgage > 0)
        xitded_base = (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        if 1991 <= effective_year <= 2017:
            if effective_year <= 2012:
                aif92 = (
                    p.num("itemized_phaseout_inflation_factor_1992_2012")
                    if effective_year >= 1992
                    else 1.0
                )
                phas92 = p["itemized_phaseout_threshold_1991_2012"] / pl.col("md_sep") * aif92
            else:
                aif13 = p.num("itemized_phaseout_inflation_factor_2013_2017")
                mult = by_filing_status(p["itemized_phaseout_status_multiplier_2013_2017"])
                phas92 = aif13 * p["itemized_phaseout_threshold_base_2013_2017"] * mult
            xitded_high = (pl.col("itemized_deduction") - pl.col("state_sales_or_income_tax_ded") * pl.col("itemized_deduction") / salt_plus_mortgage.clip(1e-9, None)).clip(0, None)
            xitded_base = pl.when((pl.col("md_agi") > phas92) & (salt_plus_mortgage > 0)).then(xitded_high).otherwise(xitded_base)
        if effective_year >= 2025 and behavior.mode.value == "statutory":
            ph = resolve_year(p["itemized_phaseout_2025plus"], effective_year)
            threshold = by_filing_status({k: float(v) for k, v in ph.items() if k != "rate"})
            xitded_base = (xitded_base - float(ph["rate"]) * (pl.col("agi") - threshold).clip(0, None)).clip(0, None)
        xitded = pl.when(itemizing_gate).then(xitded_base).otherwise(0.0)

    df = df.with_columns(
        md_xitded=xitded,
        md_deduc=pl.when(xitded >= pl.col("md_stded")).then(xitded).otherwise(pl.col("md_stded")),
    )

    # --- Exemption ---
    exemps_count = federal_exemption_count(effective_year)
    if effective_year <= 1989:
        xmp_amt = p.num("personal_exemption_amount")
        exemp = exemps_count * xmp_amt
    else:
        xmp_amt = p.num("personal_exemption_amount")
        blage = float(p["aged_exemption"]) * aged_count()
        base_exemp = pl.col("md_taxpayers") + pl.col("depx")
        exemp = blage + base_exemp * xmp_amt
        fed_agi = pl.col("agi")
        if 2008 <= effective_year <= 2011:
            ss_tiers = p["exemption_phaseout_2008_2011_single_separate"]
            jh_tiers = p["exemption_phaseout_2008_2011_joint_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            exemp = pl.when((fed_agi > p["exemption_phaseout_agi_floor"]) & replaced.is_not_null()).then(blage + base_exemp * replaced).otherwise(exemp)
        elif effective_year >= 2012:
            ss_tiers = p["exemption_phaseout_2012plus_single_separate"]
            jh_tiers = p["exemption_phaseout_2012plus_joint_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            phased = pl.when(replaced == 0).then(0.0).otherwise(blage + base_exemp * replaced)
            exemp = pl.when((fed_agi > p["exemption_phaseout_agi_floor"]) & replaced.is_not_null()).then(phased).otherwise(exemp)
        # A dependent filer gets no exemptions from 2008; the high-income
        # reductions apply only to other returns.
        if effective_year >= 2008:
            exemp = pl.when(is_dependent_filer()).then(0.0).otherwise(exemp)

    df = df.with_columns(md_exemp=exemp)
    df = df.with_columns(md_taxinc=(pl.col("md_agi") - pl.col("md_deduc") - pl.col("md_exemp")).clip(0, None))

    # --- Bracket tax --- From 1992 single and separate returns reach the
    # top brackets at lower incomes.
    if effective_year <= 1991:
        table = p["brackets_1977_1991"]
        statax = bracket_tax(pl.col("md_taxinc"), table)
        rate_expr = bracket_rate(pl.col("md_taxinc"), table)
    elif effective_year <= 2007:
        if effective_year <= 1994:
            table_ss = p["brackets_1992_1994_single_separate"]
            table_jh = p["brackets_1992_1994_joint_hoh"]
        else:
            top_rate = p.num("brackets_1995_2007_top_rate")
            table_ss = table_jh = [*p["brackets_1995_2007_lower"], [p["brackets_1995_2007_top_start"], top_rate]]
        statax = pl.when(is_single_or_separate).then(bracket_tax(pl.col("md_taxinc"), table_ss)).otherwise(
            bracket_tax(pl.col("md_taxinc"), table_jh)
        )
        rate_expr = pl.when(is_single_or_separate).then(
            bracket_rate(pl.col("md_taxinc"), table_ss)
        ).otherwise(bracket_rate(pl.col("md_taxinc"), table_jh))
    elif effective_year <= 2011:
        statax = pl.when(is_single_or_separate).then(
            bracket_tax(pl.col("md_taxinc"), p["brackets_2008_2011_single_separate"])
        ).otherwise(bracket_tax(pl.col("md_taxinc"), p["brackets_2008_2011_joint_hoh"]))
        rate_expr = pl.when(is_single_or_separate).then(
            bracket_rate(pl.col("md_taxinc"), p["brackets_2008_2011_single_separate"])
        ).otherwise(bracket_rate(pl.col("md_taxinc"), p["brackets_2008_2011_joint_hoh"]))
    else:
        suffix = "2025plus" if effective_year >= 2025 and behavior.mode.value == "statutory" else "2012plus"
        statax = pl.when(is_single_or_separate).then(
            bracket_tax(pl.col("md_taxinc"), p[f"brackets_{suffix}_single_separate"])
        ).otherwise(bracket_tax(pl.col("md_taxinc"), p[f"brackets_{suffix}_joint_hoh"]))
        rate_expr = pl.when(is_single_or_separate).then(
            bracket_rate(pl.col("md_taxinc"), p[f"brackets_{suffix}_single_separate"])
        ).otherwise(bracket_rate(pl.col("md_taxinc"), p[f"brackets_{suffix}_joint_hoh"]))
        if suffix == "2025plus":
            sur = resolve_year(p["capital_gains_surtax_2025plus"], effective_year)
            net_gain = (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
            statax = statax + pl.when(pl.col("md_agi") > float(sur["threshold"])).then(float(sur["rate"]) * net_gain).otherwise(0.0)

    df = df.with_columns(md_taxbc=statax)

    # --- Credits ---
    earncr = pl.lit(0.0)
    if effective_year >= 1987:
        earncr = p.num("eitc_nonrefundable_share") * pl.col("eitc")
        if effective_year >= 2022:
            # Unmarried childless filers get the whole federal credit
            # (nonrefundable part first), parents and couples half.
            unmarried_childless = (pl.col("depx") < 1) & ~is_joint
            earncr = pl.when(unmarried_childless).then(pl.col("eitc")).otherwise(earncr)
    earncr = pl.min_horizontal(pl.col("md_taxbc"), earncr)
    df = df.with_columns(md_pretax=(pl.col("md_taxbc") - earncr).clip(0, None))
    df = df.with_columns(md_statax=pl.col("md_pretax"))

    # Refundable EITC.
    refcr = pl.lit(0.0)
    if effective_year >= 1998:
        eicr = p.num("refundable_eitc_rate")
        base_refcr = (eicr * pl.col("eitc") - pl.col("md_taxbc")).clip(0, None)
        if effective_year <= 2008:
            refcr = pl.when(pl.col("depx") > 0).then(base_refcr).otherwise(0.0)
        elif effective_year <= 2019:
            refcr = base_refcr
        else:
            # From 2022 married childless filers use the refundable share like
            # parents; only unmarried childless filers use the floor below.
            parent_like = (pl.col("depx") > 0) | ((effective_year >= 2022) & is_joint)
            refcr = pl.when(parent_like).then(base_refcr).otherwise(0.0)
            childless_floor = float(resolve_year(p["refundable_eitc_childless_floor_2020plus"], effective_year))
            wl2 = pl.min_horizontal(childless_floor, pl.col("eitc"))
            childless_gate = (pl.col("md_pretax") < 1.0) & ~parent_like
            childless_refcr = pl.when(pl.col("md_taxbc") < wl2).then(wl2 - pl.col("md_taxbc")).otherwise(0.0)
            refcr = pl.when(childless_gate).then(childless_refcr).otherwise(refcr)

    # Nonrefundable Child/Dependent Care Credit.
    sep = pl.col("md_sep")
    if effective_year == 2000:
        c = p["child_care_credit_rate_2000"]
        chr_ = c["unit"] * (c["units"] - ((pl.col("agi") - c["start"] / sep) / (c["step"] / sep)).clip(0, None)).clip(0, None)
    elif 2001 <= effective_year <= 2018:
        c = p["child_care_credit_rate_2001_2018"]
        step = c["step_numerator"] / (c["step_divisor"] * sep)
        chr_ = c["unit"] * (c["units"] - ((pl.col("agi") - c["start"] / sep) / step).clip(0, None)).clip(0, None)
    elif effective_year >= 2019:
        joint, other = p["child_care_credit_rate_2019"]["married_joint"], p["child_care_credit_rate_2019"]["other"]

        def phased_rate(c: dict) -> pl.Expr:
            return c["unit"] * (c["units"] - ((pl.col("agi") - c["start"]) / c["step"]).clip(0, None)).clip(0, None)

        chr_ = pl.when(is_joint).then(phased_rate(joint)).otherwise(phased_rate(other))
        fagim = pl.when(is_joint).then(float(joint["refundable_agi_limit"])).otherwise(float(other["refundable_agi_limit"]))
    else:
        chr_ = pl.lit(0.0)

    ccc_cap = pl.col("federal_chcr")
    if effective_year <= 1979:
        comnew52 = pl.col("regular_tax")
    elif effective_year <= 1986:
        comnew52 = pl.col("fiitax")
    else:
        comnew52 = pl.col("regular_tax")
    chcr = chr_ * pl.min_horizontal(ccc_cap, comnew52.clip(0, None))
    df = df.with_columns(md_statax=(pl.col("md_statax") - chcr).clip(0, None))

    # Poverty Level Credit (1997+, nonrefundable).
    if effective_year >= 1997:
        # `comnew(68)` is deflated in projected years.
        exemps_count = exemps_count / flate
        pov1 = p.num("poverty_credit_income_base")
        pov2 = p.num("poverty_credit_income_per_additional_exemption")
        xlin3 = pov1 + pov2 * (exemps_count - 1.0)
        xlin4 = pl.max_horizontal(pl.col("agi"), pl.col("earned_income"))
        ptcr = pl.when((xlin3 >= xlin4) & ~is_dependent_filer()).then(p["poverty_credit_rate"] * pl.col("earned_income")).otherwise(0.0)
    else:
        ptcr = pl.lit(0.0)
    df = df.with_columns(md_statax=(pl.col("md_statax") - ptcr).clip(0, None))

    # Senior tax credit (2022+): nonrefundable, for returns with a taxpayer 65 or older below the AGI limit.
    senior = pl.lit(0.0)
    if effective_year >= 2022:
        sc = p["senior_tax_credit_2022plus"]
        limit = pl.when(is_joint | is_hoh_status).then(float(sc["limit_joint_or_hoh"])).otherwise(float(sc["limit_other"]))
        amount = (
            pl.when(is_joint).then(pl.when(aged_count() >= 2).then(float(sc["joint_two"])).when(aged_count() == 1).then(float(sc["joint_one"])).otherwise(0.0))
            .when(is_hoh_status).then(pl.when(aged_count() > 0).then(float(sc["head_of_household"])).otherwise(0.0))
            .otherwise(pl.when(aged_count() > 0).then(float(sc["single"])).otherwise(0.0))
        )
        senior = pl.when(pl.col("agi") < limit).then(pl.min_horizontal(amount, pl.col("md_statax").clip(0, None))).otherwise(0.0)
        df = df.with_columns(md_statax=pl.col("md_statax") - senior)

    df = df.with_columns(md_statax=pl.col("md_statax") - refcr)

    # 2019+ refundable portion of the Child/Dependent Care Credit.
    chref = pl.lit(0.0)
    if effective_year >= 2019:
        chref = pl.when(pl.col("agi") <= fagim).then((chcr - pl.col("md_taxbc")).clip(0, None)).otherwise(0.0)
        df = df.with_columns(md_statax=pl.col("md_statax") - chref)

    ctc = pl.lit(0.0)
    if effective_year >= 2023:
        # Refundable child tax credit for children under six at low AGI.
        credit = p["child_tax_credit_2023plus"]
        ctc = pl.when(pl.col("agi") <= float(credit["agi_cap"])).then(float(credit["amount"]) * pl.col("dep6")).otherwise(0.0)
        if effective_year >= 2025 and behavior.mode.value == "statutory":
            po = resolve_year(p["child_tax_credit_phaseout_2025plus"], effective_year)
            steps = ((pl.col("agi") - float(po["threshold"])).clip(0, None) / float(po["increment"])).ceil()
            ctc = (float(credit["amount"]) * pl.col("dep6") - float(po["rate"]) * steps).clip(0, None)
        df = df.with_columns(md_statax=pl.col("md_statax") - ctc)

    df = df.with_columns(siitax=pl.col("md_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("md_agi"),
        exemptions=pl.col("md_exemp"),
        standard_deduction=pl.col("md_stded"),
        itemized_deductions=pl.col("md_xitded"),
        taxable_income=pl.col("md_taxinc"),
        child_care_credit=chcr,
        eic=earncr + refcr,
        credits=earncr + ptcr + refcr + chcr + chref + ctc + senior,
        rate=rate_expr,
    )
