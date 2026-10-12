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
    effective_year, flate = resolve_state_year(year, behavior, ID_PARAMS)
    p = YearParams(ID_PARAMS, effective_year)

    df = df.with_columns(
        id_sep=separate_divisor(),
        id_taxpayers=taxpayer_count(),
        id_household_income=household_income(behavior),
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

    # --- Itemized deduction --- Federal itemized deductions less the state
    # tax share. Before 1987 they are rebuilt from the inputs.
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
        threshold = p.value("itemized_limitation_threshold")
        index = p.num("itemized_limitation_index")
        if isinstance(threshold, dict):
            phas92 = by_filing_status({s: float(v) * index for s, v in threshold.items()})
        else:
            phas92 = float(threshold) * index / pl.col("id_sep")
        salt_share = pl.col("state_sales_or_income_tax_ded") / pl.col("id_raw_itemized").clip(1e-9, None)
        over_thr = (pl.col("id_agi") > phas92) & (pl.col("id_raw_itemized") > 0)
        df = df.with_columns(
            id_xitded=pl.when(over_thr)
            .then((id_itemized_deduction * (1.0 - salt_share)).clip(0, None))
            .otherwise(pl.col("id_xitded"))
        )

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        is_single_or_hoh = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded_s = (sd["rate"] * pl.col("id_agi")).clip(float(sd["single"]["floor"]), float(sd["single"]["ceiling"]))
        stded_m = (sd["rate"] * pl.col("id_agi")).clip(float(sd["married"]["floor"]), float(sd["married"]["ceiling"]))
        df = df.with_columns(id_stded=pl.when(is_single_or_hoh).then(stded_s).otherwise(stded_m))
    elif effective_year <= 1986:
        # The federal zero bracket amount.
        zbr_expr = by_filing_status(
            {status: resolve_year(PRE1987_PARAMS["standard_deduction"][status], effective_year) for status in _STATUSES}
        )
        df = df.with_columns(id_stded=zbr_expr)
    elif effective_year <= 1992:
        # The federal standard deduction, 0 when the federal return itemizes.
        df = df.with_columns(
            id_stded=pl.when(pl.col("itemizes")).then(0.0).otherwise(pl.col("standard_deduction"))
        )
        if p.in_effect("standard_deduction_separate_addition"):
            df = df.with_columns(
                id_stded=pl.when(files_separate())
                .then(pl.col("id_stded") + p.num("standard_deduction_separate_addition"))
                .otherwise(pl.col("id_stded"))
            )
    else:
        s = p.num("standard_deduction", "single")
        h = p.num("standard_deduction", "head_of_household")
        j = p.num("standard_deduction", "married_joint")
        if effective_year <= 1998:
            df = df.with_columns(
                id_stded=pl.when(files_single()).then(s)
                .when(files_head_of_household()).then(h)
                .otherwise(j / pl.col("id_sep"))
            )
        else:
            w = p.num("standard_deduction", "married_separate")
            df = df.with_columns(
                id_stded=pl.when(files_single()).then(s)
                .when(files_head_of_household()).then(h)
                .when(files_joint()).then(j)
                .otherwise(w / pl.col("id_sep"))
            )
        if p.in_effect("standard_deduction_property_tax_addition"):
            addback = pl.min_horizontal(p.num("standard_deduction_property_tax_addition") * pl.col("id_taxpayers"), pl.col("proptax"))
            df = df.with_columns(id_stded=pl.col("id_stded") + addback)

    df = df.with_columns(id_xitded_detail=pl.col("id_xitded"))
    if effective_year == 1999:
        df = df.with_columns(id_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("id_xitded")))

    # Taxpayers 65 or older, and dependent filers.
    aged = aged_count()
    sep_status = files_separate()
    if p.in_effect("separate_aged_reduction"):
        reduction = p.num("separate_aged_reduction")
        df = df.with_columns(id_stded=pl.when(sep_status).then(pl.col("id_stded") - reduction * aged).otherwise(pl.col("id_stded")))
    if p.in_effect("aged_standard_deduction"):
        t = p.value("aged_standard_deduction")
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
    # The federal exemption amount.
    df = df.with_columns(id_exemp=pl.col("pre1987_amex") if effective_year <= 1986 else pl.col("personal_exemptions"))

    df = df.with_columns(id_taxinc=(pl.col("id_agi") - pl.col("id_deduc") - pl.col("id_exemp")).clip(0, None))
    if effective_year >= 2018:
        df = df.with_columns(id_taxinc=(pl.col("id_taxinc") - pl.col("qbi_deduction")).clip(0, None))
    if effective_year >= 2025:
        # Idaho conforms to the federal senior deduction (H.B. 559, 2026 session).
        df = df.with_columns(id_taxinc=(pl.col("id_taxinc") - pl.col("senior_deduction")).clip(0, None))

    # --- Tax ---
    brackets = p.value("brackets")
    aif = p.num("bracket_inflation")
    txp = pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"])).then(2.0).otherwise(1.0)
    tinc = pl.col("id_taxinc") / txp
    stat = bracket_tax(tinc / aif, brackets) * aif
    df = df.with_columns(id_statax=stat * txp)

    # --- Credits ---
    if p.in_effect("child_tax_credit_per_child"):
        per_child = p.num("child_tax_credit_per_child")
        df = df.with_columns(id_ctcred=pl.min_horizontal(per_child * pl.col("dep17"), pl.col("id_statax")))
    else:
        df = df.with_columns(id_ctcred=pl.lit(0.0))

    depx_plus_texp = pl.col("id_taxpayers") + pl.col("depx")
    grcred = p.num("grocery_credit") * depx_plus_texp
    if p.in_effect("grocery_credit_low_income_bonus"):
        bonus = p.value("grocery_credit_low_income_bonus")
        grcred = grcred + pl.when(pl.col("id_taxinc") <= float(bonus["taxable_income_limit"])).then(float(bonus["amount"]) * depx_plus_texp).otherwise(0.0)
    grcred = grcred + p.num("grocery_credit_aged") * aged_count()
    if p.value("grocery_credit_excludes_separate_filers"):
        grcred = pl.when((files_separate()) & (aged_count() < 1)).then(0.0).otherwise(grcred)
    df = df.with_columns(id_grcred=pl.when(is_dependent_filer()).then(0.0).otherwise(grcred))

    df = df.with_columns(id_credit=pl.col("id_ctcred") + pl.col("id_grcred"))
    df = df.with_columns(id_statax=pl.col("id_statax") - pl.col("id_credit"))

    # --- Permanent building fund tax ---
    if p.in_effect("building_fund_tax"):
        bf = p.value("building_fund_tax")
        limits = bf["limit"]
        lim = (
            pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then(float(limits["single_or_hoh"]))
            .when(files_joint()).then(float(limits["married_joint"]))
            .otherwise(float(limits["married_separate"]))
        )
        lim = lim + pl.when(pl.col("filing_status") != "married_separate").then(p["building_fund_limit_aged"] * aged_count()).otherwise(0.0)
        df = df.with_columns(
            id_statax=pl.when(pl.col("id_household_income") > lim).then(pl.col("id_statax") + float(bf["amount"])).otherwise(pl.col("id_statax"))
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
