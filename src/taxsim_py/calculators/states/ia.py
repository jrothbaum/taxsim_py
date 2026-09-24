"""Iowa individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    with_defaults,
    forced_standard,
    by_filing_status as _by_status,
    interpolate_table as _tablki,
    with_default as _with_default,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

IA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ia" / "income_tax.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


_RAW_INPUT_COLUMNS = [
    "mstat", "depx", "dep17", "dep18", "dep6", "dep13", "pwages", "swages",
    "proptax", "otheritem", "mortgage", "childcare", "intrec", "psemp",
    "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui",
]


def compute_ia_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = IA_PARAMS
    df = with_defaults(df, ("proptax", "otheritem", "mortgage", "dividends", "intrec", "ui", "pui", "sui", "depx", "psemp", "ssemp"))
    df = _with_default(df, "earned_income")
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "salt_capped")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)

    df = df.with_columns(
        ia_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        ia_txp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )

    # `setax` (comnew(175)) - computed at the REAL `year`'s rates on REAL
    # (undeflated) wages, NOT `effective_year`/already-deflated figures
    # (same technique Alabama already established - `comnew(175)` sits
    # OUTSIDE the real dispatcher's own generic deflate loop).
    wage_base = float(resolve_year(PAYROLL_PARAMS["oasdi_wage_base"], year))
    hi_wage_base = float(resolve_year(PAYROLL_PARAMS["hi_wage_base"], year))
    net_earnings_factor = float(resolve_year(PAYROLL_PARAMS["se_net_earnings_factor"], year))
    se_oasdi_rate = float(resolve_year(PAYROLL_PARAMS["se_oasdi_rate"], year))
    se_hi_rate = float(resolve_year(PAYROLL_PARAMS["se_hi_rate"], year))
    setax = household_self_employment_tax(
        pl.col("psemp"), pl.col("ssemp"), pl.col("pwages"), pl.col("swages"),
        net_earnings_factor, wage_base, se_oasdi_rate, se_hi_rate, hi_wage_base,
    )
    df = df.with_columns(ia_setax=setax)

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax",
        ],
    )

    phas92_base = float(p["itemized_phaseout_base"][1960])
    if effective_year <= 2012:
        aif92 = float(resolve_year(p["itemized_phaseout_aif92_1992_2012"], effective_year)) if effective_year >= 1992 else 1.0
        phas92 = phas92_base * aif92 / pl.col("ia_sep")
    else:
        aif13 = float(resolve_year(p["itemized_phaseout_aif13_2013plus"], effective_year))
        mult = _by_status(p["itemized_phaseout_multiplier"])
        phas92 = aif13 * 2.5 * phas92_base * mult

    # --- AGI ---
    df = df.with_columns(ia_agi=pl.col("agi") + 0.5 * pl.col("ia_setax"))
    if effective_year == 2020:
        # `data(58)` (charity_cash) confirmed inert - no-op.
        df = df.with_columns(
            ia_agi=pl.when(~pl.col("itemizes")).then(pl.col("ia_agi") + pl.min_horizontal(300.0, 0.0)).otherwise(pl.col("ia_agi"))
        )
    # `data(22)` (state tax refund) and `comnew(79)` (SS-in-AGI, and the
    # entire Social-Security-taxation worksheet it feeds) both confirmed
    # inert - see module docstring.
    if effective_year <= 1987:
        pass  # `xjobs()` confirmed inert.
    if 1982 <= effective_year <= 1984:
        rate_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        cap_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        lesser_wage = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        twoded = pl.when(pl.col("filing_status") == "married_joint").then(
            (rate_2e * lesser_wage).clip(0, cap_2e)
        ).otherwise(0.0)
        df = df.with_columns(ia_agi=pl.col("ia_agi") + twoded)
    if effective_year == 1981:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = _by_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_cap = (pl.col("dividends") + pl.col("intrec")).clip(0, 100.0 * pl.col("ia_txp"))
        df = df.with_columns(ia_agi=pl.col("ia_agi") + divexc - own_cap)
    # `penexc` (pension exclusion) confirmed inert (gated on data(9)>0).
    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    if effective_year == 2009:
        df = df.with_columns(ia_agi=pl.col("ia_agi") + pl.min_horizontal(ui_total, 2400.0 * pl.col("ia_txp")))

    fedded = pl.col("fiitax").clip(0, None)
    dedbus = pl.lit(0.0)  # `comnew(181)` QBI gap - see module docstring.

    # --- Standard deduction ---
    pct = float(resolve_year(p["standard_deduction_pct"], effective_year))
    cap_single = float(resolve_year(p["standard_deduction_cap_single"], effective_year))
    cap_joint = float(resolve_year(p["standard_deduction_cap_married_joint"], effective_year))
    is_joint = pl.col("filing_status") == "married_joint"
    # `if(mst.eq.1.or.mst.eq.3.or.mst.eq.6) stded=...stnd(law,2)[single cap]
    # else stded=...stnd(law,3)[joint cap]` - `mst.eq.3` never fires for
    # this schema (real HoH internal code is 4), so HoH falls into the
    # ELSE branch and gets the JOINT cap, not single - the same "mst.eq.3
    # is dead" trap already caught building Georgia, just biting from the
    # opposite direction here (HoH gets the LARGER cap, not excluded from
    # it).
    gets_single_cap = pl.col("filing_status").is_in(["single", "married_separate"])
    stded = pl.when(gets_single_cap).then(
        (pct * pl.col("ia_agi")).clip(0, cap_single)
    ).otherwise((pct * pl.col("ia_agi")).clip(0, cap_joint))
    # 1984-1986 non-itemizer charitable-contribution addback: `data(58)/
    # (59)/(60)` (cash/asset contributions) confirmed inert - no-op.
    df = df.with_columns(ia_stded=stded)

    # --- Itemized deduction ---
    # `salt_capped` (comnew(30)'s own proptax+otheritem component) is a
    # federal.py-ONLY column (never exposed by federal_pre1987.py) -
    # reconstructed locally for years<=1986 from the raw proptax/
    # otheritem inputs PLUS `state_sales_or_income_tax_ded` itself (live-
    # probe-confirmed: comnew(30)=$16,105 for a $14,000-raw-itemized/
    # $2,112-state-tax 1977 case, not $14,000 - matching the same
    # "comnew(30) already bakes in the state tax, so it cancels out of
    # `xitded=comnew(30)-data(50)` entirely" pattern DC/GA/HI/Idaho's own
    # pre-1987 reconstructions already established, not a new discovery).
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
    # `law<=1985` political-contribution addback (`data(65)`) confirmed inert.
    if 2018 <= effective_year <= 2019:
        agix = pl.col("ia_agi").clip(0, None)
        # `cash`/`asset` (data(58)/(59)/(60)) confirmed inert -> char=0
        # always (see module docstring's itemized-worksheet simplification).
        deduc1 = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
        dlim1 = 0.03 * (pl.col("ia_agi") - phas92).clip(0, None)
        dlim2 = 0.8 * deduc1
        dedphs = pl.min_horizontal(dlim1, dlim2)
        df = df.with_columns(ia_xitded=(deduc1 - dedphs).clip(0, None))
    if effective_year >= 2020:
        df = df.with_columns(ia_xitded=salt_capped_plus_mortgage)

    df = df.with_columns(ia_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ia_xitded")))

    df = df.with_columns(ia_deduc=pl.max_horizontal(pl.col("ia_stded"), pl.col("ia_xitded")))

    # --- Taxable income --- (see module docstring point 2 for the real
    # double-counted `setax` add-back)
    df = df.with_columns(
        ia_yad=pl.col("ia_agi") + pl.col("ia_setax") - fedded - dedbus,
        ia_taxinc=(pl.col("ia_agi") + pl.col("ia_setax") - fedded - dedbus - pl.col("ia_deduc")).clip(0, None),
    )

    # --- Married filing combined --- (see module docstring point 3)
    wages = pl.col("pwages") + pl.col("swages")
    agih = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + 0.5 * (pl.col("ia_agi") - wages)
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
    df = df.with_columns(ia_taxinh=taxinh, ia_taxinw=taxinw, ia_agih=agih, ia_agiw=agiw)

    # --- Bracket tax ---
    aif = float(resolve_year(p["bracket_inflation_factor"], effective_year))
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
            (pl.col("ia_agih") - dedh_general + pl.col("ia_setax") * pl.col("ia_agih") / pl.col("ia_agi").clip(1e-9, None) - fedded * pl.col("ia_agih") / pl.col("ia_agi").clip(1e-9, None)).clip(0, None)
        ).otherwise(pl.lit(0.0))
        taxinw2 = pl.when(is_mfc).then(
            (pl.col("ia_agiw") - dedw_general + pl.col("ia_setax") * pl.col("ia_agiw") / pl.col("ia_agi").clip(1e-9, None) - fedded * pl.col("ia_agiw") / pl.col("ia_agi").clip(1e-9, None)).clip(0, None)
        ).otherwise(pl.lit(0.0))
        stath = bracket_tax(taxinh2 / aif, table) * aif
        statw = bracket_tax(taxinw2 / aif, table) * aif
        df = df.with_columns(
            ia_statax=pl.when(is_mfc).then(pl.min_horizontal(pl.col("ia_statax"), stath + statw)).otherwise(pl.col("ia_statax"))
        )
    else:
        stath = bracket_tax(pl.col("ia_taxinh") / aif, table) * aif
        statw = bracket_tax(pl.col("ia_taxinw") / aif, table) * aif
        df = df.with_columns(
            ia_statax=pl.when(is_mfc).then(pl.min_horizontal(pl.col("ia_statax"), stath + statw)).otherwise(pl.col("ia_statax"))
        )

    # --- Alternate Tax --- (never single)
    exy_other = float(resolve_year(p["exy_other"], effective_year))
    not_single = pl.col("filing_status") != "single"
    if effective_year <= 1997:
        rate_alt = float(p["alt_tax_rate_pre1998"][1960])
    else:
        rate_alt = float(resolve_year(p["alt_tax_rate_1998plus"], effective_year))
    if effective_year >= 1987:
        altax = (pl.col("ia_agi") - exy_other).clip(0, None) * rate_alt
        df = df.with_columns(
            ia_statax=pl.when(not_single).then(pl.min_horizontal(pl.col("ia_statax"), altax)).otherwise(pl.col("ia_statax"))
        )

    # --- Personal Exemption Credit ---
    xmp = float(resolve_year(p["personal_exemption_credit_amount"], effective_year))
    is_hoh = pl.col("filing_status") == "head_of_household"
    if effective_year <= 1994:
        gcred = (xmp + 5.0) * pl.col("ia_txp") + xmp * pl.col("depx")
        gcred = pl.when(is_hoh).then(gcred + xmp + 5.0).otherwise(gcred)
    elif effective_year <= 1997:
        gcred = (xmp / 2.0) * pl.col("ia_txp") + xmp * pl.col("depx")
        gcred = pl.when(is_hoh).then(gcred + xmp / 2.0).otherwise(gcred)
    else:
        gcred = xmp * (pl.col("depx") + pl.col("ia_txp"))
        gcred = pl.when(is_hoh).then(gcred + xmp).otherwise(gcred)
    # `data(34)` (dependent-filing-own-return addback) confirmed inert.
    df = df.with_columns(ia_gcred=gcred)
    df = df.with_columns(ia_statax=(pl.col("ia_statax") - pl.col("ia_gcred")).clip(0, None))

    # --- AMT ---
    if effective_year >= 1982:
        amt_federal = pl.col("amt") if "amt" in df.collect_schema().names() else pl.lit(0.0)
        if effective_year == 1982:
            alty = amt_federal * 0.25
        elif effective_year in (1983, 1984):
            alty = amt_federal * 0.7
        else:
            # `comnew(20)`/`comnew(97)` live-probe-confirmed $0; `data(81)`
            # confirmed inert; `comnew(34)` an acknowledged gap (see module
            # docstring) - treated as $0.
            addprf = pl.col("proptax")
            alminy = pl.col("ia_taxinc") + addprf
            if 1991 <= effective_year <= 2017:
                alminy = pl.when(over_thr).then(
                    alminy - pl.col("state_sales_or_income_tax_ded") * pl.col("itemized_deduction") / salt_capped_plus_mortgage.clip(1e-9, None)
                ).otherwise(alminy)
            exclnt_base = _by_status(p["amt_exclusion_base_by_status"])
            exclnt_thr = _by_status(p["amt_exclusion_phaseout_threshold_by_status"])
            exclnt = pl.when(alminy > exclnt_thr).then((exclnt_base - 0.25 * (alminy - exclnt_thr)).clip(0, None)).otherwise(exclnt_base)
            amt_rate_val = float(resolve_year(p["amt_rate"], effective_year))
            alty = (amt_rate_val * (alminy - exclnt) - pl.col("ia_statax")).clip(0, None)
        exy_single = float(resolve_year(p["exy_single"], effective_year))
        low_income_amt_exempt = (
            ((pl.col("filing_status") == "single") & (pl.col("ia_agi") <= exy_single))
            | (not_single & (pl.col("ia_agi") <= exy_other))
        )
        alty = pl.when(low_income_amt_exempt).then(0.0).otherwise(alty)
        df = df.with_columns(ia_statax=pl.col("ia_statax") + alty)

    # --- Child/Dependent Care Credit ---
    posagi = pl.col("ia_agi").clip(0, None)
    ccc_base = pl.col("ccc")
    if effective_year <= 1981:
        chcr = ccc_base * float(p["child_care_credit_flat_rate_pre1982"][1960])
    elif effective_year <= 1985:
        chcr = pl.col("childcare") * float(p["child_care_credit_flat_rate_1982_1985"][1960])
    elif effective_year <= 1989:
        chcr = ccc_base * float(p["child_care_credit_flat_rate_1986_1989"][1960])
    elif effective_year <= 1992:
        chcr = ccc_base * _tablki(pl.col("agi"), p["child_care_credit_table_1990_1992"])
    elif effective_year <= 2005:
        chcr = ccc_base * _tablki(posagi, p["child_care_credit_table_1993_2005"])
    else:
        chcr = ccc_base * _tablki(posagi, p["child_care_credit_table_2006plus"])
    df = df.with_columns(ia_chcr=chcr)

    # --- EITC ---
    earncr = pl.lit(0.0)
    if effective_year >= 1990:
        rate_eitc = float(resolve_year(p["eitc_rate"], effective_year))
        cap_eitc = float(resolve_year(p["eitc_agi_cap"], effective_year))
        earncr = pl.when(pl.col("agi") < cap_eitc).then(rate_eitc * pl.col("eitc")).otherwise(0.0)

        if effective_year == 2009:
            nkid = pl.col("depx")
            xlin2 = (
                pl.when(nkid == 0).then(10590.0)
                .when((nkid >= 1) & (nkid <= 2)).then(19540.0)
                .otherwise(16420.0)
            )
            xlin3 = (pl.col("earned_income") - xlin2).clip(0, None)
            xlin5 = (
                pl.when(nkid == 0).then((457.0 - xlin3 * 0.0765).clip(0, None))
                .when(nkid == 1).then((3043.0 - xlin3 * 0.1598).clip(0, None))
                .otherwise((5028.0 - xlin3 * 0.2106).clip(0, None))
            )
            earncr = pl.when(is_joint & (earncr > 0)).then(0.07 * xlin5).otherwise(earncr)

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
    exy_single = float(resolve_year(p["exy_single"], effective_year))
    is_single = pl.col("filing_status") == "single"
    df = df.with_columns(
        ia_statax=pl.when(is_single).then(
            pl.max_horizontal(pl.min_horizontal(pl.col("ia_agi") - exy_single, pl.col("ia_statax")), 0.0)
        ).otherwise(pl.col("ia_statax"))
    )
    if effective_year >= 2007:
        df = df.with_columns(ia_statax=pl.col("ia_statax") - pl.col("ia_earncr") - pl.col("ia_chcr"))

    if effective_year >= 1992:
        exy_other = float(resolve_year(p["exy_other"], effective_year))
        low_income = pl.when(is_single).then(pl.col("agi") <= exy_single).otherwise(pl.col("agi") <= exy_other)
        df = df.with_columns(
            ia_statax=pl.when((pl.col("ia_statax") > 0) & low_income).then(0.0).otherwise(pl.col("ia_statax"))
        )

    df = df.with_columns(siitax=pl.col("ia_statax") * flate)
    return df
