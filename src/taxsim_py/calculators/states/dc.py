"""District of Columbia individual income tax calculator."""

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
    state_year = "dc" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(DC_PARAMS, effective_year)
    df = df.with_columns(
        dc_sep=separate_divisor(),
        # `data(7)` - self/spouse exemption unit count (1, or 2 ONLY for
        # married_joint), used by the exemption formula.
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
            two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
            two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
            twoded = (
                two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
            ).clip(0, two_earner_cap)
            df = df.with_columns(dc_agi=pl.col("dc_agi") + twoded)
        # Social Security benefits are exempt from 1984.
        if effective_year >= 1984:
            df = df.with_columns(dc_agi=pl.col("dc_agi") - pl.col("taxable_social_security"))

    # Unemployment compensation is excluded in every year (`data(82)`).
    ui_total = unemployment_total()
    df = df.with_columns(dc_agi=pl.col("dc_agi") - ui_total)

    # --- Standard deduction ---
    if effective_year <= 1981:
        cap = float(p["standard_deduction_cap_pre1982"][1960])
        df = df.with_columns(dc_stded=(p["standard_deduction_share_pre1982"] * pl.col("dc_agi")).clip(0, cap / pl.col("dc_sep")))
    elif effective_year <= 1986:
        flat = float(p["standard_deduction_flat_1982_1986"][1982])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2005:
        flat = float(p["standard_deduction_flat_1987_2005"][1987])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2007:
        flat = float(p["standard_deduction_flat_2006_2007"][2006])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2009:
        flat = float(p["standard_deduction_flat_2008_2009"][2008])
        per_unit = float(p["standard_deduction_proptax_addback_per_exemption_unit_2008_2009"][2008])
        addback = pl.min_horizontal(pl.col("proptax"), pl.col("dc_taxpayers") * per_unit)
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep") + addback)
    elif effective_year <= 2012:
        flat = float(p["standard_deduction_flat_2010_2012"][2010])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year == 2013:
        flat = float(p["standard_deduction_flat_2013"][2013])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year == 2014:
        flat = float(p["standard_deduction_flat_2014"][2014])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2016:
        joint = float(p["standard_deduction_2015_2016_joint"][2015])
        hoh = float(p["standard_deduction_2015_2016_hoh"][2015])
        single_or_sep = float(p["standard_deduction_2015_2016_single_or_separate"][2015])
        df = df.with_columns(
            dc_stded=pl.when(files_joint())
            .then(joint)
            .when(files_head_of_household())
            .then(hoh)
            .otherwise(single_or_sep)
        )
    elif effective_year == 2017:
        joint = float(p["standard_deduction_2017_joint"][2017])
        hoh = float(p["standard_deduction_2017_hoh"][2017])
        single_or_sep = float(p["standard_deduction_2017_single_or_separate"][2017])
        df = df.with_columns(
            dc_stded=pl.when(files_joint())
            .then(joint)
            .when(files_head_of_household())
            .then(hoh)
            .otherwise(single_or_sep)
        )
    else:
        # Federal zero bracket (`zbrack(nfile,law)/sep`); separate returns
        # use the joint amount halved.
        single_sd = float(resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"]["single"], effective_year))
        joint_sd = float(resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"]["married_joint"], effective_year))
        hoh_sd = float(resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"]["head_of_household"], effective_year))
        if effective_year >= 2025 and behavior.mode.value == "statutory":
            nonconforming = resolve_year(p["standard_deduction_nonconforming"], effective_year)
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
    # allowed itemized totals (`comnew(30)`, `comnew(24)`) are equal, so
    # they are rebuilt from the inputs.
    if effective_year <= 1986:
        df = df.with_columns(
            dc_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
    else:
        df = df.with_columns(dc_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
    # DC itemizes exactly when the federal return does
    # (`comnew(26).gt.0.and.comnew(30).gt.0`). Before 1987 that choice is
    # rebuilt from the federal zero bracket amount.
    if effective_year <= 1986:
        # Federal itemized deductions (`comnew(24)`), zero when the federal
        # return does not itemize.
        itemizing_gate = pl.col("pre1987_itemizes")
        df = df.with_columns(dc_raw_itemized=pl.col("pre1987_deduc"))
    else:
        itemizing_gate = pl.col("itemizes")

    if effective_year <= 1981:
        # `xitded=comnew(24)-max(comnew(23)-.15*agi,0)`; `comnew(23)` is 0
        # for these inputs.
        df = df.with_columns(dc_xitded=pl.col("dc_raw_itemized"))
    elif 1982 <= effective_year <= 2017:
        salt_share = (pl.col("state_sales_or_income_tax_ded") / pl.col("dc_raw_itemized").clip(1e-9, None))
        df = df.with_columns(
            dc_xitded=(pl.col("itemized_deduction") * (1.0 - salt_share)).clip(0, None)
        )
    if 1982 <= effective_year <= 1986:
        # Without a federal limitation, `comnew(24)` less the state tax
        # deduction is the itemized inputs.
        df = df.with_columns(
            dc_xitded=(pl.col("dc_raw_itemized") - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        )
    if effective_year >= 2018:
        sttax_cap = p["state_tax_deduction_cap_2018plus"] / pl.col("dc_sep")
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

    # In every era, itemized deductions fall by 5% of AGI above
    # $200,000 ($100,000 separate).
    threshold = float(p["itemized_agi_reduction_threshold"][1960]) / pl.col("dc_sep")
    excess = (pl.col("dc_agi") - threshold).clip(0, None)
    df = df.with_columns(dc_xitded=(pl.col("dc_xitded") - p["itemized_agi_reduction_rate"] * excess).clip(0, None))

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
    amnt = p.num("personal_exemption_amount")
    df = df.with_columns(dc_exemp=pl.col("dc_num") * amnt)
    df = df.with_columns(
        dc_exemp=pl.when(files_head_of_household())
        .then(pl.col("dc_exemp") + amnt)
        .otherwise(pl.col("dc_exemp"))
    )
    df = df.with_columns(dc_exemp=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("dc_exemp")))
    if 2015 <= effective_year <= 2017:
        floor = float(p["exemption_phaseout_2015_2017_floor"][2015])
        ceiling = float(p["exemption_phaseout_2015_2017_ceiling"][2015])
        band = float(p["exemption_phaseout_2015_2017_band_per_sep"][2015]) / pl.col("dc_sep")
        ratio = (
            pl.when(pl.col("dc_agi") > ceiling)
            .then(1.0)
            .when(pl.col("dc_agi") > floor)
            .then(pl.min_horizontal(1.0, p["exemption_phaseout_2015_2017_share_per_band"] * (pl.col("dc_agi") - floor) / band))
            .otherwise(0.0)
        )
        df = df.with_columns(dc_exemp=pl.col("dc_exemp") * (1.0 - ratio))

    df = df.with_columns(dc_taxinc=(pl.col("dc_agi") - pl.col("dc_deduc") - pl.col("dc_exemp")).clip(0, None))

    # --- Bracket tax ---
    year_table_map = {
        (1977, 1986): "brackets_pre1987",
        (1987, 1987): "brackets_1987",
        (1988, 1999): "brackets_1988_1999",
        (2000, 2000): "brackets_2000",
        (2001, 2004): "brackets_2001_2004",
        (2005, 2005): "brackets_2005",
        (2006, 2006): "brackets_2006",
        (2007, 2011): "brackets_2007_2011",
        (2012, 2014): "brackets_2012_2014",
        (2015, 2015): "brackets_2015",
    }
    key = "brackets_2022plus" if effective_year >= 2022 else "brackets_2016plus"
    for (lo, hi), k in year_table_map.items():
        if lo <= effective_year <= hi:
            key = k
            break
    brackets = p[key]
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
    split_brackets = p["brackets_2007_split"] if effective_year == 2007 else brackets
    dc_taxbc = bracket_tax(pl.col("dc_taxyh"), split_brackets) + bracket_tax(pl.col("dc_taxyw"), split_brackets)
    df = df.with_columns(
        dc_statax=pl.when(is_joint_relief).then(pl.min_horizontal(pl.col("dc_statax"), dc_taxbc)).otherwise(pl.col("dc_statax"))
    )

    # --- Credits ---
    # Child/Dependent Care Credit (1982+): a share of the federal credit
    # before its liability limit (`comnew(176)`), which TAXSIM leaves at 0
    # before 1987.
    if effective_year <= 1981:
        df = df.with_columns(dc_chcr=pl.lit(0.0))
    elif effective_year <= 1988:
        rate = float(p["child_care_credit_rate_1982_1988"][1982])
        df = df.with_columns(dc_chcr=rate * pl.col("ccc_uncapped"))
    else:
        rate = float(p["child_care_credit_rate_1989plus"][1989])
        df = df.with_columns(dc_chcr=rate * pl.col("ccc_uncapped"))

    # The campaign contribution credit has no TAXSIM input.
    df = df.with_columns(dc_polcr=pl.lit(0.0))
    df = df.with_columns(dc_credit=pl.col("dc_chcr") + pl.col("dc_polcr"))
    df = df.with_columns(dc_statax=(pl.col("dc_statax") - pl.col("dc_credit")).clip(0, None))

    # Property Tax Credit ("Schedule H"): property tax, or a share of rent
    # if larger, less a share of income; separate schedules for returns
    # with a taxpayer 65 or older. Dependent filers under 65 get none.
    aged = aged_count()
    hy = pl.col("dc_household_income")
    rent_share = p.num("property_credit_rent_share")
    ptax = pl.max_horizontal(rent_share * pl.col("rentpaid"), pl.col("proptax"))
    if effective_year <= 2013:
        cap = float(p["property_credit_cap_pre2014"][1960])
        aged_rate = p["property_credit_aged_base_rate"] + hy / p["property_credit_aged_rate_income"] / 100.0
        aged_pcred = (ptax - hy * aged_rate).clip(0, None)
        c = p["property_credit_nonaged_pre2014"]
        raw_pcred = (ptax - hy * (c["base_pct"] + hy / c["divisor"]) / 100.0).clip(0, None)
        young_pcred = pl.when(hy < c["low_income_limit"]).then(c["low_income_share"] * raw_pcred).otherwise(
            c["share"] * raw_pcred
        )
        pcred = pl.when(aged > 0).then(aged_pcred).otherwise(young_pcred)
        pcred = pl.when(hy <= c["income_limit"]).then(pl.min_horizontal(cap, pcred)).otherwise(0.0)
    else:
        agix = pl.col("agi").clip(0, None)
        c = p["property_credit_2014plus"]
        recent = behavior.mode.value == "statutory" and effective_year >= 2022
        if recent:
            recent_p = p.value("property_credit_recent")
            # Recent Schedule H adds the rent allowance to property taxes;
            # the historical TAXSIM path retains its max() approximation.
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
            table_years = p["property_credit_by_year_2014plus"]
            lookup_year = effective_year if effective_year in table_years else max(y for y in table_years if y <= effective_year)
            prop_cap, pagi_val, prlim_val = (float(v) for v in table_years[lookup_year])
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
    if 1987 <= effective_year <= 2017:
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

    # --- EITC (2000+) ---
    if effective_year < 2000:
        earncr = pl.lit(0.0)
    elif effective_year <= 2014:
        rate = float(resolve_year(
            {**p["eitc_rate_2000"], **p["eitc_rate_2001_2004"], **p["eitc_rate_2005_2007"], **p["eitc_rate_2008plus"]},
            effective_year,
        ))
        earncr = rate * pl.col("eitc").clip(0, None)
    else:
        rate = p.num("eitc_rate_2008plus")
        earncr_with_kids = rate * pl.col("eitc").clip(0, None)
        cr = p.num("eitc_childless_max_credit_by_year")
        am = p.num("eitc_childless_max_earned_by_year")
        ym = p.num("eitc_childless_phaseout_start_by_year")
        dylim = p.num("eitc_disqualified_income_limit_by_year")
        earned = pl.col("earned_income")
        agimax = pl.max_horizontal(earned, pl.col("agi"))
        childless = p["eitc_childless"]
        phase_rate = float(resolve_year(childless["phase_in_rate"], effective_year))
        earncr_childless_raw = pl.when(agimax <= am).then(
            pl.min_horizontal(cr, phase_rate * earned) - childless["phaseout_rate"] * (agimax - ym).clip(0, None)
        ).otherwise(0.0)
        # `disqy` (`comnew(159)`) is dividends and interest only.
        disqy = pl.col("dividends").clip(0, None) + pl.col("intrec")
        no_childless_credit = (disqy > dylim) | (pl.col("dc_sep") == 2) | is_dependent_filer()
        if effective_year != 2021:
            no_childless_credit = no_childless_credit | (aged >= taxpayer_count())
        # Childless filers under 25 (or over 65 with a spouse under 25) did
        # not qualify before 2021. ARPA lowered the 2021 minimum to 19 and
        # removed the upper-age restriction for that year.
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        younger = pl.min_horizontal(pl.col("page"), pl.col("sage"))
        min_age, max_age = childless["minimum_age"], childless["maximum_age"]
        if effective_year == 2021:
            too_young = ((older > 0) & (older < 19)) | ((younger > 0) & (younger < 19))
        else:
            too_young = ((older > 0) & (older < min_age)) | ((older > max_age) & (younger > 0) & (younger < min_age))
        no_childless_credit = no_childless_credit | too_young
        earncr_childless = pl.when(no_childless_credit).then(0.0).otherwise(earncr_childless_raw)
        # A dependent is not automatically a qualifying EITC child. Statutory
        # mode uses the age-qualified count; compatibility mode preserves the
        # historical TAXSIM `depx` switch.
        has_qualifying_child = (
            pl.col("dep18") > 0
            if behavior.mode.value == "statutory"
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
