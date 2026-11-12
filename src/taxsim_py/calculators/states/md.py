"""Maryland individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import (
    federal_zero_bracket,
    phaseout_steps,
    federal_dividend_exclusion_limit,
    by_filing_status,
    REVERSE_FALLING_TABLE_WEIGHTS,
    interpolate_table,
    pre1987_federal_itemizing,
    split_agi,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MD_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "md" / "income_tax.yaml")
FEDERAL_ITEMIZED_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "itemized.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _tiered_replace(income: pl.Expr, tiers: list[list[float]]) -> pl.Expr:
    """Select a replacement value from an income tier."""
    expr = pl.lit(None, dtype=pl.Float64)
    for lo, hi, value in tiers:
        cond = (income > lo) & (income <= hi)
        expr = pl.when(cond).then(pl.lit(float(value))).otherwise(expr)
    return expr


def compute_md_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, MD_PARAMS)
    p = YearParams(MD_PARAMS, effective_year)
    FEDERAL_ITEMIZED = YearParams(FEDERAL_ITEMIZED_PARAMS, effective_year) if effective_year >= 2018 else None

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
        excl_cap = federal_dividend_exclusion_limit(effective_year, behavior)
        divexc_base = pl.col("dividends") + pl.col("intrec") if effective_year == 1981 else pl.col("dividends")
        divexc = pl.min_horizontal(divexc_base, excl_cap)
        md_agi = md_agi + divexc
    if p.in_effect("preference_addback_share"):
        pref = pl.col("pre1987_pref")
        md_agi = md_agi + p.num("preference_addback_share") * (
            pref - p.num("preference_exclusion_per_taxpayer") * pl.col("md_taxpayers")
        ).clip(0, None)
    if p.in_effect("interest_exclusion_per_taxpayer"):
        md_agi = md_agi - pl.min_horizontal(pl.col("intrec"), p.num("interest_exclusion_per_taxpayer") * pl.col("md_taxpayers")).clip(0, None)
    if 1982 <= effective_year <= 1986:
        md_agi = md_agi + pl.col("pre1987_twoded")

    df = df.with_columns(md_agi=md_agi)

    # --- Maryland's own two-earner subtraction (1992+, joint returns only) ---
    wages_total = pl.col("pwages") + pl.col("swages")
    agih = pl.col("pwages") + 0.5 * (pl.col("agi") - wages_total)
    agiw = pl.col("swages") + 0.5 * (pl.col("agi") - wages_total)
    if not behavior.split_maryland_joint_income_per_statute:
        # TAXSIM's `splitagi`: each spouse's own income (self-employment and benefits too), the rest shared.
        agih, agiw = split_agi(pl.col("agi"), 0.0, 0.0)
    # Exclusions are split evenly, except that one aged spouse's pension
    # exclusion comes from the spouse's share.
    ss_subtracted = pl.col("taxable_social_security") if effective_year >= 1985 else pl.lit(0.0)
    shared = chexp + ss_subtracted
    one_aged_pension = (aged_count() == 1) & (penexc > 0)
    twoh = pl.when(one_aged_pension).then(agih - 0.5 * shared).otherwise(agih - 0.5 * (shared + penexc)).clip(0, None)
    twow = pl.when(one_aged_pension).then(agiw - 0.5 * shared - penexc).otherwise(agiw - 0.5 * (shared + penexc)).clip(0, None)
    if effective_year <= 1991:
        twoear = pl.lit(0.0)
    elif p.in_effect("two_earner_subtraction_high_income"):
        high = p.value("two_earner_subtraction_high_income")
        twoear = pl.when(pl.col("agi") <= high["agi_limit"]).then(
            p.num("two_earner_subtraction")
        ).otherwise(float(high["amount"]))
        twoear = pl.min_horizontal(twoear, twoh, twow)
    else:
        twoear = pl.min_horizontal(pl.lit(p.num("two_earner_subtraction")), twoh, twow)
    two_earner_eligible = is_joint & (pl.col("agi") > 0) & (agih * agiw > 0)
    twoear = pl.when(two_earner_eligible).then(twoear).otherwise(0.0)
    df = df.with_columns(md_agi=(pl.col("md_agi") - twoear).clip(0, None))

    # --- Capital gains (discontinued 1992) ---
    capgn = pl.col("stcg") + pl.col("ltcg")
    if p.in_effect("capital_gains_exclusion_share"):
        md_agi_cg = pl.when(capgn > 0).then(pl.col("md_agi") - p.num("capital_gains_exclusion_share") * capgn).otherwise(pl.col("md_agi"))
        df = df.with_columns(md_agi=md_agi_cg)
    elif p.in_effect("capital_gains_exclusion_capped"):
        cg = p.value("capital_gains_exclusion_capped")
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
    if p.in_effect("standard_deduction_cap_per_exemption"):
        stded = pl.min_horizontal(pl.col("md_txpded") * p.num("standard_deduction_cap_per_exemption"), std_rate * agi_pos)
    elif p.in_effect("standard_deduction_limits_per_exemption"):
        limits = p.value("standard_deduction_limits_per_exemption")
        stded = (std_rate * pl.col("md_agi")).clip(pl.col("md_txpded") * float(limits["floor"]), pl.col("md_txpded") * float(limits["ceiling"]))
        if p.in_effect("aged_standard_deduction"):
            stded = stded + p.num("aged_standard_deduction") * aged_count()
    elif p.in_effect("standard_deduction_limits"):
        limits = p.value("standard_deduction_limits")
        ss, jh = limits["single_or_separate"], limits["joint_or_hoh"]
        stded = pl.when(is_joint | is_hoh).then((std_rate * pl.col("md_agi")).clip(float(jh["floor"]), float(jh["ceiling"]))).otherwise(
            (std_rate * pl.col("md_agi")).clip(float(ss["floor"]), float(ss["ceiling"]))
        )
    else:
        stded = by_filing_status({k: float(v) for k, v in p.value("standard_deduction_flat").items()})
    df = df.with_columns(md_stded=stded)

    # --- Itemized deduction ---
    if effective_year <= 1986:
        gross, fed_itemizes_pre87, zbr = pre1987_federal_itemizing(effective_year)
        xitded = pl.max_horizontal(gross - pl.col("state_sales_or_income_tax_ded"), zbr)
        if not behavior.use_maryland_itemized_deduction_without_federal_itemizing:
            # The deduction counts only when the federal return itemizes.
            xitded = pl.when(fed_itemizes_pre87).then(xitded).otherwise(0.0)
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemizing_gate = pl.col("itemizes") & (salt_plus_mortgage > 0)
        xitded_base = (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        if 2018 <= effective_year <= 2021 and not behavior.compute_maryland_itemized_deduction_from_2018_per_statute:
            # TAXSIM: the federal total less the state and local tax (capped at $10,000 with property tax
            # first) plus the property tax (also capped).
            salt_cap = FEDERAL_ITEMIZED.num("salt_cap") / pl.col("md_sep")
            capsalt = pl.min_horizontal(salt_cap, pl.col("state_sales_or_income_tax_ded") + pl.col("proptax"))
            capptax = pl.min_horizontal(salt_cap, pl.col("proptax"))
            xitded_base = (pl.col("itemized_deduction") - capsalt).clip(0, None) + capptax
            itemizing_gate = pl.col("itemizes") & (pl.col("itemized_deduction") > 0)
        if effective_year >= 2022 and not behavior.compute_maryland_itemized_deduction_from_2018_per_statute:
            # TAXSIM (2022 on): the federal total less the state and local tax above the cap.
            salt_cap = FEDERAL_ITEMIZED.num("salt_cap") / pl.col("md_sep")
            excess_salt = (pl.col("proptax") + pl.col("state_sales_or_income_tax_ded") - salt_cap).clip(0, None)
            xitded_base = (pl.col("itemized_deduction") - excess_salt).clip(0, None)
            itemizing_gate = pl.col("itemizes")
        if effective_year <= 2017 and not behavior.floor_maryland_itemized_deduction_at_zero:
            # TAXSIM floors it at the federal zero bracket amount (the standard deduction on the pass that
            # does not force itemizing).
            xitded_base = pl.max_horizontal(xitded_base, federal_zero_bracket())
        if 1991 <= effective_year <= 2017:
            threshold = p.value("itemized_limitation_threshold")
            index = p.num("itemized_limitation_index")
            if isinstance(threshold, dict):
                phas92 = by_filing_status({s: float(v) * index for s, v in threshold.items()})
            else:
                phas92 = float(threshold) / pl.col("md_sep") * index
            xitded_high = (pl.col("itemized_deduction") - pl.col("state_sales_or_income_tax_ded") * pl.col("itemized_deduction") / salt_plus_mortgage.clip(1e-9, None)).clip(0, None)
            xitded_base = pl.when((pl.col("md_agi") > phas92) & (salt_plus_mortgage > 0)).then(xitded_high).otherwise(xitded_base)
        if p.in_effect("itemized_deduction_phaseout"):
            ph = p.value("itemized_deduction_phaseout")
            threshold = by_filing_status({k: float(v) for k, v in ph.items() if k != "rate"})
            xitded_base = (xitded_base - float(ph["rate"]) * (pl.col("agi") - threshold).clip(0, None)).clip(0, None)
        xitded = pl.when(itemizing_gate).then(xitded_base).otherwise(0.0)

    df = df.with_columns(
        md_xitded=xitded,
        md_deduc=pl.when(xitded >= pl.col("md_stded")).then(xitded).otherwise(pl.col("md_stded")),
    )

    # --- Exemption ---
    exemps_count = federal_exemption_count(effective_year)
    xmp_amt = p.num("personal_exemption")
    if not p.in_effect("aged_exemption"):
        exemp = exemps_count * xmp_amt
    else:
        blage = p.num("aged_exemption") * aged_count()
        base_exemp = pl.col("md_taxpayers") + pl.col("depx")
        exemp = blage + base_exemp * xmp_amt
        fed_agi = pl.col("agi")
        if 2008 <= effective_year <= 2011:
            tiers = p.value("exemption_phaseout")
            ss_tiers, jh_tiers = tiers["single_or_separate"], tiers["joint_or_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            exemp = pl.when((fed_agi > p["exemption_phaseout_agi_floor"]) & replaced.is_not_null()).then(blage + base_exemp * replaced).otherwise(exemp)
        elif effective_year >= 2012:
            tiers = p.value("exemption_phaseout")
            ss_tiers, jh_tiers = tiers["single_or_separate"], tiers["joint_or_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            phased = pl.when(replaced == 0).then(0.0).otherwise(blage + base_exemp * replaced)
            exemp = pl.when((fed_agi > p["exemption_phaseout_agi_floor"]) & replaced.is_not_null()).then(phased).otherwise(exemp)
        if effective_year >= 2008 and not behavior.step_maryland_exemption_phaseout_per_statute:
            table = p.value("taxsim_exemption_table")
            rows_ss, rows_jh = table["single_or_separate"], table["joint_or_hoh"]
            token = REVERSE_FALLING_TABLE_WEIGHTS.set(effective_year < 2012)  # `tablki` to 2011, `tablk3` after
            try:
                per_exemption = pl.when(is_single_or_separate).then(interpolate_table(fed_agi, rows_ss)).otherwise(
                    interpolate_table(fed_agi, rows_jh)
                )
            finally:
                REVERSE_FALLING_TABLE_WEIGHTS.reset(token)
            exemp = blage + base_exemp * per_exemption
        # A dependent filer gets no exemptions from 2008; the high-income
        # reductions apply only to other returns.
        if effective_year >= 2008:
            exemp = pl.when(is_dependent_filer()).then(0.0).otherwise(exemp)

    df = df.with_columns(md_exemp=exemp)
    df = df.with_columns(md_taxinc=(pl.col("md_agi") - pl.col("md_deduc") - pl.col("md_exemp")).clip(0, None))

    # --- Tax ---
    brackets = p.value("brackets")
    if isinstance(brackets, list):
        statax = bracket_tax(pl.col("md_taxinc"), brackets)
        rate_expr = bracket_rate(pl.col("md_taxinc"), brackets)
    else:
        table_ss, table_jh = brackets["single_or_separate"], brackets["joint_or_hoh"]
        statax = pl.when(is_single_or_separate).then(bracket_tax(pl.col("md_taxinc"), table_ss)).otherwise(
            bracket_tax(pl.col("md_taxinc"), table_jh)
        )
        rate_expr = pl.when(is_single_or_separate).then(
            bracket_rate(pl.col("md_taxinc"), table_ss)
        ).otherwise(bracket_rate(pl.col("md_taxinc"), table_jh))
    if p.in_effect("taxsim_tax_offset") and not behavior.use_maryland_published_2025_tax_table_per_statute:
        # TAXSIM's 2025 single table has constants that do not match its rates in two bands.
        for offset in p.value("taxsim_tax_offset"):
            in_band = is_single_or_separate & (pl.col("md_taxinc") >= float(offset["from"])) & (pl.col("md_taxinc") < float(offset["to"]))
            statax = statax + pl.when(in_band).then(float(offset["amount"])).otherwise(0.0)
    if p.in_effect("capital_gains_surtax"):
        sur = p.value("capital_gains_surtax")
        net_gain = (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        statax = statax + pl.when(pl.col("md_agi") > float(sur["threshold"])).then(float(sur["rate"]) * net_gain).otherwise(0.0)

    df = df.with_columns(md_taxbc=statax)

    # --- Credits ---
    eitc = pl.col("eitc")
    if effective_year >= 2022 and not behavior.apply_maryland_earned_income_credit_age_test_per_statute:
        # TAXSIM skips the age test for childless joint returns whose older taxpayer is 25 or younger (or has no age)
        # or 65 or older, and gives dependent filers nothing.
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        eitc = pl.when(is_joint & (pl.col("depx") == 0) & ((older <= 25) | (older >= 65))).then(pl.col("eitc_before_filer_test") / flate).otherwise(eitc)
        eitc = pl.when(is_dependent_filer()).then(0.0).otherwise(eitc)
    earncr = pl.lit(0.0)
    if p.in_effect("eitc_nonrefundable_share"):
        earncr = p.num("eitc_nonrefundable_share") * eitc
        if effective_year >= 2022:
            # Unmarried childless filers get the whole federal credit
            # (nonrefundable part first), parents and couples half.
            unmarried_childless = (pl.col("depx") < 1) & ~is_joint
            earncr = pl.when(unmarried_childless).then(eitc).otherwise(earncr)
    earncr = pl.min_horizontal(pl.col("md_taxbc"), earncr)
    df = df.with_columns(md_pretax=(pl.col("md_taxbc") - earncr).clip(0, None))
    df = df.with_columns(md_statax=pl.col("md_pretax"))

    # Refundable EITC.
    refcr = pl.lit(0.0)
    if p.in_effect("refundable_eitc_rate"):
        eicr = p.num("refundable_eitc_rate")
        base_refcr = (eicr * eitc - pl.col("md_taxbc")).clip(0, None)
        if effective_year <= 2019 and not behavior.pay_maryland_refundable_eitc_only_above_tax:
            # Through 2019 TAXSIM pays the refundable share in full, on top of the nonrefundable credit.
            base_refcr = eicr * eitc
        if effective_year <= 2008:
            refcr = pl.when(pl.col("depx") > 0).then(base_refcr).otherwise(0.0)
        elif effective_year <= 2019:
            refcr = base_refcr
        else:
            # From 2022 married childless filers use the refundable share like
            # parents; only unmarried childless filers use the floor below.
            parent_like = (pl.col("depx") > 0) | ((effective_year >= 2022) & is_joint)
            refcr = pl.when(parent_like).then(base_refcr).otherwise(0.0)
            childless_floor = p.num("refundable_eitc_childless_floor")
            if p.in_effect("taxsim_refundable_eitc_childless_floor") and not behavior.lift_maryland_childless_refundable_credit_limit_per_statute:
                childless_floor = p.num("taxsim_refundable_eitc_childless_floor")
            wl2 = pl.min_horizontal(childless_floor, eitc)
            childless_gate = (pl.col("md_pretax") < 1.0) & ~parent_like
            childless_refcr = pl.when(pl.col("md_taxbc") < wl2).then(wl2 - pl.col("md_taxbc")).otherwise(0.0)
            refcr = pl.when(childless_gate).then(childless_refcr).otherwise(refcr)

    # Nonrefundable Child/Dependent Care Credit.
    sep = pl.col("md_sep")
    if effective_year == 2000:
        c = p.value("child_care_credit_rate")
        chr_ = c["unit"] * (c["units"] - ((pl.col("agi") - c["start"] / sep) / (c["step"] / sep)).clip(0, None)).clip(0, None)
    elif 2001 <= effective_year <= 2018:
        c = p.value("child_care_credit_rate")
        step = c["step_numerator"] / (c["step_divisor"] * sep)
        unit = c["unit"] if behavior.scale_maryland_child_care_credit_per_statute else p.num("taxsim_child_care_credit_unit")
        chr_ = unit * (c["units"] - ((pl.col("agi") - c["start"] / sep) / step).clip(0, None)).clip(0, None)
    elif effective_year >= 2019:
        rates = p.value("child_care_credit_rate")
        joint, other = rates["married_joint"], rates["other"]

        def phased_rate(c: dict) -> pl.Expr:
            steps = ((pl.col("agi") - c["start"]) / c["step"]).clip(0, None)
            if not behavior.scale_maryland_child_care_credit_per_statute:
                return c["unit"] * c["units"] * (1.0 - p.num("taxsim_child_care_credit_reduction_per_step") * steps).clip(0, None)
            return c["unit"] * (c["units"] - steps).clip(0, None)

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
    if p.in_effect("poverty_credit_income_base"):
        # The federal exemption count is deflated in projected years.
        exemps_count = exemps_count / flate
        pov1 = p.num("poverty_credit_income_base")
        pov2 = p.num("poverty_credit_income_per_additional_exemption")
        xlin3 = pov1 + pov2 * (exemps_count - 1.0)
        xlin4 = pl.max_horizontal(pl.col("agi"), pl.col("earned_income"))
        ptcr = pl.when((xlin3 >= xlin4) & ~is_dependent_filer()).then(p["poverty_credit_rate"] * pl.col("earned_income")).otherwise(0.0)
        if not behavior.pay_maryland_poverty_level_credit_per_statute:
            # TAXSIM works the credit out but never takes it off the tax.
            ptcr = pl.lit(0.0)
    else:
        ptcr = pl.lit(0.0)
    df = df.with_columns(md_statax=(pl.col("md_statax") - ptcr).clip(0, None))

    # Senior tax credit (2022+): nonrefundable, for returns with a taxpayer 65 or older below the AGI limit.
    senior = pl.lit(0.0)
    if p.in_effect("senior_tax_credit"):
        sc = p.value("senior_tax_credit")
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

    if effective_year in (2020, 2021) and not behavior.compute_maryland_earned_income_credit_per_statute:
        # TAXSIM 2020-21: an unmarried filer with no children gets back the whole tax less a $530 credit
        # (so pays nothing once the earned income credit is gone), and a $300 rebate in 2021.
        taxbc = pl.col("md_taxbc")
        c59 = pl.col("eitc")
        childless_single = (pl.col("filing_status") == "single") & (pl.col("depx") == 0)
        earn_t = pl.when(childless_single).then(pl.min_horizontal(pl.lit(530.0), pl.col("eitc_before_age_test"))).otherwise(0.0)
        x3 = pl.when(childless_single).then(taxbc - 0.5 * c59).otherwise(0.0)
        parent_or_couple = (pl.col("depx") > 0) | is_joint
        refcr_parent = (p.num("refundable_eitc_rate") * c59 - taxbc).clip(0, None)
        refcr_t = (
            pl.when(x3 > 0).then(pl.when(taxbc >= earn_t).then(taxbc - earn_t).otherwise(0.0))
            .when(parent_or_couple).then(refcr_parent)
            .otherwise(0.0)
        )
        rebate_t = pl.lit(0.0)
        if effective_year == 2021:
            rebate_t = pl.when(earn_t > 0).then(pl.when(taxpayer_count() == 1).then(300.0).otherwise(500.0)).otherwise(0.0)
        credno = pl.min_horizontal(0.5 * c59 + (chcr if effective_year != 2021 else 0.0), taxbc)
        credref = refcr_t + chref + (chr_ * pl.col("federal_chcr") if effective_year == 2021 else 0.0)
        df = df.with_columns(md_statax=taxbc - credno - credref - rebate_t)

    ctc = pl.lit(0.0)
    if p.in_effect("child_tax_credit"):
        credit = p.value("child_tax_credit")
        ctc = pl.when(pl.col("agi") <= float(credit["agi_cap"])).then(float(credit["amount"]) * pl.col("dep6")).otherwise(0.0)
        if p.in_effect("child_tax_credit_phaseout"):
            po = p.value("child_tax_credit_phaseout")
            steps = phaseout_steps((pl.col("agi") - float(po["threshold"])).clip(0, None) / float(po["increment"]), behavior)
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
