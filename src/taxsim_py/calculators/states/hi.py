"""Hawaii individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import federal_capital_gain_in_agi, forced_standard, interpolate_table, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

HI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "hi" / "income_tax.yaml")

def compute_hi_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    state_year = "hi" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(HI_PARAMS, effective_year)

    df = df.with_columns(
        hi_sep=separate_divisor(),
        hi_taxpayers=taxpayer_count(),
    )

    df = deflate_for_extrapolation(df, flate)

    # --- AGI ---
    # Social Security benefits are exempt.
    df = df.with_columns(hi_agi=pl.col("agi") - pl.col("taxable_social_security"))
    # 2020/2021: unemployment compensation federal excluded is taxable.
    if effective_year in (2020, 2021):
        ui_total = unemployment_total()
        df = df.with_columns(
            hi_agi=pl.col("hi_agi") + ui_total - pl.col("taxable_unemployment")
        )
    # TAXSIM's pension subtraction reads `data(72)`, a slot the input reader
    # never sets (pensions are `data(20)`), so pensions are not excluded.
    # Statutory mode applies the exclusion TAXSIM intended: Hawaii exempts
    # pensions, treating the single `pensions` input as the exempt kind.
    if behavior.mode.value == "statutory" and behavior.exclude_hawaii_pensions:
        df = df.with_columns(hi_agi=pl.col("hi_agi") - pl.col("pensions").clip(0, None))

    # --- Exemptions ---
    xmp = p.num("personal_exemption_amount")
    df = df.with_columns(hi_exemp=(pl.col("hi_taxpayers") + pl.col("depx") + aged_count()) * xmp)
    df = df.with_columns(hi_exema=pl.col("hi_exemp"))
    if 2009 <= effective_year <= 2015:
        base = float(p["personal_exemption_phaseout_base"][2009])
        step = float(p["personal_exemption_phaseout_step"][2009])
        phex = (
            pl.when(files_single()).then(base)
            .when(files_head_of_household()).then(p["personal_exemption_phaseout_multiplier"]["head_of_household"] * base)
            .otherwise(p["personal_exemption_phaseout_multiplier"]["married"] * base / pl.col("hi_sep"))
        )
        ln6 = (1.0 + (pl.col("hi_agi") - phex) / (step / pl.col("hi_sep"))).floor()
        phased = pl.col("hi_exema") - pl.col("hi_exema") * p["personal_exemption_phaseout_share_per_step"] * ln6
        df = df.with_columns(hi_exemp=pl.when(pl.col("hi_agi") > phex).then(phased).otherwise(pl.col("hi_exemp")))
    # Dependent filers claim no exemptions (the disability exemption needs
    # an input TAXSIM does not take).
    df = df.with_columns(hi_exemp=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("hi_exemp")))

    # --- Standard deduction ---
    stded_single = p.num("standard_deduction_single")
    stded_joint = p.num("standard_deduction_married_joint")
    stded_sep = p.num("standard_deduction_married_separate")
    stded_hoh = p.num("standard_deduction_hoh")
    df = df.with_columns(
        hi_stded=pl.when(files_single()).then(stded_single)
        .when(files_joint()).then(stded_joint)
        .when(files_separate()).then(stded_sep)
        .otherwise(stded_hoh)
    )
    if effective_year == 1980:
        df = df.with_columns(hi_stded=pl.min_horizontal((p["standard_deduction_agi_share_1980"] * pl.col("hi_agi")).clip(0, None), pl.col("hi_stded")))
    # The 1983-1986 charitable addback (`data(58)`/`data(59)`) has no TAXSIM input.

    # --- Itemized deduction --- Before 1987 rebuilt from the inputs.
    if effective_year <= 1986:
        df = df.with_columns(
            hi_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
        df = df.with_columns(hi_xitded=pl.col("hi_raw_itemized"))
    else:
        df = df.with_columns(hi_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
        df = df.with_columns(hi_xitded=pl.col("hi_raw_itemized"))

    if effective_year >= 2011:
        salt_thr = (
            pl.when(files_head_of_household())
            .then(float(p["salt_allowance_threshold_hoh"][2011]))
            .when((files_single()) | (pl.col("hi_sep") == 2))
            .then(float(p["salt_allowance_threshold_single_or_separate"][2011]))
            .otherwise(float(p["salt_allowance_threshold_joint"][2011]))
        )
        sttax = pl.col("state_sales_or_income_tax_ded")
        df = df.with_columns(
            hi_xitded=pl.when(pl.col("agi") > salt_thr).then((pl.col("hi_xitded") - sttax).clip(0, None)).otherwise(pl.col("hi_xitded"))
        )
    # The 2017+ medical expense adjustment has no TAXSIM input.

    if 1991 <= effective_year <= 2010:
        threshold = float(p["itemized_phaseout_threshold_1991_2010"][1991]) / pl.col("hi_sep")
        reduce1 = pl.when(pl.col("hi_agi") > threshold).then(
            pl.min_horizontal(
                p["itemized_phaseout_cap_rate"] * pl.col("hi_xitded"),
                p["itemized_phaseout_rate"] * (pl.col("hi_agi") - threshold),
            )
        ).otherwise(0.0)
        reduce1 = reduce1 * p.num("itemized_phaseout_share")
        df = df.with_columns(hi_xitded=pl.col("hi_xitded") - reduce1)

    if effective_year >= 2011:
        threshold2 = float(p["itemized_phaseout_threshold_2011plus"][2011]) / pl.col("hi_sep")
        reduce2 = pl.when(pl.col("hi_agi") > threshold2).then(
            pl.min_horizontal(
                p["itemized_phaseout_cap_rate"] * pl.col("hi_xitded"),
                p["itemized_phaseout_rate"] * (pl.col("hi_agi") - threshold2),
            )
        ).otherwise(0.0)
        df = df.with_columns(hi_xitded=pl.col("hi_xitded") - reduce2)

    if 2011 <= effective_year <= 2015:
        phas92_hoh = float(p["itemized_cap_2011_2015_threshold_hoh"][2011])
        phas92_other = float(p["itemized_cap_2011_2015_threshold_other_base"][2011]) * pl.col("hi_taxpayers")
        phas92 = pl.when(files_head_of_household()).then(phas92_hoh).otherwise(phas92_other)
        cap_hoh = float(p["itemized_cap_2011_2015_amount_hoh"][2011])
        cap_other = float(p["itemized_cap_2011_2015_amount_other_base"][2011]) * pl.col("hi_taxpayers")
        over_thr = pl.col("agi") > phas92
        df = df.with_columns(
            hi_xitded=pl.when(over_thr & (files_head_of_household()))
            .then(pl.min_horizontal(cap_hoh, pl.col("hi_xitded")))
            .when(over_thr)
            .then(pl.min_horizontal(cap_other, pl.col("hi_xitded")))
            .otherwise(pl.col("hi_xitded"))
        )

    if 1982 <= effective_year <= 1986:
        std_era = pl.when(files_single()).then(stded_single).when(
            files_joint()
        ).then(stded_joint).when(files_separate()).then(stded_sep).otherwise(stded_hoh)
        df = df.with_columns(
            hi_xitded=(pl.col("hi_raw_itemized") - std_era).clip(0, None),
            hi_stded=(pl.col("hi_stded") - std_era).clip(0, None),
        )

    if effective_year == 1999:
        df = df.with_columns(hi_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("hi_xitded")))

    df = df.with_columns(hi_deduc=pl.max_horizontal(pl.col("hi_xitded"), pl.col("hi_stded")))
    # The 2020-2021 charitable deduction (`data(58)`) has no TAXSIM input.

    df = df.with_columns(hi_taxinc=(pl.col("hi_agi") - pl.col("hi_exemp") - pl.col("hi_deduc")).clip(0, None))

    # --- Bracket tax --- Head of household has its own table; joint
    # returns use the shared table on half their income, doubled.
    year_table_map = [
        ((1977, 1986), "pre1987"),
        ((1987, 1987), "1987"),
        ((1988, 1988), "1988"),
        ((1989, 1998), "1989_1998"),
        ((1999, 2000), "1999_2000"),
        ((2001, 2001), "2001"),
        ((2002, 2006), "2002_2006"),
        ((2007, 2008), "2007_2008"),
        ((2009, 2015), "2009_2015"),
        ((2016, 2017), "2016_2017"),
    ]
    key = "2018plus"
    if behavior.mode.value == "statutory" and effective_year >= 2022:
        key = "2022plus"
    for (lo, hi), k in year_table_map:
        if lo <= effective_year <= hi:
            key = k
            break
    is_hoh = files_head_of_household()
    is_joint = files_joint()

    def _bracket_stat(taxinc_col: str) -> pl.Expr:
        brackets_single = p[f"brackets_single_{key}"]
        brackets_hoh = p[f"brackets_hoh_{key}"]
        taxy = pl.when(is_joint).then(pl.col(taxinc_col) / 2).otherwise(pl.col(taxinc_col))
        stat_single = bracket_tax(taxy, brackets_single)
        stat_single = pl.when(is_joint).then(stat_single * 2).otherwise(stat_single)
        stat_hoh = bracket_tax(pl.col(taxinc_col), brackets_hoh)
        return pl.when(is_hoh).then(stat_hoh).otherwise(stat_single)

    def _bracket_rate(taxinc_col: str) -> pl.Expr:
        brackets_single = p[f"brackets_single_{key}"]
        brackets_hoh = p[f"brackets_hoh_{key}"]
        taxy = pl.when(is_joint).then(pl.col(taxinc_col) / 2).otherwise(pl.col(taxinc_col))
        return pl.when(is_hoh).then(
            bracket_rate(pl.col(taxinc_col), brackets_hoh)
        ).otherwise(bracket_rate(taxy, brackets_single))

    df = df.with_columns(hi_statax=_bracket_stat("hi_taxinc"))
    rate_expr = _bracket_rate("hi_taxinc")

    # --- Capital gains alternative tax --- `comnew(6)` is capital gains in
    # federal AGI: net of the federal long-term exclusion through 1986.
    # The exclusion applies to the smaller of raw `ltcg` or the net gain
    # after any short-term loss (`min(ltcg, stcg+ltcg)`), not to raw
    # `ltcg` outright - the port previously recomputed a cruder figure
    # that applied the exclusion to the full `ltcg` regardless of an
    # offsetting short-term loss. It happens to match `comnew(6)` for a
    # pure gain or a pure loss, so this only showed up when a long-term
    # gain and a short-term loss coincided (a $12,000 long-term gain with
    # a $4,000 short-term loss was off by $81) - confirmed against the
    # real oracle, which already nets correctly, so this applies
    # unconditionally rather than being calculation_mode-gated.
    capgn = pl.col("pre1987_capgn") if effective_year <= 1986 else federal_capital_gain_in_agi(year, flate)
    has_gain = capgn.clip(0, None) > 0
    brack = pl.when(is_hoh).then(float(p["capital_gains_floor_hoh"])).otherwise(
        p["capital_gains_floor_per_taxpayer"] * pl.col("hi_taxpayers")
    )
    taxyng = (pl.col("hi_taxinc") - capgn.clip(0, None)).clip(0, None)
    taxyng = pl.max_horizontal(brack, taxyng)
    taxycg = (pl.col("hi_taxinc") - taxyng).clip(0, None)
    df = df.with_columns(hi_taxyng=taxyng, hi_taxycg=taxycg)
    statng = _bracket_stat("hi_taxyng")
    statcg = pl.col("hi_taxycg") * p["capital_gains_rate"]
    df = df.with_columns(
        hi_statax=pl.when(has_gain).then(pl.min_horizontal(pl.col("hi_statax"), statng + statcg)).otherwise(pl.col("hi_statax"))
    )
    rate_expr = pl.when(has_gain).then(_bracket_rate("hi_taxyng")).otherwise(rate_expr)

    # --- Credits ---
    # Child/Dependent Care Credit.
    cap_per_dep = p.num("child_care_credit_cap_per_dependent")
    max_deps = float(p["child_care_credit_max_dependents"][1960])
    child = pl.min_horizontal(pl.col("childcare"), cap_per_dep * pl.min_horizontal(pl.col("depx"), max_deps))
    child = pl.when(is_joint).then(pl.min_horizontal(child, pl.col("pwages"), pl.col("swages"))).otherwise(child)
    c = p.value("child_care_credit_rate")
    chr_rate = 0.01 * pl.max_horizontal(
        float(c["min_pct"]), c["max_pct"] - pl.max_horizontal((pl.col("hi_agi") - c["start"]) / c["step"], 0.0)
    )
    df = df.with_columns(hi_chcr=chr_rate * child)

    # General Income Tax Credit - not available 1996+ (except the flat
    # $1/exemption revival 2001-2006/2008-2009, and 2007's own formula).
    if effective_year <= 1995:
        amt = p.num("general_credit_amount_by_year_pre1996")
        df = df.with_columns(hi_gencr=amt * (pl.col("hi_taxpayers") + pl.col("depx")))
    elif (2001 <= effective_year <= 2006) or (2008 <= effective_year <= 2009):
        amt = float(p["general_credit_flat_2001_2009"][2001])
        df = df.with_columns(hi_gencr=amt * (pl.col("hi_taxpayers") + pl.col("depx")))
    elif effective_year == 2007:
        def general_credit(c: dict) -> pl.Expr:
            return pl.when(pl.col("agi") < c["limit"]).then(c["max"] - c["slope"] * pl.col("agi") / c["limit"]).otherwise(0.0)

        g = p["general_credit_2007"]
        df = df.with_columns(
            hi_gencr=pl.when(is_hoh).then(general_credit(g["head_of_household"]))
            .when(is_joint).then(general_credit(g["married_joint"]))
            .otherwise(general_credit(g["other"]))
        )
    else:
        df = df.with_columns(hi_gencr=pl.lit(0.0))

    # Renter's Credit.
    aged_factor = aged_count().clip(1, None)
    rent_limit = p.num("renter_credit_agi_limit")
    rcred = pl.when((pl.col("rentpaid") >= p["renter_credit_min_rent"]) & (pl.col("hi_agi") < rent_limit)).then(
        p.num("renter_credit_per_exemption") * (pl.col("hi_taxpayers") + pl.col("depx") + aged_count())
    ).otherwise(0.0)
    df = df.with_columns(hi_rcred=rcred * aged_factor)

    # Excise Tax Credit (repealed after 1994) / Refundable Food-Excise
    # Tax Credit (2008+, replaces it) - both real, `tablki`-based.
    if effective_year <= 1979:
        df = df.with_columns(hi_exc=interpolate_table(pl.col("hi_agi"), p["excise_credit_table_1977_1979"]) * pl.col("hi_taxpayers") * aged_factor)
    elif effective_year <= 1987:
        df = df.with_columns(hi_exc=interpolate_table(pl.col("hi_agi"), p["excise_credit_table_1980_1987"]) * pl.col("hi_taxpayers") * aged_factor)
    elif effective_year <= 1994:
        df = df.with_columns(hi_exc=interpolate_table(pl.col("hi_agi"), p["excise_credit_table_1988_1994"]) * pl.col("hi_taxpayers") * aged_factor)
    elif 2008 <= effective_year <= 2015:
        fedagi = pl.col("agi").clip(0, None)
        df = df.with_columns(
            hi_exc=(pl.col("depx") + pl.col("hi_taxpayers")) * interpolate_table(fedagi, p["food_excise_credit_table_2008_2015"])
        )
    elif effective_year >= 2016:
        fedagi = pl.col("agi").clip(0, None)
        food_key = "food_excise_credit_table_2016plus_non_single"
        food_single_key = "food_excise_credit_table_2016plus_single"
        if behavior.mode.value == "statutory" and effective_year >= 2023:
            food_key = "food_excise_credit_table_2023plus_non_single"
            food_single_key = "food_excise_credit_table_2023plus_single"
        exc_non_single = (pl.col("depx") + pl.col("hi_taxpayers")) * interpolate_table(fedagi, p[food_key])
        exc_single = (pl.col("depx") + pl.col("hi_taxpayers")) * interpolate_table(fedagi, p[food_single_key])
        df = df.with_columns(hi_exc=pl.when(files_single()).then(exc_single).otherwise(exc_non_single))
    else:
        df = df.with_columns(hi_exc=pl.lit(0.0))

    # Food Tax Credit (not available since 1999).
    if effective_year <= 1998:
        amt = p.num("food_credit_amount_by_year")
        df = df.with_columns(hi_foodcr=amt * (pl.col("hi_taxpayers") + pl.col("depx")))
    else:
        df = df.with_columns(hi_foodcr=pl.lit(0.0))

    # Low-Income (refundable) Credit (1999-2007 only).
    if 1999 <= effective_year <= 2007:
        t1 = float(p["low_income_credit_tier1_amount"][1960])
        t2 = float(p["low_income_credit_tier2_amount"][1960])
        t3 = float(p["low_income_credit_tier3_amount"][1960])
        c1 = float(p["low_income_credit_tier1_ceiling"][1960])
        c2 = float(p["low_income_credit_tier2_ceiling"][1960])
        c3 = float(p["low_income_credit_tier3_ceiling"][1960])
        units = pl.col("hi_taxpayers") + pl.col("depx")
        df = df.with_columns(
            hi_lowcr=pl.when(pl.col("hi_agi") < c1).then(units * t1)
            .when(pl.col("hi_agi") < c2).then(units * t2)
            .when(pl.col("hi_agi") <= c3).then(units * t3)
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(hi_lowcr=pl.lit(0.0))
    # Dependent filers get no excise or food credit.
    df = df.with_columns(
        hi_exc=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("hi_exc")),
        hi_foodcr=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("hi_foodcr")),
    )
    df = df.with_columns(
        hi_credit=pl.col("hi_gencr") + pl.col("hi_rcred") + pl.col("hi_exc") + pl.col("hi_foodcr")
        + pl.col("hi_chcr") + pl.col("hi_lowcr")
    )
    df = df.with_columns(hi_statax=pl.col("hi_statax") - pl.col("hi_credit"))

    # --- State EITC ---
    if behavior.mode.value == "statutory" and effective_year >= 2023:
        rate = float(p["eitc_rate_2023plus"][2023])
        df = df.with_columns(hi_earncr=rate * pl.col("eitc").clip(0, None))
    elif 2018 <= effective_year <= 2022:
        rate = float(p["eitc_rate_2018_2022"][2018])
        raw = rate * pl.col("eitc").clip(0, None)
        df = df.with_columns(hi_earncr=pl.min_horizontal(raw, pl.col("hi_statax").clip(0, None)))
    else:
        df = df.with_columns(hi_earncr=pl.lit(0.0))
    df = df.with_columns(hi_statax=pl.col("hi_statax") - pl.col("hi_earncr"))

    df = df.with_columns(siitax=pl.col("hi_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("hi_agi"),
        exemptions=pl.col("hi_exemp"),
        standard_deduction=pl.col("hi_stded"),
        itemized_deductions=pl.col("hi_xitded"),
        taxable_income=pl.col("hi_taxinc"),
        child_care_credit=pl.col("hi_chcr"),
        eic=pl.col("hi_earncr"),
        credits=pl.col("hi_credit") + pl.col("hi_earncr"),
        rate=rate_expr,
    )
