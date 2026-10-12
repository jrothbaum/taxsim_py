"""Iowa individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    forced_standard,
    higher_earner_share,
    interpolate_table,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

IA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ia" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def compute_ia_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, IA_PARAMS)
    p = YearParams(IA_PARAMS, effective_year)

    df = df.with_columns(
        ia_sep=separate_divisor(),
        ia_taxpayers=taxpayer_count(),
    )

    # Self-employment tax is not deflated in projected years.
    df = df.with_columns(ia_setax=pl.col("payroll_setax"))

    df = deflate_for_extrapolation(df, flate)

    threshold = p.value("itemized_limitation_threshold")
    index = p.num("itemized_limitation_index")
    if isinstance(threshold, dict):
        phas92 = by_filing_status({s: float(v) * index for s, v in threshold.items()})
    else:
        phas92 = float(threshold) * index / pl.col("ia_sep")

    # --- AGI ---
    df = df.with_columns(ia_agi=pl.col("agi") + 0.5 * pl.col("ia_setax"))
    if 1982 <= effective_year <= 1984:
        p87 = YearParams(PRE1987_PARAMS, effective_year)
        rate_2e = p87.num("two_earner_deduction_rate")
        cap_2e = p87.num("two_earner_deduction_cap")
        lesser_wage = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        twoded = pl.when(files_joint()).then(
            (rate_2e * lesser_wage).clip(0, cap_2e)
        ).otherwise(0.0)
        df = df.with_columns(ia_agi=pl.col("ia_agi") + twoded)
    if p.in_effect("dividend_interest_exclusion_per_taxpayer"):
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = by_filing_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_cap = (pl.col("dividends") + pl.col("intrec")).clip(0, p.num("dividend_interest_exclusion_per_taxpayer") * pl.col("ia_taxpayers"))
        df = df.with_columns(ia_agi=pl.col("ia_agi") + divexc - own_cap)
    # Social Security: federal taxable benefits out; through 2013 Iowa's own
    # partial taxation in.
    ss_p = p["social_security_base"]
    pha = (
        pl.when(files_joint()).then(float(ss_p["joint"]))
        .when(files_separate()).then(float(ss_p["married_separate"]))
        .otherwise(float(ss_p["single"]))
    )
    df = df.with_columns(ia_agi=pl.col("ia_agi") - pl.col("taxable_social_security"), ia_addph=pl.lit(0.0))
    if effective_year <= 2013:
        half = 0.5 * pl.col("gssi")
        ssb = pl.min_horizontal(0.5 * (half + pl.col("ia_agi").clip(0, None) - pha).clip(0, None), half)
        if p.in_effect("social_security_phaseout_share"):
            ssb = ssb * (1 - p.num("social_security_phaseout_share"))
        ssb = pl.when(pl.col("gssi") > 0).then(ssb).otherwise(0.0)
        df = df.with_columns(ia_agi=pl.col("ia_agi") + ssb, ia_addph=pl.col("taxable_social_security") - ssb)
    else:
        half = 0.5 * pl.col("gssi")
        provisional = pl.col("agi") - pl.col("taxable_social_security") + pl.col("transfers") + half
        df = df.with_columns(
            ia_addph=pl.when(pl.col("gssi") > 0).then(pl.min_horizontal(half, (provisional - pha).clip(0, None) / 2)).otherwise(0.0)
        )
    # Pension exclusion (1995+), taxpayers 65 or older.
    if effective_year >= 1995:
        cap = p.num("pension_exclusion")
        penexc = pl.when(aged_count() > 0).then(pl.min_horizontal(pl.col("ia_taxpayers") * cap, pl.col("pensions"))).otherwise(0.0)
    else:
        penexc = pl.lit(0.0)
    if p.in_effect("retirement_exclusion"):
        pe = p.value("retirement_exclusion")
        eligible = (pl.col("page") >= float(pe["minimum_age"])) | (pl.col("sage") >= float(pe["minimum_age"]))
        cap = float(pe["cap_per_taxpayer"]) * pl.col("ia_taxpayers")
        penexc = pl.when(eligible).then(pl.min_horizontal(cap, pl.col("pensions"))).otherwise(0.0)
    df = df.with_columns(ia_penexc=penexc, ia_agi=pl.col("ia_agi") - penexc)
    ui_total = unemployment_total()
    if p.in_effect("unemployment_addback_per_taxpayer"):
        df = df.with_columns(ia_agi=pl.col("ia_agi") + pl.min_horizontal(ui_total, p.num("unemployment_addback_per_taxpayer") * pl.col("ia_taxpayers")))

    fedded = pl.col("fiitax").clip(0, None)
    if effective_year >= 2022:
        fedded = (pl.col("fiitax") - pl.col("addmed")).clip(0, None)  # Additional Medicare tax is not income tax
    if not p.value("federal_tax_deduction"):
        fedded = pl.lit(0.0)
    dedbus = p.num("qbi_deduction_share") * pl.col("qbi_deduction") if effective_year >= 2019 else pl.lit(0.0)

    # --- Standard deduction ---
    pct = p.num("standard_deduction_pct")
    cap_single = p.num("standard_deduction_cap", "single")
    cap_joint = p.num("standard_deduction_cap", "joint_or_hoh")
    is_joint = files_joint()
    gets_single_cap = pl.col("filing_status").is_in(["single", "married_separate"])
    stded = pl.when(gets_single_cap).then(
        (pct * pl.col("ia_agi")).clip(0, cap_single)
    ).otherwise((pct * pl.col("ia_agi")).clip(0, cap_joint))
    if p.value("conforms_to_federal_standard_deduction"):
        stded = pl.col("standard_deduction")
    df = df.with_columns(ia_stded=stded)

    # --- Itemized deduction ---
    # Federal gross itemized deductions less the state tax deduction (before
    # 1987, the itemized inputs).
    if effective_year <= 1986:
        salt_capped_plus_mortgage = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
    else:
        salt_capped_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
    df = df.with_columns(ia_xitded=(salt_capped_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None))
    over_thr = (pl.col("ia_agi") > phas92) & (1991 <= effective_year <= 2017) & pl.col("itemizes")
    df = df.with_columns(
        ia_xitded=pl.when(over_thr)
        .then((pl.col("itemized_deduction") - pl.col("state_sales_or_income_tax_ded") * pl.col("itemized_deduction") / salt_capped_plus_mortgage.clip(1e-9, None)).clip(0, None))
        .otherwise(pl.col("ia_xitded"))
    )
    if 2018 <= effective_year <= 2019:
        deduc1 = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
        dlim1 = p["itemized_phaseout_rate"] * (pl.col("ia_agi") - phas92).clip(0, None)
        dlim2 = p["itemized_phaseout_cap_rate"] * deduc1
        dedphs = pl.min_horizontal(dlim1, dlim2)
        df = df.with_columns(ia_xitded=(deduc1 - dedphs).clip(0, None))
    if effective_year >= 2020:
        df = df.with_columns(ia_xitded=salt_capped_plus_mortgage)

    df = df.with_columns(ia_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ia_xitded")))

    df = df.with_columns(ia_deduc=pl.max_horizontal(pl.col("ia_stded"), pl.col("ia_xitded")))

    # --- Taxable income --- Half of self-employment tax is already in
    # AGI, and the whole amount is added again.
    df = df.with_columns(
        ia_yad=pl.col("ia_agi") + pl.col("ia_setax") - fedded - dedbus,
        ia_taxinc=(pl.col("ia_agi") + pl.col("ia_setax") - fedded - dedbus - pl.col("ia_deduc")
                   # 2025 on: Iowa starts from federal taxable income, which is already
                   # net of the federal senior deduction.
                   - (pl.col("senior_deduction") if effective_year >= 2025 else 0.0)
                   ).clip(0, None),
    )

    # --- Joint returns may file combined: each spouse on their own share ---
    agih = higher_earner_share(pl.col("ia_agi"))
    agiw = pl.col("ia_agi") - agih
    is_mfc = is_joint & (pl.col("ia_agi") > 0)
    stded_h_general = (pct * pl.col("ia_agi")).clip(0, cap_single)
    xitdh = pl.when(pl.col("ia_agi") != 0).then(pl.col("ia_xitded") * agih / pl.col("ia_agi")).otherwise(0.0)
    xitdw = pl.col("ia_xitded") - xitdh
    dedh_general = pl.when(stded_h_general >= xitdw).then(stded_h_general).otherwise(xitdh)
    dedw_general = pl.max_horizontal(stded_h_general, xitdw)
    feddh = pl.when(pl.col("ia_agi") != 0).then(fedded * agih / pl.col("ia_agi")).otherwise(0.0)
    feddw = fedded - feddh
    agiw_positive = is_mfc & (agiw > 0)
    taxinh = (
        pl.when(agiw_positive)
        .then((agih - dedh_general + 0.25 * pl.col("ia_setax") - feddh - 0.5 * dedbus).clip(0, None))
        .when(is_mfc)
        .then(pl.col("ia_taxinc"))
        .otherwise(pl.lit(0.0))
    )
    taxinw = (
        pl.when(agiw_positive)
        .then((agiw - dedw_general + 0.25 * pl.col("ia_setax") - feddw - 0.5 * dedbus).clip(0, None))
        .otherwise(pl.lit(0.0))
    )
    df = df.with_columns(ia_taxinh=taxinh, ia_taxinw=taxinw, ia_agi_higher_earner=agih, ia_agi_lower_earner=agiw)

    # --- Tax ---
    aif = p.num("bracket_inflation")
    table = p.value("brackets")
    statax = bracket_tax(pl.col("ia_taxinc") / aif, table) * aif
    if p.in_effect("brackets_by_status"):
        schedules = p.value("brackets_by_status")
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ia_taxinc"), schedules["joint"])).otherwise(
            bracket_tax(pl.col("ia_taxinc"), schedules["other"])
        )
    df = df.with_columns(ia_statax=statax)

    if effective_year <= 1986:
        taxinh2 = pl.when(is_mfc).then(
            (pl.col("ia_agi_higher_earner") - dedh_general + pl.col("ia_setax") * pl.col("ia_agi_higher_earner") / pl.col("ia_agi").clip(1e-9, None) - fedded * pl.col("ia_agi_higher_earner") / pl.col("ia_agi").clip(1e-9, None)).clip(0, None)
        ).otherwise(pl.lit(0.0))
        taxinw2 = pl.when(is_mfc).then(
            (pl.col("ia_agi_lower_earner") - dedw_general + pl.col("ia_setax") * pl.col("ia_agi_lower_earner") / pl.col("ia_agi").clip(1e-9, None) - fedded * pl.col("ia_agi_lower_earner") / pl.col("ia_agi").clip(1e-9, None)).clip(0, None)
        ).otherwise(pl.lit(0.0))
        stath = bracket_tax(taxinh2 / aif, table) * aif
        statw = bracket_tax(taxinw2 / aif, table) * aif
        df = df.with_columns(
            ia_statax=pl.when(is_mfc).then(pl.min_horizontal(pl.col("ia_statax"), stath + statw)).otherwise(pl.col("ia_statax"))
        )
        split_rate = bracket_rate(taxinw2 / aif, table)
    else:
        stath = bracket_tax(pl.col("ia_taxinh") / aif, table) * aif
        statw = bracket_tax(pl.col("ia_taxinw") / aif, table) * aif
        df = df.with_columns(
            ia_statax=pl.when(is_mfc).then(pl.min_horizontal(pl.col("ia_statax"), stath + statw)).otherwise(pl.col("ia_statax"))
        )
        split_rate = bracket_rate(pl.col("ia_taxinw") / aif, table)
    rate_expr = pl.when(is_mfc).then(split_rate).otherwise(
        bracket_rate(pl.col("ia_taxinc") / aif, table)
    )

    # --- Alternate Tax --- (never single)
    exy_other = p.num("no_tax_income_limit", "other")
    not_single = pl.col("filing_status") != "single"
    if p.in_effect("alternate_tax_rate"):
        rate_alt = p.num("alternate_tax_rate")
        if effective_year <= 1997:
            altax = (pl.col("ia_agi") - exy_other).clip(0, None) * rate_alt
        else:
            subtr = pl.lit(exy_other)
            if effective_year >= 2007:
                subtr = pl.when(aged_count() > 0).then(p.num("alternate_tax_aged_floor")).otherwise(subtr)
            altax = (pl.col("ia_agi") + pl.col("ia_penexc") + pl.col("ia_addph") - subtr).clip(0, None) * rate_alt
        df = df.with_columns(
            ia_statax=pl.when(not_single).then(pl.min_horizontal(pl.col("ia_statax"), altax)).otherwise(pl.col("ia_statax"))
        )

    # --- Personal Exemption Credit ---
    xmp = p.num("personal_exemption_credit")
    is_hoh = files_head_of_household()
    if p.in_effect("personal_credit_taxpayer_addition"):
        extra = p.num("personal_credit_taxpayer_addition")
        gcred = (xmp + extra) * (pl.col("ia_taxpayers") + aged_count()) + xmp * pl.col("depx")
        gcred = pl.when(is_hoh).then(gcred + xmp + extra).otherwise(gcred)
    elif effective_year <= 1997:
        gcred = (xmp / 2.0) * (pl.col("ia_taxpayers") + aged_count()) + xmp * pl.col("depx")
        gcred = pl.when(is_hoh).then(gcred + xmp / 2.0).otherwise(gcred)
    else:
        gcred = xmp * (pl.col("depx") + pl.col("ia_taxpayers")) + (xmp / 2.0) * aged_count()
        gcred = pl.when(is_hoh).then(gcred + xmp).otherwise(gcred)
    df = df.with_columns(ia_gcred=gcred)
    df = df.with_columns(ia_statax=(pl.col("ia_statax") - pl.col("ia_gcred")).clip(0, None))

    # --- AMT ---
    if p.in_effect("amt_share_of_federal") or p.in_effect("amt_rate"):
        if p.in_effect("amt_share_of_federal"):
            alty = pl.col("amt") * p.num("amt_share_of_federal")
        else:
            # Preferences: property tax only.
            addprf = pl.col("proptax")
            alminy = pl.col("ia_taxinc") + addprf
            if 1991 <= effective_year <= 2017:
                alminy = pl.when(over_thr).then(
                    alminy - pl.col("state_sales_or_income_tax_ded") * pl.col("itemized_deduction") / salt_capped_plus_mortgage.clip(1e-9, None)
                ).otherwise(alminy)
            exclnt_base = by_filing_status(p["amt_exclusion_base_by_status"])
            exclnt_thr = by_filing_status(p["amt_exclusion_phaseout_threshold_by_status"])
            exclnt = pl.when(alminy > exclnt_thr).then((exclnt_base - p["amt_exclusion_phaseout_rate"] * (alminy - exclnt_thr)).clip(0, None)).otherwise(exclnt_base)
            amt_rate_val = p.num("amt_rate")
            alty = (amt_rate_val * (alminy - exclnt) - pl.col("ia_statax")).clip(0, None)
        exy_single = p.num("no_tax_income_limit", "single")
        aged = aged_count() > 0
        aged_limit = p["amt_aged_exempt_income"]
        is_single = files_single()
        low_income_amt_exempt = (
            (is_single & ~aged & (pl.col("ia_agi") <= exy_single))
            | (is_single & aged & (pl.col("ia_agi") <= aged_limit["single"]))
            | (not_single & ~aged & (pl.col("ia_agi") <= exy_other))
            | (not_single & aged & (pl.col("ia_agi") <= aged_limit["other"]))
        )
        alty = pl.when(low_income_amt_exempt).then(0.0).otherwise(alty)
        df = df.with_columns(ia_statax=pl.col("ia_statax") + alty)

    # --- Child/Dependent Care Credit ---
    posagi = pl.col("ia_agi").clip(0, None)
    # Federal credit before its liability limit, read undeflated; TAXSIM
    # leaves it at 0 before 1987.
    ccc_base = pl.col("ccc_uncapped")
    if p.in_effect("child_care_credit_expense_rate"):
        chcr = pl.col("childcare") * p.num("child_care_credit_expense_rate")
    else:
        share = p.value("child_care_credit_federal_share")
        if not isinstance(share, list):
            chcr = ccc_base * float(share)
        elif effective_year <= 1992:
            chcr = ccc_base * interpolate_table(pl.col("agi"), share)
        else:
            chcr = ccc_base * interpolate_table(posagi, share)
    df = df.with_columns(ia_chcr=chcr)

    # --- EITC ---
    earncr = pl.lit(0.0)
    if effective_year >= 1990:
        rate_eitc = p.num("eitc_rate")
        cap_eitc = p.num("eitc_agi_cap")
        # Tested against Iowa AGI.
        earncr = pl.when(pl.col("ia_agi") < cap_eitc).then(rate_eitc * pl.col("eitc")).otherwise(0.0)

        if p.in_effect("eitc_joint_worksheet"):
            nkid = pl.col("depx")
            e = p.value("eitc_joint_worksheet")
            start, maximum, rate = e["phaseout_start"], e["maximum"], e["phaseout_rate"]
            xlin2 = (
                pl.when(nkid == 0).then(float(start["none"]))
                .when((nkid >= 1) & (nkid <= 2)).then(float(start["one_or_two"]))
                .otherwise(float(start["more"]))
            )
            xlin3 = (pl.col("earned_income") - xlin2).clip(0, None)
            xlin5 = (
                pl.when(nkid == 0).then((maximum["none"] - xlin3 * rate["none"]).clip(0, None))
                .when(nkid == 1).then((maximum["one"] - xlin3 * rate["one"]).clip(0, None))
                .otherwise((maximum["more"] - xlin3 * rate["more"]).clip(0, None))
            )
            earncr = pl.when(is_joint & (earncr > 0)).then(e["share"] * xlin5).otherwise(earncr)

        if p.in_effect("eitc_eligibility_caps"):
            caps = p.value("eitc_eligibility_caps")
            cap0, cap1_flat, cap2p_flat = float(caps["none"]), float(caps["one"]), float(caps["two_or_more"])
            over_0_flat = (pl.col("depx") == 0) & ((pl.col("earned_income") > cap0) | (pl.col("ia_agi") > cap0))
            over_1_flat = (pl.col("depx") == 1) & ((pl.col("earned_income") > cap1_flat) | (pl.col("ia_agi") > cap1_flat))
            over_2p_flat = (pl.col("depx") >= 2) & ((pl.col("earned_income") > cap2p_flat) | (pl.col("ia_agi") > cap2p_flat))
            over_flat = over_0_flat | over_1_flat | over_2p_flat
            if p.in_effect("eitc_joint_eligibility_caps"):
                joint_caps = p.value("eitc_joint_eligibility_caps")
                cap1, cap2 = float(joint_caps["one"]), float(joint_caps["two"])
                over_1kid = (pl.col("depx") == 1) & ((pl.col("earned_income") > cap1) | (pl.col("ia_agi") > cap1))
                over_2kid = (pl.col("depx") == 2) & ((pl.col("earned_income") > cap2) | (pl.col("ia_agi") > cap2))
                in_joint_branch = is_joint & (earncr > 0)
                earncr = pl.when(in_joint_branch & (over_1kid | over_2kid)).then(0.0).otherwise(earncr)
                earncr = pl.when(~in_joint_branch & over_flat).then(0.0).otherwise(earncr)
            else:
                earncr = pl.when(over_flat).then(0.0).otherwise(earncr)
    df = df.with_columns(ia_earncr=earncr)

    if effective_year <= 1989:
        df = df.with_columns(ia_statax=(pl.col("ia_statax") - pl.col("ia_chcr") - pl.col("ia_earncr")).clip(0, None))
    if 1990 <= effective_year <= 2006:
        df = df.with_columns(
            ia_statax=(pl.col("ia_statax") - pl.col("ia_earncr")).clip(0, None) - pl.col("ia_chcr")
        )
    exy_single = p.num("no_tax_income_limit", "single")
    is_single = files_single()
    df = df.with_columns(
        ia_statax=pl.when(is_single).then(
            pl.max_horizontal(pl.min_horizontal(pl.col("ia_agi") - exy_single, pl.col("ia_statax")), 0.0)
        ).otherwise(pl.col("ia_statax"))
    )
    if effective_year >= 2007:
        df = df.with_columns(ia_statax=pl.col("ia_statax") - pl.col("ia_earncr") - pl.col("ia_chcr"))

    if effective_year >= 1992:
        exy_other = p.num("no_tax_income_limit", "other")
        low_income = pl.when(is_single).then(pl.col("agi") <= exy_single).otherwise(pl.col("agi") <= exy_other)
        if effective_year >= 2022:
            # Actual-law years: a return with a taxpayer 65 or older owes no tax
            # at or below $24,000 (single) or $32,000 (other).
            aged_limit = p["amt_aged_exempt_income"]
            low_income = low_income | (
                (aged_count() > 0)
                & pl.when(is_single).then(pl.col("agi") <= aged_limit["single"]).otherwise(pl.col("agi") <= aged_limit["other"])
            )
        df = df.with_columns(
            ia_statax=pl.when((pl.col("ia_statax") > 0) & low_income).then(0.0).otherwise(pl.col("ia_statax"))
        )

    df = df.with_columns(siitax=pl.col("ia_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("ia_agi"),
        standard_deduction=pl.col("ia_stded"),
        itemized_deductions=pl.col("ia_xitded"),
        taxable_income=pl.col("ia_taxinc"),
        child_care_credit=pl.col("ia_chcr"),
        eic=pl.col("ia_earncr"),
        credits=pl.col("ia_gcred") + pl.col("ia_chcr") + pl.col("ia_earncr"),
        rate=rate_expr,
    )
