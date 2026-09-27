"""Iowa individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
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
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

IA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ia" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


_RAW_INPUT_COLUMNS = [
    "mstat", "depx", "dep17", "dep18", "dep6", "dep13", "pwages", "swages",
    "proptax", "otheritem", "mortgage", "childcare", "intrec", "psemp",
    "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui",
]


def compute_ia_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(IA_PARAMS, effective_year)

    df = df.with_columns(
        ia_sep=separate_divisor(),
        ia_taxpayers=taxpayer_count(),
    )

    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    df = df.with_columns(ia_setax=setax)

    df = deflate_for_extrapolation(df, flate)

    phas92_base = float(p["itemized_phaseout_base"][1960])
    if effective_year <= 2012:
        aif92 = p.num("itemized_phaseout_aif92_1992_2012") if effective_year >= 1992 else 1.0
        phas92 = phas92_base * aif92 / pl.col("ia_sep")
    else:
        aif13 = p.num("itemized_phaseout_aif13_2013plus")
        mult = by_filing_status(p["itemized_phaseout_multiplier"])
        phas92 = aif13 * p["itemized_phaseout_base_multiple_2013plus"] * phas92_base * mult

    # --- AGI ---
    df = df.with_columns(ia_agi=pl.col("agi") + 0.5 * pl.col("ia_setax"))
    if 1982 <= effective_year <= 1984:
        rate_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        cap_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        lesser_wage = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        twoded = pl.when(files_joint()).then(
            (rate_2e * lesser_wage).clip(0, cap_2e)
        ).otherwise(0.0)
        df = df.with_columns(ia_agi=pl.col("ia_agi") + twoded)
    if effective_year == 1981:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = by_filing_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_cap = (pl.col("dividends") + pl.col("intrec")).clip(0, 100.0 * pl.col("ia_taxpayers"))
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
        if effective_year >= 2007:
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
    df = df.with_columns(ia_penexc=penexc, ia_agi=pl.col("ia_agi") - penexc)
    ui_total = unemployment_total()
    if effective_year == 2009:
        df = df.with_columns(ia_agi=pl.col("ia_agi") + pl.min_horizontal(ui_total, p["unemployment_addback_2009_per_taxpayer"] * pl.col("ia_taxpayers")))

    fedded = pl.col("fiitax").clip(0, None)
    dedbus = p.num("qbi_deduction_share") * pl.col("qbi_deduction") if effective_year >= 2019 else pl.lit(0.0)

    # --- Standard deduction ---
    pct = p.num("standard_deduction_pct")
    cap_single = p.num("standard_deduction_cap_single")
    cap_joint = p.num("standard_deduction_cap_married_joint")
    is_joint = files_joint()
    # `if(mst.eq.1.or.mst.eq.3.or.mst.eq.6)` gets the single cap; head of
    # household (code 4) gets the joint cap.
    gets_single_cap = pl.col("filing_status").is_in(["single", "married_separate"])
    stded = pl.when(gets_single_cap).then(
        (pct * pl.col("ia_agi")).clip(0, cap_single)
    ).otherwise((pct * pl.col("ia_agi")).clip(0, cap_joint))
    df = df.with_columns(ia_stded=stded)

    # --- Itemized deduction ---
    # Before 1987 federal gross itemized deductions (`comnew(30)`) include
    # the state tax deduction, which `xitded=comnew(30)-data(50)` removes,
    # leaving the itemized inputs.
    if effective_year <= 1986:
        salt_capped_plus_mortgage = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
    else:
        salt_capped_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")  # comnew(30)
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
        ia_taxinc=(pl.col("ia_agi") + pl.col("ia_setax") - fedded - dedbus - pl.col("ia_deduc")).clip(0, None),
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

    # --- Bracket tax ---
    aif = p.num("bracket_inflation_factor")
    if effective_year <= 1986:
        table = p["brackets_pre1987"]
    elif effective_year <= 1997:
        table = p["brackets_1987_1997"]
    elif effective_year <= 2018:
        table = p["brackets_1998_2018"]
    else:
        table = p["brackets_2019plus"]

    statax = bracket_tax(pl.col("ia_taxinc") / aif, table) * aif
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
    exy_other = p.num("exy_other")
    not_single = pl.col("filing_status") != "single"
    if effective_year <= 1997:
        rate_alt = float(p["alt_tax_rate_pre1998"][1960])
    else:
        rate_alt = p.num("alt_tax_rate_1998plus")
    if effective_year >= 1987:
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
    xmp = p.num("personal_exemption_credit_amount")
    is_hoh = files_head_of_household()
    if effective_year <= 1994:
        extra = p["personal_credit_taxpayer_addition_pre1995"]
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
    if effective_year >= 1982:
        amt_federal = pl.col("amt")
        if effective_year <= 1984:
            alty = amt_federal * float(resolve_year(p["amt_share_of_federal"], effective_year))
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
        exy_single = p.num("exy_single")
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
    # Federal credit before its liability limit (`comnew(176)`), read
    # undeflated; TAXSIM leaves that slot at 0 before 1987.
    ccc_base = pl.col("ccc_uncapped")
    if effective_year <= 1981:
        chcr = ccc_base * float(p["child_care_credit_flat_rate_pre1982"][1960])
    elif effective_year <= 1985:
        chcr = pl.col("childcare") * float(p["child_care_credit_flat_rate_1982_1985"][1960])
    elif effective_year <= 1989:
        chcr = ccc_base * float(p["child_care_credit_flat_rate_1986_1989"][1960])
    elif effective_year <= 1992:
        chcr = ccc_base * interpolate_table(pl.col("agi"), p["child_care_credit_table_1990_1992"])
    elif effective_year <= 2005:
        chcr = ccc_base * interpolate_table(posagi, p["child_care_credit_table_1993_2005"])
    else:
        chcr = ccc_base * interpolate_table(posagi, p["child_care_credit_table_2006plus"])
    df = df.with_columns(ia_chcr=chcr)

    # --- EITC ---
    earncr = pl.lit(0.0)
    if effective_year >= 1990:
        rate_eitc = p.num("eitc_rate")
        cap_eitc = p.num("eitc_agi_cap")
        earncr = pl.when(pl.col("agi") < cap_eitc).then(rate_eitc * pl.col("eitc")).otherwise(0.0)

        if effective_year == 2009:
            nkid = pl.col("depx")
            e = p["eitc_2009_joint"]
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

        if effective_year == 2010:
            cap1 = float(p["eitc_2010_married_joint_eligibility_cap_1kid"][1960])
            cap2 = float(p["eitc_2010_married_joint_eligibility_cap_2kids"][1960])
            over_1kid = (pl.col("depx") == 1) & ((pl.col("earned_income") > cap1) | (pl.col("agi") > cap1))
            over_2kid = (pl.col("depx") == 2) & ((pl.col("earned_income") > cap2) | (pl.col("agi") > cap2))
            in_2010_branch = is_joint & (earncr > 0)
            earncr = pl.when(in_2010_branch & (over_1kid | over_2kid)).then(0.0).otherwise(earncr)
            cap0 = float(p["eitc_flat_eligibility_cap_0kids"][1960])
            cap1_flat = float(p["eitc_flat_eligibility_cap_1kid"][1960])
            cap2p_flat = float(p["eitc_flat_eligibility_cap_2plus_kids"][1960])
            over_0_flat = (pl.col("depx") == 0) & ((pl.col("earned_income") > cap0) | (pl.col("agi") > cap0))
            over_1_flat = (pl.col("depx") == 1) & ((pl.col("earned_income") > cap1_flat) | (pl.col("agi") > cap1_flat))
            over_2p_flat = (pl.col("depx") >= 2) & ((pl.col("earned_income") > cap2p_flat) | (pl.col("agi") > cap2p_flat))
            earncr = pl.when(~in_2010_branch & (over_0_flat | over_1_flat | over_2p_flat)).then(0.0).otherwise(earncr)
        elif effective_year >= 1990:
            cap0 = float(p["eitc_flat_eligibility_cap_0kids"][1960])
            cap1_flat = float(p["eitc_flat_eligibility_cap_1kid"][1960])
            cap2p_flat = float(p["eitc_flat_eligibility_cap_2plus_kids"][1960])
            over_0_flat = (pl.col("depx") == 0) & ((pl.col("earned_income") > cap0) | (pl.col("agi") > cap0))
            over_1_flat = (pl.col("depx") == 1) & ((pl.col("earned_income") > cap1_flat) | (pl.col("agi") > cap1_flat))
            over_2p_flat = (pl.col("depx") >= 2) & ((pl.col("earned_income") > cap2p_flat) | (pl.col("agi") > cap2p_flat))
            earncr = pl.when(over_0_flat | over_1_flat | over_2p_flat).then(0.0).otherwise(earncr)
    df = df.with_columns(ia_earncr=earncr)

    if effective_year <= 1989:
        df = df.with_columns(ia_statax=(pl.col("ia_statax") - pl.col("ia_chcr") - pl.col("ia_earncr")).clip(0, None))
    if 1990 <= effective_year <= 2006:
        df = df.with_columns(
            ia_statax=(pl.col("ia_statax") - pl.col("ia_earncr")).clip(0, None) - pl.col("ia_chcr")
        )
    exy_single = p.num("exy_single")
    is_single = files_single()
    df = df.with_columns(
        ia_statax=pl.when(is_single).then(
            pl.max_horizontal(pl.min_horizontal(pl.col("ia_agi") - exy_single, pl.col("ia_statax")), 0.0)
        ).otherwise(pl.col("ia_statax"))
    )
    if effective_year >= 2007:
        df = df.with_columns(ia_statax=pl.col("ia_statax") - pl.col("ia_earncr") - pl.col("ia_chcr"))

    if effective_year >= 1992:
        exy_other = p.num("exy_other")
        low_income = pl.when(is_single).then(pl.col("agi") <= exy_single).otherwise(pl.col("agi") <= exy_other)
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
