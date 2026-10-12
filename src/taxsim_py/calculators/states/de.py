"""Delaware individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_joint, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import by_filing_status, dividend_input_adjustment, forced_standard, higher_earner_share, unemployment_total, with_state_detail
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
    investment = pl.col("intrec") + pl.col("dividends") + dividend_input_adjustment() + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
    aged_cap = p.num("pension_exclusion_aged")
    penexc = (
        pl.when(aged < 1).then(pl.min_horizontal(p["pension_exclusion_young_per_taxpayer"] * pl.col("de_taxpayers"), pensions))
        .when(aged == 1).then(pl.min_horizontal(pl.lit(aged_cap), pensions + 0.5 * investment))
        .otherwise(pl.min_horizontal(pl.lit(2 * aged_cap), pensions + investment))
    ).clip(0, None)
    df = df.with_columns(de_agi=pl.col("de_agi") - penexc)
    if p.in_effect("aged_low_income_exclusion"):
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
    elif p.in_effect("state_tax_deduction_cap"):
        # Only the state tax deduction's share of the cap is removed.
        sttax_cap = p.num("state_tax_deduction_cap") / pl.col("de_sep")
        sttax = pl.min_horizontal(
            sttax_cap, pl.col("proptax") + pl.col("state_sales_or_income_tax_ded") + pl.col("otheritem")
        )
        room_after_proptax_otheritem = sttax - (pl.col("proptax") + pl.col("otheritem"))
        df = df.with_columns(
            de_xitded_base=(
                pl.col("de_raw_itemized")
                - pl.min_horizontal(pl.col("state_sales_or_income_tax_ded"), room_after_proptax_otheritem)
            ).clip(0, None)
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

    # --- Joint returns: the lower of the joint tax and the tax on each
    # earner's share ---
    is_joint_relief = (files_joint()) & (pl.col("de_agi") > 0)
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
    stat = bracket_tax(pl.col("de_taxinc"), brackets)
    stat_h = bracket_tax(pl.col("de_taxinh"), brackets)
    stat_w = bracket_tax(pl.col("de_taxinw"), brackets)
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
        df = df.with_columns(de_pecred=(pl.col("de_num") + aged_count()) * per_unit)
    else:
        df = df.with_columns(de_pecred=pl.lit(0.0))

    df = df.with_columns(de_credit=pl.col("de_chcr") + pl.col("de_pecred"))
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
