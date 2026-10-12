"""District of Columbia individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_single, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_exclusion_addback, forced_standard, higher_earner_share, household_income, tier_values, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

DC_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "dc" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
FEDERAL_PERSONAL_EXEMPTION_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "personal_exemption.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

def compute_dc_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, DC_PARAMS)
    p = YearParams(DC_PARAMS, effective_year)
    df = df.with_columns(
        dc_sep=separate_divisor(),
        dc_taxpayers=taxpayer_count(),
    )

    df = df.with_columns(
        dc_household_income=household_income()
    )
    df = deflate_for_extrapolation(df, flate, extra=("dc_household_income",))

    # --- AGI ---
    if effective_year <= 1981:
        addit = dividend_exclusion_addback(effective_year)
        # Other non-property income is not DC income before 1982.
        df = df.with_columns(
            dc_agi=pl.col("agi") + addit - pl.col("taxable_unemployment") - pl.col("nonprop")
        )
    else:
        df = df.with_columns(dc_agi=pl.col("agi"))
        if 1982 <= effective_year <= 1986:
            p87 = YearParams(PRE1987_PARAMS, effective_year)
            two_earner_rate = p87.num("two_earner_deduction_rate")
            two_earner_cap = p87.num("two_earner_deduction_cap")
            twoded = (
                two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
            ).clip(0, two_earner_cap)
            df = df.with_columns(dc_agi=pl.col("dc_agi") + twoded)
        # Social Security benefits are exempt from 1984.
        if effective_year >= 1984:
            df = df.with_columns(dc_agi=pl.col("dc_agi") - pl.col("taxable_social_security"))

    # Unemployment compensation is excluded in every year.
    ui_total = unemployment_total()
    df = df.with_columns(dc_agi=pl.col("dc_agi") - ui_total)

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        df = df.with_columns(dc_stded=(sd["rate"] * pl.col("dc_agi")).clip(0, float(sd["cap"]) / pl.col("dc_sep")))
    elif p.in_effect("standard_deduction"):
        sd = p.value("standard_deduction")
        if isinstance(sd, dict):
            df = df.with_columns(
                dc_stded=pl.when(files_joint())
                .then(float(sd["married_joint"]))
                .when(files_head_of_household())
                .then(float(sd["head_of_household"]))
                .otherwise(float(sd["single"]))
            )
        elif p.in_effect("standard_deduction_property_tax_addition"):
            addback = pl.min_horizontal(pl.col("proptax"), pl.col("dc_taxpayers") * p.num("standard_deduction_property_tax_addition"))
            df = df.with_columns(dc_stded=float(sd) / pl.col("dc_sep") + addback)
        else:
            df = df.with_columns(dc_stded=float(sd) / pl.col("dc_sep"))
    else:
        # The federal standard deduction; separate returns use the joint
        # amount halved.
        fed = YearParams(FEDERAL_INCOME_TAX_PARAMS, effective_year)
        single_sd = fed.num("standard_deduction", "single")
        joint_sd = fed.num("standard_deduction", "married_joint")
        hoh_sd = fed.num("standard_deduction", "head_of_household")
        if p.in_effect("standard_deduction_nonconforming"):
            nonconforming = p.value("standard_deduction_nonconforming")
            single_sd, joint_sd, hoh_sd = (float(nonconforming[k]) for k in ("single", "married_joint", "head_of_household"))
        df = df.with_columns(
            dc_stded=pl.when(files_single())
            .then(single_sd)
            .when(files_head_of_household())
            .then(hoh_sd)
            .otherwise(joint_sd / pl.col("dc_sep"))
        )
    if effective_year >= 2018:
        # The federal amounts: extra for each taxpayer 65 or older, and the
        # dependent filer's limit.
        aged_std = by_filing_status({
            s: resolve_year(FEDERAL_INCOME_TAX_PARAMS["aged_standard_deduction"][s], effective_year) for s in _PRE1987_STATUSES
        })
        dep_p = YearParams(FEDERAL_INCOME_TAX_PARAMS["dependent_standard_deduction"], effective_year)
        earnkd = pl.col("wages") + (pl.col("psemp") + pl.col("ssemp")).clip(0, None) + float(
            dep_p.value("earned_income_addition")
        )
        std = pl.col("dc_stded") + aged_std * aged_count()
        std = pl.when(is_dependent_filer()).then(
            pl.min_horizontal(std, pl.max_horizontal(pl.lit(dep_p.num("minimum")), earnkd))
        ).otherwise(std)
        df = df.with_columns(dc_stded=std)
    df = df.with_columns(dc_deduc=pl.col("dc_stded"))

    # --- Itemized deduction --- Before 1987 the federal gross and
    # allowed itemized totals are equal, so they are rebuilt from the inputs.
    if effective_year <= 1986:
        df = df.with_columns(
            dc_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
    else:
        df = df.with_columns(dc_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
    # DC itemizes exactly when the federal return does.
    if effective_year <= 1986:
        # Federal itemized deductions, zero when the federal return does not
        # itemize.
        itemizing_gate = pl.col("pre1987_itemizes")
        df = df.with_columns(dc_raw_itemized=pl.col("pre1987_deduc"))
    else:
        itemizing_gate = pl.col("itemizes")

    if effective_year <= 1981:
        df = df.with_columns(dc_xitded=pl.col("dc_raw_itemized"))
    elif 1982 <= effective_year <= 2017:
        salt_share = (pl.col("state_sales_or_income_tax_ded") / pl.col("dc_raw_itemized").clip(1e-9, None))
        df = df.with_columns(
            dc_xitded=(pl.col("itemized_deduction") * (1.0 - salt_share)).clip(0, None)
        )
    if 1982 <= effective_year <= 1986:
        # Without a federal limitation, itemized deductions less the state
        # tax deduction are the itemized inputs.
        df = df.with_columns(
            dc_xitded=(pl.col("dc_raw_itemized") - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        )
    if p.in_effect("state_tax_deduction_cap"):
        sttax_cap = p.num("state_tax_deduction_cap") / pl.col("dc_sep")
        sttax = pl.min_horizontal(
            sttax_cap, pl.col("proptax") + pl.col("state_sales_or_income_tax_ded") + pl.col("otheritem")
        )
        room_after_proptax_otheritem = sttax - (pl.col("proptax") + pl.col("otheritem"))
        df = df.with_columns(
            dc_xitded=(
                pl.col("dc_raw_itemized")
                - pl.min_horizontal(pl.col("state_sales_or_income_tax_ded"), room_after_proptax_otheritem)
            ).clip(0, None)
        )

    reduction = p["itemized_reduction"]
    threshold = float(reduction["threshold"]) / pl.col("dc_sep")
    excess = (pl.col("dc_agi") - threshold).clip(0, None)
    df = df.with_columns(dc_xitded=(pl.col("dc_xitded") - reduction["rate"] * excess).clip(0, None))

    # Not itemizing federally leaves `xitded` at 0, which the joint
    # earner split below also compares against.
    df = df.with_columns(dc_xitded=pl.when(itemizing_gate).then(pl.col("dc_xitded")).otherwise(0.0))

    if effective_year == 1999:
        df = df.with_columns(dc_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("dc_xitded")))

    df = df.with_columns(
        dc_deduc=pl.when(itemizing_gate).then(pl.col("dc_xitded")).otherwise(pl.col("dc_deduc"))
    )

    # --- Exemption ---
    df = df.with_columns(dc_num=pl.col("dc_taxpayers") + pl.col("depx") + aged_count())
    amnt = p.num("personal_exemption")
    df = df.with_columns(dc_exemp=pl.col("dc_num") * amnt)
    df = df.with_columns(
        dc_exemp=pl.when(files_head_of_household())
        .then(pl.col("dc_exemp") + amnt)
        .otherwise(pl.col("dc_exemp"))
    )
    df = df.with_columns(dc_exemp=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("dc_exemp")))
    if p.in_effect("exemption_phaseout"):
        ep = p.value("exemption_phaseout")
        floor, ceiling = float(ep["floor"]), float(ep["ceiling"])
        band = float(ep["band"]) / pl.col("dc_sep")
        ratio = (
            pl.when(pl.col("dc_agi") > ceiling)
            .then(1.0)
            .when(pl.col("dc_agi") > floor)
            .then(pl.min_horizontal(1.0, ep["share_per_band"] * (pl.col("dc_agi") - floor) / band))
            .otherwise(0.0)
        )
        df = df.with_columns(dc_exemp=pl.col("dc_exemp") * (1.0 - ratio))

    df = df.with_columns(dc_taxinc=(pl.col("dc_agi") - pl.col("dc_deduc") - pl.col("dc_exemp")).clip(0, None))

    # --- Tax ---
    brackets = p.value("brackets")
    df = df.with_columns(dc_statax=bracket_tax(pl.col("dc_taxinc"), brackets))
    rate_expr = bracket_rate(pl.col("dc_taxinc"), brackets)

    # --- Joint returns: the lower of the joint tax and the tax on each earner's share ---
    is_joint_relief = (files_joint()) & (pl.col("dc_agi") > 0)
    df = df.with_columns(
        dc_agi_higher_earner=higher_earner_share(pl.col("dc_agi")),
    )
    df = df.with_columns(
        dc_agi_lower_earner=pl.col("dc_agi") - pl.col("dc_agi_higher_earner"),
        dc_exemph=pl.col("dc_exemp") * pl.col("dc_agi_higher_earner") / pl.col("dc_agi").clip(1e-9, None),
    )
    df = df.with_columns(dc_exempw=pl.col("dc_exemp") - pl.col("dc_exemph"))
    prefers_std = pl.col("dc_stded") >= pl.col("dc_xitded")
    dedh_base = pl.when(prefers_std).then(pl.col("dc_stded")).otherwise(pl.col("dc_xitded"))
    df = df.with_columns(dc_dedh=dedh_base * pl.col("dc_agi_higher_earner") / pl.col("dc_agi").clip(1e-9, None))
    df = df.with_columns(dc_dedw=dedh_base - pl.col("dc_dedh"))
    df = df.with_columns(
        dc_taxyh=(pl.col("dc_agi_higher_earner") - pl.col("dc_dedh") - pl.col("dc_exemph")).clip(0, None),
        dc_taxyw=(pl.col("dc_agi_lower_earner") - pl.col("dc_dedw") - pl.col("dc_exempw")).clip(0, None),
    )
    split_brackets = p.value("earner_split_brackets") if p.in_effect("earner_split_brackets") else brackets
    dc_taxbc = bracket_tax(pl.col("dc_taxyh"), split_brackets) + bracket_tax(pl.col("dc_taxyw"), split_brackets)
    df = df.with_columns(
        dc_statax=pl.when(is_joint_relief).then(pl.min_horizontal(pl.col("dc_statax"), dc_taxbc)).otherwise(pl.col("dc_statax"))
    )

    # --- Credits ---
    # Child and dependent care credit: a share of the federal credit before
    # its liability limit, which TAXSIM leaves at 0 before 1987.
    if p.in_effect("child_care_credit_federal_share"):
        df = df.with_columns(dc_chcr=p.num("child_care_credit_federal_share") * pl.col("ccc_uncapped"))
    else:
        df = df.with_columns(dc_chcr=pl.lit(0.0))

    df = df.with_columns(dc_credit=pl.col("dc_chcr"))
    df = df.with_columns(dc_statax=(pl.col("dc_statax") - pl.col("dc_credit")).clip(0, None))

    # Property Tax Credit ("Schedule H"): property tax, or a share of rent
    # if larger, less a share of income; separate schedules for returns
    # with a taxpayer 65 or older. Dependent filers under 65 get none.
    aged = aged_count()
    hy = pl.col("dc_household_income")
    rent_share = p.num("property_credit_rent_share")
    ptax = pl.max_horizontal(rent_share * pl.col("rentpaid"), pl.col("proptax"))
    if p.in_effect("property_credit_by_income"):
        c = p.value("property_credit_by_income")
        cap = float(c["cap"])
        aged_rate = c["aged_base_rate"] + hy / c["aged_rate_income"] / 100.0
        aged_pcred = (ptax - hy * aged_rate).clip(0, None)
        raw_pcred = (ptax - hy * (c["base_pct"] + hy / c["divisor"]) / 100.0).clip(0, None)
        young_pcred = pl.when(hy < c["low_income_limit"]).then(c["low_income_share"] * raw_pcred).otherwise(
            c["share"] * raw_pcred
        )
        pcred = pl.when(aged > 0).then(aged_pcred).otherwise(young_pcred)
        pcred = pl.when(hy <= c["income_limit"]).then(pl.min_horizontal(cap, pcred)).otherwise(0.0)
    else:
        agix = pl.col("agi").clip(0, None)
        c = p["property_credit_rates"]
        if p.in_effect("property_credit_schedule"):
            recent_p = p.value("property_credit_schedule")
            # From 2022 the rent allowance is added to property tax.
            ptax = pl.col("proptax") + float(recent_p["rent_share"]) * pl.col("rentpaid")
            nonelderly_rate = tier_values(
                agix,
                [float(v) for v in recent_p["nonelderly_upper_bounds"]],
                [float(v) for v in recent_p["nonelderly_rates"]],
            )[0]
            elderly_rate = tier_values(
                agix,
                [float(v) for v in recent_p["elderly_upper_bounds"]],
                [float(v) for v in recent_p["elderly_rates"]],
            )[0]
            elderly = (
                (pl.col("page") >= int(recent_p["elderly_age"]))
                + (pl.col("sage") >= int(recent_p["elderly_age"]))
            ) > 0
            pcred = pl.min_horizontal(
                float(recent_p["max"]),
                (ptax - pl.when(elderly).then(elderly_rate).otherwise(nonelderly_rate) * agix).clip(0, None),
            )
            # Above the last income bound there is no credit (not a 0% rate).
            limit = pl.when(elderly).then(float(recent_p["elderly_upper_bounds"][-2])).otherwise(
                float(recent_p["nonelderly_upper_bounds"][-2])
            )
            pcred = pl.when(agix >= limit).then(0.0).otherwise(pcred)
        else:
            limits = p.value("property_credit_limits")
            prop_cap, pagi_val, prlim_val = (float(limits[k]) for k in ("income_limit", "aged_income_limit", "max"))
            under_prop_cap = agix <= prop_cap
            if effective_year <= 2018:
                over_25k_pcred = pl.when(under_prop_cap).then((ptax - c["rate"] * agix).clip(0, None)).otherwise(0.0)
            else:
                over_25k_pcred = (
                    pl.when(agix < c["middle_income"])
                    .then((ptax - c["rate"] * agix).clip(0, None))
                    .when(under_prop_cap)
                    .then((ptax - c["high_rate"] * agix).clip(0, None))
                    .otherwise(0.0)
                )
            young = pl.when(agix < c["low_income"]).then((ptax - c["low_rate"] * agix).clip(0, None)).otherwise(over_25k_pcred)
            aged_pcred = pl.when(agix <= pagi_val).then((ptax - c["low_rate"] * agix).clip(0, None)).otherwise(0.0)
            pcred = pl.min_horizontal(prlim_val, pl.when(aged > 0).then(aged_pcred).otherwise(young))
    pcred = pl.when(is_dependent_filer() & (aged < 1)).then(0.0).otherwise(pcred)
    df = df.with_columns(dc_pcred=pcred)

    # Low Income Credit (1987-2017), when federal tax before credits is
    # zero: by filing status and number of aged taxpayers, plus an amount
    # per dependent and aged taxpayer. Dependent filers instead get DC tax
    # on the federal standard deduction less DC's.
    if p.in_effect("low_income_credit_per_dependent"):
        joint_row = [float(v) for v in p.value("low_income_credit_joint_by_aged")]
        single_row = [float(v) for v in p.value("low_income_credit_single_by_aged")]
        hoh_row = [float(v) for v in p.value("low_income_credit_hoh_by_aged")]
        xtra = p.num("low_income_credit_per_dependent")
        is_joint = files_joint()
        dx2 = pl.when(is_joint).then(aged).otherwise(aged.clip(None, 2)).cast(pl.Int64)

        def pick(row: list[float]) -> pl.Expr:
            return pl.lit(pl.Series(row, dtype=pl.Float64)).gather(dx2.clip(0, len(row) - 1))

        base = (
            pl.when(pl.col("filing_status").is_in(["married_joint", "married_separate"])).then(pick(joint_row) / pl.col("dc_sep"))
            .when(files_single()).then(pick(single_row))
            .otherwise(pick(hoh_row))
        )
        allow = pl.col("depx") + aged
        no_tax = pl.col("tax_before_credits") <= 0
        ycr = pl.when(no_tax & ~is_dependent_filer()).then(base + xtra * allow).otherwise(0.0)
        federal_std = pl.when(pl.col("itemizes")).then(0.0).otherwise(pl.col("standard_deduction"))
        gap = federal_std - pl.col("dc_stded")
        brackets = p.value("low_income_credit_dependent_brackets")
        dependent_ycr = pl.when(
            no_tax & is_dependent_filer() & (gap > 0) & (pl.col("agi") <= federal_std)
        ).then(bracket_tax(gap.clip(0, None), brackets)).otherwise(0.0)
        ycr = ycr + dependent_ycr
        # The dependent-filer lookup overwrites the reported rate; joint
        # returns end on the higher earner's share.
        dependent_y = pl.when(files_joint()).then(
            higher_earner_share(gap)
        ).otherwise(gap)
        rate_expr = pl.when(no_tax & is_dependent_filer() & (gap > 0)).then(
            bracket_rate(dependent_y, brackets)
        ).otherwise(rate_expr)
    else:
        ycr = pl.lit(0.0)
    df = df.with_columns(dc_ycr=ycr)
    stat1 = (pl.col("dc_statax") - pl.col("dc_ycr")).clip(0, None)

    # --- Earned income credit (2000+) ---
    if not p.in_effect("eitc_federal_share"):
        earncr = pl.lit(0.0)
    elif not p.in_effect("eitc_childless"):
        earncr = p.num("eitc_federal_share") * pl.col("eitc").clip(0, None)
    else:
        earncr_with_kids = p.num("eitc_federal_share") * pl.col("eitc").clip(0, None)
        childless = p.value("eitc_childless")
        cr, am, ym = float(childless["max_credit"]), float(childless["max_earned"]), float(childless["phaseout_start"])
        dylim = float(childless["disqualified_income_limit"])
        phase_rate = float(childless["phase_in_rate"])
        earned = pl.col("earned_income")
        agimax = pl.max_horizontal(earned, pl.col("agi"))
        earncr_childless_raw = pl.when(agimax <= am).then(
            pl.min_horizontal(cr, phase_rate * earned) - p["eitc_childless_phaseout_rate"] * (agimax - ym).clip(0, None)
        ).otherwise(0.0)
        # Investment income: interest and dividends (and capital gains, D-40
        # EITC checklist).
        disqy = pl.col("dividends").clip(0, None) + pl.col("intrec")
        if behavior.count_capital_gains_in_dc_childless_eitc_investment_income:
            disqy = disqy + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        no_childless_credit = (disqy > dylim) | (pl.col("dc_sep") == 2) | is_dependent_filer()
        if effective_year != 2021:
            no_childless_credit = no_childless_credit | (aged >= taxpayer_count())
        # Childless filers under 25 (or over 65 with a spouse under 25) did
        # not qualify before 2021. ARPA lowered the 2021 minimum to 19 and
        # removed the upper-age restriction for that year.
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        younger = pl.min_horizontal(pl.col("page"), pl.col("sage"))
        min_age, max_age = p["eitc_childless_age_limits"]["minimum_age"], p["eitc_childless_age_limits"]["maximum_age"]
        if effective_year == 2021:
            too_young = ((older > 0) & (older < 19)) | ((younger > 0) & (younger < 19))
        else:
            too_young = ((older > 0) & (older < min_age)) | ((older > max_age) & (younger > 0) & (younger < min_age))
        no_childless_credit = no_childless_credit | too_young
        earncr_childless = pl.when(no_childless_credit).then(0.0).otherwise(earncr_childless_raw)
        # DC-002: TAXSIM treats any dependent as a qualifying child.
        has_qualifying_child = (
            pl.col("dep18") > 0
            if behavior.use_age_qualified_children_for_dc_eitc
            else pl.col("depx") > 0
        )
        earncr = pl.when(has_qualifying_child).then(earncr_with_kids).otherwise(earncr_childless)
    df = df.with_columns(dc_earncr=earncr)
    stat2 = pl.col("dc_statax") - pl.col("dc_earncr")

    # Taxpayer may not claim both the Low Income Credit and the EITC -
    # whichever leaves a smaller final tax wins.
    df = df.with_columns(
        dc_statax=pl.min_horizontal(stat1, stat2),
        dc_credit=pl.col("dc_credit") + pl.when(stat1 < stat2).then(pl.col("dc_earncr")).otherwise(pl.col("dc_ycr")) + pl.col("dc_pcred"),
    )
    df = df.with_columns(dc_statax=(pl.col("dc_statax") - pl.col("dc_pcred")))

    df = df.with_columns(siitax=pl.col("dc_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("dc_agi"),
        exemptions=pl.col("dc_exemp"),
        standard_deduction=pl.col("dc_stded"),
        itemized_deductions=pl.col("dc_xitded"),
        taxable_income=pl.col("dc_taxinc"),
        property_credit=pl.col("dc_pcred"),
        child_care_credit=pl.col("dc_chcr"),
        eic=pl.col("dc_earncr"),
        credits=pl.col("dc_credit"),
        rate=rate_expr,
    )
