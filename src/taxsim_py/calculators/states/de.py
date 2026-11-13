"""Delaware individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_joint, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import allocate_pensions, by_filing_status, dividend_input_adjustment, forced_standard, higher_earner_share, tier_values, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

DE_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "de" / "income_tax.yaml")


def compute_de_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, DE_PARAMS)
    p = YearParams(DE_PARAMS, effective_year)
    df = df.with_columns(
        de_sep=separate_divisor(),
        de_taxpayers=taxpayer_count(),
        # Federal exemptions, a count TAXSIM nonetheless deflates in
        # projected years.
        de_exemps_raw=(
            taxpayer_count() + pl.col("depx") + aged_count()
            if effective_year <= 1986
            else pl.when(is_dependent_filer()).then(0.0).otherwise(taxpayer_count() + pl.col("depx"))
        ),
    )

    df = deflate_for_extrapolation(df, flate, extra=("de_exemps_raw",))
    df = df.with_columns(de_num=pl.col("de_exemps_raw").floor())
    if effective_year == 1989:
        df = df.with_columns(de_num=pl.col("de_num") + aged_count())

    # --- AGI --- federal AGI less taxable Social Security. 2021 law (and
    # projected years) also excludes unemployment compensation.
    df = df.with_columns(de_agi=pl.col("agi") - pl.col("taxable_social_security"))
    if effective_year == 2021:
        ui_total = unemployment_total()
        df = df.with_columns(de_agi=pl.col("de_agi") - ui_total)

    # Pension exclusion.
    aged = aged_count()
    pensions = pl.col("pensions")
    is_joint_return = files_joint()
    page = pl.col("page")
    # TAXSIM assumes a joint spouse with no age is 40.
    sage = pl.when(is_joint_return & (pl.col("sage") == 0)).then(40.0).otherwise(pl.col("sage").cast(pl.Float64))
    unified = effective_year >= 1996 or not behavior.compute_delaware_before_1996_per_statute
    if unified:
        # Taxpayers 60 or older exclude up to $12,500 of pensions, others $2,000; pensions go to
        # the spouse aged 60 or over (half each when both or neither are).
        h_old, w_old = page >= 60, sage >= 60
        pent = pl.when(~is_joint_return).then(pensions).when(h_old & ~w_old).then(pensions).when(~h_old & w_old).then(0.0).otherwise(pensions / 2)
        pens = pl.when(is_joint_return).then(pensions - pent).otherwise(0.0)
        earned = lambda wages, a, b, c_: wages + 0.9235 * (a.clip(0, None) + b.clip(0, None) + c_.clip(0, None))  # noqa: E731
        totn_h = earned(pl.col("pwages").clip(0, None), pl.col("psemp"), pl.col("pbusinc"), pl.col("pprofinc"))
        totn_w = earned(pl.col("swages").clip(0, None), pl.col("ssemp"), pl.col("sbusinc"), pl.col("sprofinc"))
        agi_pre = pl.col("de_agi")
        agilow = agi_pre < 10000.0 * pl.col("de_taxpayers")
        retdedt = pl.when(h_old).then(
            pl.min_horizontal(pent, pl.lit(12500.0)) + pl.when((totn_h < 2500) & agilow).then(2000.0).otherwise(0.0)
        ).otherwise(pl.min_horizontal(pent, pl.lit(2000.0)))
        retdeds_old = pl.min_horizontal(pens, pl.lit(12500.0))
        retdeds = pl.when(is_joint_return).then(
            pl.when(w_old).then(retdeds_old).otherwise(pl.min_horizontal(pens, pl.lit(2000.0)))
        ).otherwise(0.0)
        if not behavior.credit_delaware_spouse_low_income_exclusion_to_spouse:
            # TAXSIM assigns the spouse's low-income addition to the taxpayer's line, replacing it.
            retdedt = pl.when(is_joint_return & w_old & (totn_w < 2500) & agilow).then(
                retdeds_old + pl.min_horizontal(pens, pl.lit(2000.0))
            ).otherwise(retdedt)
        else:
            retdeds = retdeds + pl.when(is_joint_return & w_old & (totn_w < 2500) & agilow).then(2000.0).otherwise(0.0)
        if effective_year >= 2024 and not behavior.exclude_delaware_pensions_by_statute_from_2024:
            # The 2024 on routine gives the pension to the spouse 55 or older and, to a taxpayer 60 or older, adds an equal
            # share of interest and dividends to the $12,500 exclusion; others exclude up to $2,000.
            pent_24, pens_24 = allocate_pensions(pensions, 55)
            share = (pl.col("dividends") + dividend_input_adjustment() + pl.col("intrec")) / pl.col("de_taxpayers").clip(1, None)
            retdedt = pl.when(page >= 60).then(pl.min_horizontal(pent_24 + share, pl.lit(12500.0))).otherwise(pl.min_horizontal(pent_24, pl.lit(2000.0)))
            retdeds = pl.when(sage >= 60).then(pl.min_horizontal(pens_24 + share, pl.lit(12500.0))).otherwise(pl.min_horizontal(pens_24, pl.lit(2000.0)))
        df = df.with_columns(de_retdedt=retdedt, de_retdeds=retdeds, de_agi_pre=agi_pre)
        df = df.with_columns(de_agi=(pl.col("de_agi_pre") - pl.col("de_retdedt") - pl.col("de_retdeds")).clip(0, None))
    else:
        investment = pl.col("intrec") + pl.col("dividends") + dividend_input_adjustment() + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        aged_cap = p.num("pension_exclusion_aged")
        penexc = (
            pl.when(aged < 1).then(pl.min_horizontal(p["pension_exclusion_young_per_taxpayer"] * pl.col("de_taxpayers"), pensions))
            .when(aged == 1).then(pl.min_horizontal(pl.lit(aged_cap), pensions + 0.5 * investment))
            .otherwise(pl.min_horizontal(pl.lit(2 * aged_cap), pensions + investment))
        ).clip(0, None)
        df = df.with_columns(de_agi=pl.col("de_agi") - penexc)
    if p.in_effect("aged_low_income_exclusion") and effective_year < 2000 and behavior.compute_delaware_before_1996_per_statute:
        # TAXSIM already adds this inside the pension exclusion lines.
        ex = p.value("aged_low_income_exclusion")
        low_income = (
            (aged > 0)
            & (pl.col("earned_income") < ex["earned_limit"] * aged)
            & (pl.col("de_agi") <= ex["agi_limit"] * aged)
        )
        df = df.with_columns(
            de_agi=pl.when(low_income).then(pl.col("de_agi") - ex["exclusion"] * aged).otherwise(pl.col("de_agi"))
        )

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        df = df.with_columns(
            de_stded=pl.min_horizontal(
                float(sd["cap_per_taxpayer"]) * pl.col("de_taxpayers") / pl.col("de_sep"), sd["rate"] * pl.col("de_agi").clip(0, None)
            )
        )
    elif p.in_effect("standard_deduction"):
        sd = p.value("standard_deduction")
        df = df.with_columns(
            de_stded=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(float(sd["single_or_hoh"]))
            .otherwise(float(sd["married"]) / pl.col("de_sep"))
        )
    else:
        df = df.with_columns(de_stded=p.num("standard_deduction_per_taxpayer") * pl.col("de_taxpayers"))
    if effective_year >= 1987:
        aged_std = p.num("aged_standard_deduction")
        df = df.with_columns(de_stded=pl.col("de_stded") + aged_std * aged)
    elif not behavior.compute_delaware_before_1996_per_statute:
        # TAXSIM never sets the aged addition before 1987; the compiled code adds the personal exemption
        # times the exemption count (including the aged one) to a single return with a taxpayer 65 or older.
        extra = p.num("personal_exemption") * (pl.col("de_taxpayers") + pl.col("depx") + aged) * (aged >= 1).cast(pl.Float64)
        df = df.with_columns(de_stded=pl.col("de_stded") + pl.when(files_joint()).then(0.0).otherwise(extra))

    # --- Itemized deduction --- Through 1986 the itemized inputs.
    if effective_year <= 1986:
        df = df.with_columns(de_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    else:
        df = df.with_columns(de_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
    if effective_year <= 1986:
        df = df.with_columns(de_xitded_base=pl.col("de_raw_itemized"))
    elif effective_year <= 2017:
        df = df.with_columns(
            de_xitded_base=(pl.col("de_raw_itemized") - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        )
        if p.in_effect("itemized_deduction_multiplier"):
            df = df.with_columns(de_xitded_base=pl.col("de_xitded_base") * p.num("itemized_deduction_multiplier"))
    elif effective_year >= 2019:
        # Property tax up to $10,000, mortgage interest and charity; no state income tax
        # and no other itemized deductions.
        property_tax = pl.col("proptax")
        if effective_year < 2024 or behavior.cap_delaware_property_tax_deduction_per_statute:
            property_tax = pl.min_horizontal(property_tax, pl.lit(10000.0))
        df = df.with_columns(
            de_xitded_base=property_tax + pl.col("mortgage") + pl.col("charity_cash")
        )
    elif p.in_effect("state_tax_deduction_cap"):
        # The federal itemized total less the state income tax inside its capped state and local part.
        sttax_cap = p.num("state_tax_deduction_cap") / pl.col("de_sep")
        sttax = pl.min_horizontal(sttax_cap, pl.col("proptax") + pl.col("state_sales_or_income_tax_ded"))
        room_after_proptax = sttax - pl.col("proptax")
        df = df.with_columns(
            de_xitded_base=(
                pl.col("itemized_before_limit")
                - pl.min_horizontal(pl.col("state_sales_or_income_tax_ded"), room_after_proptax)
            )
        )
    else:
        df = df.with_columns(de_xitded_base=pl.col("de_raw_itemized"))

    if p.in_effect("itemized_limitation"):
        lim = p.value("itemized_limitation")
        index = p.num("itemized_limitation_index")
        if isinstance(lim["threshold"], dict):
            phas92 = by_filing_status({s: float(v) * index for s, v in lim["threshold"].items()})
        else:
            phas92 = float(lim["threshold"]) * index / pl.col("de_sep")
        reduce_amt = pl.when(pl.col("de_agi") > phas92).then(
            pl.min_horizontal(
                lim["cap_rate"] * pl.col("de_xitded_base"),
                lim["rate"] * (pl.col("de_agi") - phas92),
            )
        ).otherwise(0.0)
        reduce_amt = reduce_amt * float(lim["reduction_share"])
        df = df.with_columns(de_xitded=(pl.col("de_xitded_base") - reduce_amt).clip(0, None))
    else:
        df = df.with_columns(de_xitded=pl.col("de_xitded_base"))

    # A forced standard deduction zeroes itemized deductions in 1999 only.
    if effective_year == 1999:
        df = df.with_columns(de_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("de_xitded")))

    if effective_year >= 2018 and not behavior.let_delaware_itemize_when_federal_return_takes_standard_deduction:
        # TAXSIM zeroes the federal itemized totals it hands the state on a standard-deduction pass.
        df = df.with_columns(de_xitded=pl.when(pl.col("itemizes")).then(pl.col("de_xitded")).otherwise(0.0))
    df = df.with_columns(de_deduc=pl.max_horizontal(pl.col("de_stded"), pl.col("de_xitded")))

    # --- Exemption (through 1995) ---
    if p.in_effect("personal_exemption"):
        df = df.with_columns(de_exemp=pl.col("de_num") * p.num("personal_exemption"))
        if p.in_effect("federal_tax_exemption_per_taxpayer"):
            # Plus federal income tax up to an amount per taxpayer.
            bonus = pl.col("fiitax").clip(0, None).clip(None, p.num("federal_tax_exemption_per_taxpayer") * pl.col("de_taxpayers"))
            df = df.with_columns(de_exemp=pl.col("de_exemp") + bonus)
    else:
        df = df.with_columns(de_exemp=pl.lit(0.0))

    df = df.with_columns(de_taxinc=(pl.col("de_agi") - pl.col("de_deduc") - pl.col("de_exemp")).clip(0, None))

    # --- Joint returns: the lower of the joint tax and the tax on each earner's share ---
    is_joint_relief = (files_joint()) & (pl.col("de_agi") > 0)
    if unified:
        # TAXSIM's `splitagi`: each spouse's own income, with the rest shared equally.
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        sui_part = pl.col("sui")
        pui_part = ui_total - sui_part
        both_55 = (pl.col("page") >= 55)
        wife_55 = (sage >= 55)
        pension_ss = pensions + pl.col("taxable_social_security")
        pent_s = pl.when(both_55 & ~wife_55).then(pension_ss).when(~both_55 & wife_55).then(0.0).otherwise(pension_ss / 2)
        pens_s = pension_ss - pent_s
        seih = pl.col("psemp") + pl.col("pbusinc") + pl.col("pprofinc")
        seis = pl.col("ssemp") + pl.col("sbusinc") + pl.col("sprofinc")
        secah = pl.col("payroll_setax_primary") / 2
        secas = pl.col("payroll_setax_secondary") / 2
        split_after_exclusion = effective_year >= 2024 and not behavior.split_delaware_income_before_pension_exclusion_per_statute
        exclusion_h = pl.lit(0.0) if split_after_exclusion else pl.col("de_retdedt")
        exclusion_w = pl.lit(0.0) if split_after_exclusion else pl.col("de_retdeds")
        tinc = pl.col("pwages") + seih + pui_part - exclusion_h + pent_s
        sinc = pl.col("swages") + seis + sui_part - exclusion_w + pens_s
        # The 2024 on routine splits federal AGI (with its taxable Social Security), then takes each spouse's pension
        # exclusion and share of the Social Security.
        cinc = 0.5 * ((pl.col("agi") if split_after_exclusion else pl.col("de_agi_pre")) - tinc - sinc + secah + secas)
        agit = tinc + cinc - secah
        agis = sinc + cinc - secas
        if split_after_exclusion:
            benefit_h, benefit_w = allocate_pensions(pl.col("taxable_social_security"), 55)
            agit = agit.clip(0, None) - pl.col("de_retdedt") - benefit_h
            agis = agis.clip(0, None) - pl.col("de_retdeds") - benefit_w
        std_each = float(p.num("standard_deduction_per_taxpayer")) if p.in_effect("standard_deduction_per_taxpayer") else 0.0
        if not p.in_effect("standard_deduction_per_taxpayer") and not behavior.compute_delaware_before_1996_per_statute:
            std_each = float(p.num("taxsim_split_standard_deduction"))
        aged_h = pl.when(pl.col("page") > 0).then(pl.col("page") >= 65).otherwise(aged >= 1)
        aged_w = pl.when(pl.col("page") > 0).then(sage >= 65).otherwise(aged >= 2)
        aged_std = p.num("aged_standard_deduction") if p.in_effect("aged_standard_deduction") else 0.0
        std_h = pl.min_horizontal(agit, std_each + aged_std * aged_h.cast(pl.Float64))
        std_w = pl.min_horizontal(agis, std_each + aged_std * aged_w.cast(pl.Float64))
        itemizing_split = pl.col("de_xitded") > pl.col("de_stded") - aged_std * aged
        safe_agi = pl.when(pl.col("de_agi") != 0).then(pl.col("de_agi")).otherwise(1.0)
        deduch = pl.when(itemizing_split).then(pl.col("de_xitded") * agit / safe_agi).otherwise(std_h)
        deducw = pl.when(itemizing_split).then(pl.col("de_xitded") * agis / safe_agi).otherwise(std_w)
        df = df.with_columns(
            de_dedh=deduch, de_dedw=deducw,
            de_taxinh=(agit - deduch - 0.5 * pl.col("de_exemp")).clip(0, None),
            de_taxinw=(agis - deducw - 0.5 * pl.col("de_exemp")).clip(0, None),
        )
    else:
        df = df.with_columns(
            de_agi_higher_earner=higher_earner_share(pl.col("de_agi")),
        )
        df = df.with_columns(
            de_agi_lower_earner=pl.col("de_agi") - pl.col("de_agi_higher_earner"),
            de_dedh=pl.when(pl.col("de_agi") != 0).then(pl.col("de_deduc") * pl.col("de_agi_higher_earner") / pl.col("de_agi")).otherwise(0.0),
        )
        df = df.with_columns(de_dedw=pl.col("de_deduc") - pl.col("de_dedh"))
        df = df.with_columns(
            de_taxinh=(pl.col("de_agi_higher_earner") - pl.col("de_dedh") - 0.5 * pl.col("de_exemp")).clip(0, None),
            de_taxinw=(pl.col("de_agi_lower_earner") - pl.col("de_dedw") - 0.5 * pl.col("de_exemp")).clip(0, None),
        )

    # --- Tax ---
    brackets = p.value("brackets")
    def tax_of(income: pl.Expr) -> pl.Expr:
        if p.in_effect("taxsim_tax_table") and not behavior.use_delaware_published_tax_brackets:
            rows = p.value("taxsim_tax_table")
            rate_pct, subtract = tier_values(income, [float(r[0]) for r in rows], [float(r[1]) for r in rows], [float(r[2]) for r in rows], strict=True)
            return income * rate_pct / 100.0 - subtract
        return bracket_tax(income, brackets)

    stat = tax_of(pl.col("de_taxinc"))
    stat_h = tax_of(pl.col("de_taxinh"))
    stat_w = tax_of(pl.col("de_taxinw"))
    df = df.with_columns(de_statax=pl.when(is_joint_relief).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))

    # --- Child/Dependent Care Credit ---
    ccc_rate = p.num("child_care_credit_federal_share")
    df = df.with_columns(de_chcr=pl.col("federal_chcr").clip(0, None) * ccc_rate)
    if p.in_effect("child_care_credit_cap"):
        cap = p.num("child_care_credit_cap")
        df = df.with_columns(de_chcr=pl.min_horizontal(pl.col("de_chcr"), cap))

    # --- Personal exemption credit (1996+) ---
    if p.in_effect("personal_exemption_credit"):
        per_unit = p.num("personal_exemption_credit")
        # The extra credit is for each taxpayer 60 or older (the 2024 build used 65).
        aged_60 = (pl.col("page") >= 60).cast(pl.Float64) + (pl.col("sage") >= 60).cast(pl.Float64)
        credit_people = pl.col("de_num")
        if effective_year >= 2024 and not behavior.deny_delaware_personal_credit_to_dependent_filers_per_statute:
            # TAXSIM counts one taxpayer even for a dependent filer.
            credit_people = pl.when(is_dependent_filer()).then(1.0 + pl.col("depx")).otherwise(credit_people)
        df = df.with_columns(de_pecred=(credit_people + aged_60) * per_unit)
    else:
        df = df.with_columns(de_pecred=pl.lit(0.0))

    df = df.with_columns(de_credit=pl.col("de_chcr") + pl.col("de_pecred"))
    if unified and (effective_year >= 1996 or not behavior.compute_delaware_before_1996_per_statute):
        # Combined separate returns: each spouse's tax is cut by that spouse's personal credits,
        # dependents going first to the husband until his tax is used up.
        unit = p.num("personal_exemption_credit") if p.in_effect("personal_exemption_credit") else 0.0
        # TAXSIM credits each dependent $110 on a combined separate return in every year.
        dep_unit = unit if behavior.compute_delaware_before_1996_per_statute else p.num("taxsim_dependent_credit")
        h_aged60 = (pl.col("page") >= 60) | (aged >= 1)
        w_aged60 = (pl.col("sage") >= 60) | (aged >= 2)
        if effective_year >= 2024 and not behavior.allocate_delaware_credits_freely_between_spouses_per_statute:
            # From 2024 TAXSIM reads the ages themselves; the aged count only stands in when an age is missing.
            h_aged60 = (pl.col("page") >= 60) | ((pl.col("page") == 0) & (aged >= 1))
            w_aged60 = (pl.col("sage") >= 60) | ((pl.col("sage") == 0) & (aged >= 2))
        credit_taxpayers = pl.col("de_taxpayers")
        if effective_year >= 2024 and not behavior.deny_delaware_personal_credit_to_dependent_filers_per_statute:
            credit_taxpayers = pl.when(is_dependent_filer()).then(1.0).otherwise(credit_taxpayers)
        credit_total = unit * (credit_taxpayers + pl.col("depx")) + unit * h_aged60.cast(pl.Float64) + unit * w_aged60.cast(pl.Float64)
        sepcmb = is_joint_relief & (stat >= stat_h + stat_w)
        cred_h = unit + unit * h_aged60.cast(pl.Float64)
        cred_w = unit + unit * w_aged60.cast(pl.Float64)
        dep_max_h = ((stat_h - cred_h) / dep_unit).cast(pl.Int64).cast(pl.Float64)
        dep_h = pl.min_horizontal(pl.col("depx").cast(pl.Float64), dep_max_h)
        dep_w = pl.col("depx").cast(pl.Float64) - dep_h
        tax_h = (stat_h - cred_h - dep_h * dep_unit).clip(0, None)
        tax_w = (stat_w - cred_w - dep_w * dep_unit).clip(0, None)
        joint_after = (stat - credit_total).clip(0, None)
        statax = pl.when(sepcmb).then(pl.min_horizontal(joint_after, tax_h + tax_w)).otherwise(joint_after)
        if effective_year >= 2024 and not behavior.allocate_delaware_credits_freely_between_spouses_per_statute:
            # TAXSIM gives the taxpayer at least the credits for the taxpayer (and the aged one) and lets only the
            # dependents' credits move to the spouse, trying each split and keeping the lowest tax.
            numt = 1.0 + (pl.col("page") >= 60).cast(pl.Float64)
            nums = 1.0 + (pl.col("sage") >= 60).cast(pl.Float64)
            numd = pl.col("depx").cast(pl.Float64)
            best = (stat_h - unit * (numt + numd)).clip(0, None) + (stat_w - unit * nums).clip(0, None)
            for extra in range(1, 11):
                moved = pl.lit(float(extra))
                candidate = (stat_h - unit * (numt + (numd - moved).clip(0, None))).clip(0, None) + (
                    stat_w - unit * (nums + pl.min_horizontal(moved, numd))
                ).clip(0, None)
                best = pl.when(moved <= numd).then(pl.min_horizontal(best, candidate)).otherwise(best)
            best = pl.min_horizontal(best, (stat_h - unit * numt).clip(0, None) + (stat_w - unit * (nums + numd)).clip(0, None))
            separate_chcr = pl.min_horizontal(pl.col("de_chcr"), stat_h, stat_w)
            separate_after = best - separate_chcr
            joint_final = stat - pl.min_horizontal(stat, credit_total) - pl.col("de_chcr")
            statax = pl.when(is_joint_relief & (separate_after < joint_final)).then(separate_after).otherwise(joint_final)
            joint_after = ((stat - credit_total).clip(0, None) - pl.col("de_chcr")).clip(0, None)
            df = df.with_columns(de_chcr=pl.lit(0.0))
        df = df.with_columns(de_statax=pl.when(is_joint_relief).then(statax).otherwise(joint_after))
        chcr_limit = pl.when(sepcmb).then(pl.min_horizontal(pl.col("de_chcr"), tax_h, tax_w)).otherwise(pl.col("de_chcr"))
        df = df.with_columns(de_chcr=chcr_limit)
        df = df.with_columns(de_statax=(pl.col("de_statax") - pl.col("de_chcr")).clip(0, None))
    else:
        df = df.with_columns(de_statax=(pl.col("de_statax") - pl.col("de_credit")).clip(0, None))

    # --- EITC (2006+) ---
    if p.in_effect("eitc_refundable_share"):
        refundable_rate = p.num("eitc_refundable_share")
        nonrefundable_rate = p.num("eitc_federal_share")
        refundable_amt = refundable_rate * pl.col("eitc").clip(0, None)
        nonrefundable_amt = nonrefundable_rate * pl.col("eitc").clip(0, None)
        use_refundable = refundable_amt > pl.col("de_statax")
        df = df.with_columns(de_earncr=pl.when(use_refundable).then(refundable_amt).otherwise(nonrefundable_amt))
        df = df.with_columns(
            de_statax=pl.when(use_refundable)
            .then(pl.col("de_statax") - pl.col("de_earncr"))
            .otherwise((pl.col("de_statax") - pl.col("de_earncr")).clip(0, None))
        )
    elif p.in_effect("eitc_federal_share"):
        df = df.with_columns(de_earncr=p.num("eitc_federal_share") * pl.col("eitc").clip(0, None))
        df = df.with_columns(de_statax=(pl.col("de_statax") - pl.col("de_earncr")).clip(0, None))
    else:
        df = df.with_columns(de_earncr=pl.lit(0.0))

    df = df.with_columns(siitax=pl.col("de_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("de_agi"),
        exemptions=pl.col("de_exemp"),
        standard_deduction=pl.col("de_stded"),
        itemized_deductions=pl.col("de_xitded"),
        taxable_income=pl.col("de_taxinc"),
        child_care_credit=pl.col("de_chcr"),
        eic=pl.col("de_earncr"),
        credits=pl.col("de_credit") + pl.col("de_earncr"),
        rate=pl.when(is_joint_relief).then(bracket_rate(pl.col("de_taxinw"), brackets)).otherwise(bracket_rate(pl.col("de_taxinc"), brackets)),
    )
