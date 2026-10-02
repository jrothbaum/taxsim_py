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
    # Statutory mode uses Delaware's dated 2022-2024 values. They happen to
    # equal the existing TAXSIM table, but must not inherit projected-year
    # extrapolation when a caller asks for a recent year.
    state_year = "de" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(DE_PARAMS, effective_year)
    df = df.with_columns(
        de_sep=separate_divisor(),
        # Taxpayers (`data(7)`): 2 only on joint returns.
        de_taxpayers=taxpayer_count(),
        # Federal exemptions (`comnew(68)`), a count TAXSIM nonetheless
        # deflates in projected years.
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
    if effective_year >= 1984:
        low_income = (
            (aged > 0)
            & (pl.col("earned_income") < p["aged_low_income_earned_limit"] * aged)
            & (pl.col("de_agi") <= p["aged_low_income_agi_limit"] * aged)
        )
        df = df.with_columns(
            de_agi=pl.when(low_income).then(pl.col("de_agi") - p["aged_low_income_exclusion"] * aged).otherwise(pl.col("de_agi"))
        )

    # --- Standard deduction ---
    if effective_year <= 1987:
        cap_per_exemption = p.num("standard_deduction_cap_per_exemption_pre1988")
        df = df.with_columns(
            de_stded=pl.min_horizontal(
                cap_per_exemption * pl.col("de_taxpayers") / pl.col("de_sep"), p["standard_deduction_agi_share_pre1988"] * pl.col("de_agi").clip(0, None)
            )
        )
    elif effective_year <= 1998:
        flat_single = p.num("standard_deduction_flat_single_or_hoh_1988_1998")
        flat_joint = p.num("standard_deduction_flat_joint_or_sep_1988_1998")
        df = df.with_columns(
            de_stded=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(flat_single)
            .otherwise(flat_joint / pl.col("de_sep"))
        )
    elif effective_year == 1999:
        flat_single = float(p["standard_deduction_flat_single_or_hoh_1999"][1999])
        flat_joint = float(p["standard_deduction_flat_joint_or_sep_1999"][1999])
        df = df.with_columns(
            de_stded=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(flat_single)
            .otherwise(flat_joint / pl.col("de_sep"))
        )
    else:
        per_exemption = p.num("standard_deduction_per_exemption_2000plus")
        df = df.with_columns(de_stded=per_exemption * pl.col("de_taxpayers"))
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
        # `if(law.eq.1987) xitded=xitded*1.12`.
        if effective_year == 1987:
            df = df.with_columns(de_xitded_base=pl.col("de_xitded_base") * p["itemized_deduction_multiplier_1987"])
    elif effective_year == 2018:
        # 2018: `sttax=min(10000/sep,data51+data50+data54);
        # xitded=comnew(30)-min(data50,sttax-(data51+data54))` - only the
        # state tax deduction's share of the $10,000 cap is removed.
        sttax_cap = p["state_tax_deduction_cap_2018"] / pl.col("de_sep")
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

    if 1991 <= effective_year <= 2017:
        base = float(p["itemized_phaseout_base"][1960])
        if effective_year <= 2012:
            aif92 = p.num("itemized_phaseout_aif92_1992_2012") if effective_year >= 1992 else 1.0
            phas92 = base * aif92 / pl.col("de_sep")
        else:
            aif13 = p.num("itemized_phaseout_aif13_2013_2017")
            mult = p["itemized_phaseout_2013_2017_multiplier"]
            mult_expr = by_filing_status(mult)
            phas92 = aif13 * p["itemized_phaseout_base_multiple_2013_2017"] * base * mult_expr
        reduce_amt = pl.when(pl.col("de_agi") > phas92).then(
            pl.min_horizontal(
                p["itemized_phaseout_cap_rate"] * pl.col("de_xitded_base"),
                p["itemized_phaseout_rate"] * (pl.col("de_agi") - phas92),
            )
        ).otherwise(0.0)
        reduce_amt = reduce_amt * p.num("itemized_phaseout_share")
        df = df.with_columns(de_xitded=(pl.col("de_xitded_base") - reduce_amt).clip(0, None))
    else:
        df = df.with_columns(de_xitded=pl.col("de_xitded_base"))

    # `if(ided.eq.-2.and.law.eq.1999) xitded=0`: a forced standard
    # deduction zeroes itemized deductions in 1999 only.
    if effective_year == 1999:
        df = df.with_columns(de_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("de_xitded")))

    df = df.with_columns(de_deduc=pl.max_horizontal(pl.col("de_stded"), pl.col("de_xitded")))

    # --- Exemption --- (dies out after 1995 into a flat credit instead,
    # see EXEMPTIONS/personal-credit sections below)
    if effective_year <= 1987:
        # `exemp=(num*xmp(law,1))+twn(comnew(1),0,300*texp)`: plus federal
        # income tax up to $300 per taxpayer.
        pe = p.num("personal_exemption_amount")
        bonus = pl.col("fiitax").clip(0, None).clip(None, p["federal_tax_exemption_per_taxpayer_pre1988"] * pl.col("de_taxpayers"))
        df = df.with_columns(de_exemp=pl.col("de_num") * pe + bonus)
    elif effective_year <= 1995:
        pe = p.num("personal_exemption_amount")
        df = df.with_columns(de_exemp=pl.col("de_num") * pe)
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

    # --- Bracket tax ---
    year_table_map = {
        (1977, 1978): "brackets_1977_1978",
        (1979, 1979): "brackets_1979",
        (1980, 1984): "brackets_1980_1984",
        (1985, 1985): "brackets_1985",
        (1986, 1986): "brackets_1986",
        (1987, 1987): "brackets_1987",
        (1988, 1995): "brackets_1988_1995",
        (1996, 1996): "brackets_1996",
        (1997, 1998): "brackets_1997_1998",
        (1999, 1999): "brackets_1999",
        (2000, 2009): "brackets_2000_2009",
        (2010, 2011): "brackets_2010_2011",
        (2012, 2013): "brackets_2012_2013",
    }
    key = "brackets_2014plus"
    for (lo, hi), k in year_table_map.items():
        if lo <= effective_year <= hi:
            key = k
            break
    brackets = p[key]
    stat = bracket_tax(pl.col("de_taxinc"), brackets)
    stat_h = bracket_tax(pl.col("de_taxinh"), brackets)
    stat_w = bracket_tax(pl.col("de_taxinw"), brackets)
    df = df.with_columns(de_statax=pl.when(is_joint_relief).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))

    # --- Child/Dependent Care Credit ---
    ccc_rate = p.num("child_care_credit_rate")
    df = df.with_columns(de_chcr=pl.col("federal_chcr").clip(0, None) * ccc_rate)
    if effective_year >= 1999:
        cap = p.num("child_care_credit_cap_1999plus")
        df = df.with_columns(de_chcr=pl.min_horizontal(pl.col("de_chcr"), cap))

    # --- Personal Exemption Credit (1996+) --- The energy credit
    # (`data(38)`) has no TAXSIM input.
    if effective_year >= 1996:
        per_unit = p.num("personal_exemption_credit_per_unit")
        df = df.with_columns(de_pecred=(pl.col("de_num") + aged_count()) * per_unit)
    else:
        df = df.with_columns(de_pecred=pl.lit(0.0))

    df = df.with_columns(de_credit=pl.col("de_chcr") + pl.col("de_pecred"))
    df = df.with_columns(de_statax=(pl.col("de_statax") - pl.col("de_credit")).clip(0, None))

    # --- EITC (2006+) ---
    if 2006 <= effective_year <= 2020:
        rate = float(p["eitc_nonrefundable_rate_2006_2020"][2006])
        df = df.with_columns(de_earncr=rate * pl.col("eitc").clip(0, None))
        df = df.with_columns(de_statax=(pl.col("de_statax") - pl.col("de_earncr")).clip(0, None))
    elif effective_year >= 2021:
        refundable_rate = float(p["eitc_refundable_rate_2021plus"][2021])
        nonrefundable_rate = float(p["eitc_nonrefundable_rate_2021plus"][2021])
        refundable_amt = refundable_rate * pl.col("eitc").clip(0, None)
        nonrefundable_amt = nonrefundable_rate * pl.col("eitc").clip(0, None)
        use_refundable = refundable_amt > pl.col("de_statax")
        df = df.with_columns(de_earncr=pl.when(use_refundable).then(refundable_amt).otherwise(nonrefundable_amt))
        df = df.with_columns(
            de_statax=pl.when(use_refundable)
            .then(pl.col("de_statax") - pl.col("de_earncr"))
            .otherwise((pl.col("de_statax") - pl.col("de_earncr")).clip(0, None))
        )
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
