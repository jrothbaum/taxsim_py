"""Idaho individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, forced_standard, household_income, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

ID_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "id" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
_SEPRET_BY_STATUS = {"single": 1.0, "married_joint": 1.0, "head_of_household": 1.0, "married_separate": 2.0}

def compute_id_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    state_year = "id" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(ID_PARAMS, effective_year)

    df = df.with_columns(
        id_sep=separate_divisor(),
        id_taxpayers=taxpayer_count(),
        id_household_income=household_income(),
    )
    df = deflate_for_extrapolation(df, flate, extra=("id_household_income",))

    # --- AGI --- 1977 uses federal AGI unadjusted (`if(law.ge.1978)`).
    df = df.with_columns(id_agi=pl.col("agi"))
    if effective_year >= 1978:
        cc_cap = p.num("child_care_deduction_per_dependent") * pl.min_horizontal(pl.col("depx"), 2.0)
        cc_ded = pl.min_horizontal(pl.col("childcare"), pl.col("earned_income"), cc_cap)
        df = df.with_columns(id_agi=pl.col("id_agi") - cc_ded)
        if effective_year >= 1984:
            df = df.with_columns(id_agi=pl.col("id_agi") - pl.col("taxable_social_security"))

    if effective_year == 2020:
        ui_total = unemployment_total()
        df = df.with_columns(
            id_agi=pl.col("id_agi") + ui_total - pl.col("taxable_unemployment")
        )

    # --- Itemized deduction --- `comnew(24)*(1-data(50)/comnew(30))`:
    # federal itemized deductions less the state tax share. Before 1987
    # they are rebuilt from the inputs.
    if effective_year <= 1986:
        df = df.with_columns(
            id_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
        id_itemized_deduction = pl.col("id_raw_itemized")
    else:
        df = df.with_columns(id_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
        id_itemized_deduction = pl.col("itemized_deduction")
    df = df.with_columns(id_xitded=(pl.col("id_raw_itemized") - pl.col("state_sales_or_income_tax_ded")).clip(0, None))

    if 1991 <= effective_year <= 2017:
        base = float(p["itemized_phaseout_base"][1960])
        if effective_year <= 2012:
            aif92 = p.num("itemized_phaseout_aif92_1992_2012") if effective_year >= 1992 else 1.0
            phas92 = base * aif92 / pl.col("id_sep")
        else:
            aif13 = p.num("itemized_phaseout_aif13_2013_2017")
            mult = p["itemized_phaseout_2013_2017_multiplier"]
            mult_expr = by_filing_status(mult)
            phas92 = aif13 * p["itemized_phaseout_base_multiple_2013_2017"] * base * mult_expr
        salt_share = pl.col("state_sales_or_income_tax_ded") / pl.col("id_raw_itemized").clip(1e-9, None)
        over_thr = (pl.col("id_agi") > phas92) & (pl.col("id_raw_itemized") > 0)
        df = df.with_columns(
            id_xitded=pl.when(over_thr)
            .then((id_itemized_deduction * (1.0 - salt_share)).clip(0, None))
            .otherwise(pl.col("id_xitded"))
        )

    # --- Standard deduction ---
    if effective_year == 1977:
        floor_s = float(p["standard_deduction_1977_single_or_hoh_floor"][1960])
        ceiling_s = float(p["standard_deduction_1977_single_or_hoh_ceiling"][1960])
        floor_m = float(p["standard_deduction_1977_married_floor"][1960])
        ceiling_m = float(p["standard_deduction_1977_married_ceiling"][1960])
        is_single_or_hoh = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded_s = (p["standard_deduction_agi_share_1977"] * pl.col("id_agi")).clip(floor_s, ceiling_s)
        stded_m = (p["standard_deduction_agi_share_1977"] * pl.col("id_agi")).clip(floor_m, ceiling_m)
        df = df.with_columns(id_stded=pl.when(is_single_or_hoh).then(stded_s).otherwise(stded_m))
    elif effective_year <= 1986:
        # `stded=comnew(3)`: through 1986 the federal zero bracket amount.
        zbr_expr = by_filing_status(
            {status: resolve_year(PRE1987_PARAMS["standard_deduction"][status], effective_year) for status in _STATUSES}
        )
        df = df.with_columns(id_stded=zbr_expr)
    elif effective_year <= 1992:
        # `stded=comnew(3)`: for 1987-1992 the federal standard deduction,
        # which TAXSIM leaves at 0 when the federal return itemizes. In 1990
        # separate returns add $25 (`-150.*nblage+25.`).
        df = df.with_columns(
            id_stded=pl.when(pl.col("itemizes")).then(0.0).otherwise(pl.col("standard_deduction"))
        )
        if effective_year == 1990:
            df = df.with_columns(
                id_stded=pl.when(files_separate())
                .then(pl.col("id_stded") + p["standard_deduction_separate_addition_1990"])
                .otherwise(pl.col("id_stded"))
            )
    elif effective_year <= 1998:
        s = p.num("standard_deduction_single_1993plus")
        h = p.num("standard_deduction_hoh_1993plus")
        j = p.num("standard_deduction_married_joint_1993plus")
        df = df.with_columns(
            id_stded=pl.when(files_single()).then(s)
            .when(files_head_of_household()).then(h)
            .otherwise(j / pl.col("id_sep"))
        )
    else:
        # From 1999 separate returns use their own table (`dedw`), not
        # half the joint amount.
        s = p.num("standard_deduction_single_1993plus")
        h = p.num("standard_deduction_hoh_1993plus")
        j = p.num("standard_deduction_married_joint_1993plus")
        w = p.num("standard_deduction_married_separate_1999plus")
        df = df.with_columns(
            id_stded=pl.when(files_single()).then(s)
            .when(files_head_of_household()).then(h)
            .when(files_joint()).then(j)
            .otherwise(w / pl.col("id_sep"))
        )
        if 2008 <= effective_year <= 2009:
            cap_per_exemption = float(p["standard_deduction_proptax_addback_cap_per_exemption_2008_2009"][1960])
            addback = pl.min_horizontal(cap_per_exemption * pl.col("id_taxpayers"), pl.col("proptax"))
            df = df.with_columns(id_stded=pl.col("id_stded") + addback)

    df = df.with_columns(id_xitded_detail=pl.col("id_xitded"))
    if effective_year == 1999:
        df = df.with_columns(id_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("id_xitded")))

    # Taxpayers 65 or older, and dependent filers.
    aged = aged_count()
    sep_status = files_separate()
    if 1988 <= effective_year <= 1992:
        reduction = p.num("separate_aged_reduction")
        df = df.with_columns(id_stded=pl.when(sep_status).then(pl.col("id_stded") - reduction * aged).otherwise(pl.col("id_stded")))
    if effective_year == 1987:
        t = p["aged_standard_deduction_1987"]
        aged_std = pl.lit(None, dtype=pl.Float64)
        for status, (first, step) in t.items():
            aged_std = pl.when(pl.col("filing_status") == status).then(float(first) + float(step) * (aged - 1)).otherwise(aged_std)
        df = df.with_columns(id_stded=pl.when(aged >= 1).then(aged_std).otherwise(pl.col("id_stded")))
    elif effective_year >= 1993:
        single_like = pl.col("filing_status").is_in(["single", "head_of_household"])
        aged_p = YearParams(FEDERAL_INCOME_TAX_PARAMS["aged_standard_deduction"], effective_year)
        amount = pl.when(single_like).then(aged_p.num("single")).otherwise(
            aged_p.num("married_joint")
        )
        df = df.with_columns(id_stded=pl.col("id_stded") + amount * aged)
    if effective_year >= 1987:
        minimum = p.num("dependent_standard_deduction_minimum")
        limit = pl.max_horizontal(pl.lit(minimum), pl.col("earned_income") + p["dependent_standard_deduction_earned_addition"])
        df = df.with_columns(
            id_stded=pl.when(is_dependent_filer()).then(pl.min_horizontal(pl.col("id_stded"), limit)).otherwise(pl.col("id_stded"))
        )
    df = df.with_columns(id_deduc=pl.max_horizontal(pl.col("id_xitded"), pl.col("id_stded")))

    # --- Exemption ---
    # The federal exemption amount (`comnew(83)`).
    df = df.with_columns(id_exemp=pl.col("pre1987_amex") if effective_year <= 1986 else pl.col("personal_exemptions"))

    df = df.with_columns(id_taxinc=(pl.col("id_agi") - pl.col("id_deduc") - pl.col("id_exemp")).clip(0, None))
    if effective_year >= 2018:
        df = df.with_columns(id_taxinc=(pl.col("id_taxinc") - pl.col("qbi_deduction")).clip(0, None))

    # --- Bracket tax ---
    year_table_map = [
        ((1977, 1986), "pre1987", 1.0),
        ((1987, 1999), "1987_1999", 1.0),
        ((2000, 2000), "2000", 1.0),
    ]
    key = None
    for (lo, hi), k, _aif in year_table_map:
        if lo <= effective_year <= hi:
            key = k
            break
    if key is None:
        if 2001 <= effective_year <= 2011:
            key = "2001_2011"
        elif 2012 <= effective_year <= 2017:
            key = "2012_2017"
        elif 2018 <= effective_year <= 2020:
            key = "2018_2020"
        else:
            key = "2021plus"
        aif = 1.0 if behavior.mode.value == "statutory" and effective_year >= 2022 else p.num("bracket_inflation_factor")
    else:
        aif = 1.0
    if behavior.mode.value == "statutory" and effective_year >= 2022:
        brackets = p[f"brackets_{effective_year}_statutory"]
    else:
        brackets = p[f"brackets_{key}"]
    txp = pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"])).then(2.0).otherwise(1.0)
    tinc = pl.col("id_taxinc") / txp
    stat = bracket_tax(tinc / aif, brackets) * aif
    df = df.with_columns(id_statax=stat * txp)

    # --- Credits ---
    # Nonrefundable Child Tax Credit (2018+).
    if effective_year >= 2018:
        per_child = float(p["child_tax_credit_per_child_2018plus"][1960])
        df = df.with_columns(id_ctcred=pl.min_horizontal(per_child * pl.col("dep17"), pl.col("id_statax")))
    else:
        df = df.with_columns(id_ctcred=pl.lit(0.0))

    # Grocery Tax Credit.
    depx_plus_texp = pl.col("id_taxpayers") + pl.col("depx")
    if effective_year == 1977:
        flat = float(p["grocery_credit_flat_1977"][1960])
        df = df.with_columns(id_grcred=flat * depx_plus_texp)
    elif effective_year <= 2000:
        flat = float(p["grocery_credit_flat_1978_2000"][1960])
        df = df.with_columns(id_grcred=flat * depx_plus_texp)
    elif effective_year <= 2007:
        flat = float(p["grocery_credit_flat_2001_2007"][1960])
        df = df.with_columns(id_grcred=flat * depx_plus_texp)
    else:
        amt = p.num("grocery_credit_by_year_2008plus")
        grcred = amt * depx_plus_texp
        if effective_year <= 2014:
            low_income_bonus = float(
                p["grocery_credit_low_income_bonus_2013" if effective_year <= 2013 else "grocery_credit_low_income_bonus_2014"][1960]
            ) if False else (
                float(p["grocery_credit_low_income_bonus_2008_2013"][1960]) if effective_year <= 2013
                else float(p["grocery_credit_low_income_bonus_2014"][1960])
            )
            grcred = grcred + pl.when(pl.col("id_taxinc") <= 1000.0).then(low_income_bonus * depx_plus_texp).otherwise(0.0)
        grcred = grcred + p.num("grocery_credit_aged") * aged_count()
        if 2012 <= effective_year <= 2016:
            grcred = pl.when((files_separate()) & (aged_count() < 1)).then(0.0).otherwise(grcred)
        df = df.with_columns(id_grcred=grcred)
    if effective_year <= 2007:
        df = df.with_columns(
            id_grcred=pl.col("id_grcred") + p.num("grocery_credit_aged") * aged_count()
        )
    df = df.with_columns(id_grcred=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("id_grcred")))

    df = df.with_columns(id_credit=pl.col("id_ctcred") + pl.col("id_grcred"))
    df = df.with_columns(id_statax=pl.col("id_statax") - pl.col("id_credit"))

    # --- Permanent Building Fund Tax ---
    lim_single = float(p["building_fund_limit_single_or_hoh_base"][1960]) + (
        float(p["building_fund_limit_single_or_hoh_addon_1981plus"][1960]) if effective_year >= 1981 else 0.0
    )
    lim_married = float(p["building_fund_limit_married_base"][1960]) + (
        float(p["building_fund_limit_married_addon_1981plus"][1960]) if effective_year >= 1981 else 0.0
    )
    lim_sep = float(p["building_fund_limit_separate_base"][1960]) + (
        float(p["building_fund_limit_separate_addon_1981plus"][1960]) if effective_year >= 1981 else 0.0
    )
    lim = (
        pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then(lim_single)
        .when(files_joint()).then(lim_married)
        .otherwise(lim_sep)
    )
    lim = lim + pl.when(pl.col("filing_status") != "married_separate").then(p["building_fund_limit_aged"] * aged_count()).otherwise(0.0)
    if effective_year >= 1978:
        building_fund_tax = float(p["building_fund_tax_amount"][1960])
        df = df.with_columns(
            id_statax=pl.when(pl.col("id_household_income") > lim).then(pl.col("id_statax") + building_fund_tax).otherwise(pl.col("id_statax"))
        )

    df = df.with_columns(siitax=pl.col("id_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("id_agi"),
        exemptions=pl.col("id_exemp"),
        standard_deduction=pl.col("id_stded"),
        itemized_deductions=pl.col("id_xitded_detail"),
        taxable_income=pl.col("id_taxinc"),
        credits=pl.col("id_credit"),
        rate=bracket_rate(tinc / aif, brackets),
    )
