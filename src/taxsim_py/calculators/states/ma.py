"""Massachusetts individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.inputs import aged_count, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ma" / "income_tax.yaml")

def _p(name: str, year: int) -> float:
    return float(resolve_year(MA_PARAMS[name], year))


def compute_ma_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(df, ("dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "childcare", "psemp", "ssemp",
                "depx", "dep13", "dep18", "eitc", "earned_income", "ltg", "taxable_unemployment", "pensions",
                "gssi", "taxable_social_security", "transfers", "rentpaid", "proptax", "otherprop", "nonprop",
                "scorp", "pbusinc", "pprofinc", "sbusinc", "sprofinc"))

    df = df.with_columns(ma_untax=pl.col("taxable_unemployment"))
    # Payroll figures come from the federal run: the real year's wages and
    # rates, never deflated (TAXSIM's `sstax` call here writes the shared
    # federal block, not the state's deflated copy).
    payroll = payroll_parts(year)
    # `comnew(183)`: the primary earner's payroll tax as TAXSIM credits the
    # taxpayer.
    df = df.with_columns(ma_c183=payroll["own_fica_primary"], ma_setax=payroll["setax"])
    # Federal Schedule E income (`comnew(8)`): other property income, plus S
    # corporation income from 1987.
    schede = pl.col("otherprop") + (pl.col("scorp") if y >= 1987 else 0.0)
    df = df.with_columns(ma_schede=schede)

    df = deflate_for_extrapolation(
        df,
        flate,
        ["pwages", "swages", "psemp", "ssemp", "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui",
         "childcare", "agi", "earned_income", "eitc", "ltg", "ma_untax", "pensions", "gssi",
         "taxable_social_security", "transfers", "rentpaid", "proptax", "nonprop", "ma_schede"],
    )

    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"
    is_sep = pl.col("filing_status") == "married_separate"
    is_single = pl.col("filing_status") == "single"
    not_sep = ~is_sep
    n_tp = taxpayer_count()
    x_flag = pl.lit(1.0) if y >= 1985 else pl.when(is_joint).then(1.0).otherwise(0.0)

    # Negative wages fold into `data(17)` rather than `data(85)/(86)`.
    w1 = pl.col("pwages").clip(0, None)
    w2 = pl.col("swages").clip(0, None)
    wages = w1 + w2  # `data(11)`
    semp = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
    # Business income is not deflated in projected years.
    business_h = pl.col("pbusinc") + pl.col("pprofinc")
    business_w = pl.col("sbusinc") + pl.col("sprofinc")
    d17 = semp + business_h + business_w
    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))  # `data(82)`

    # --- Exemptions ---
    xmps = _p("personal_exemption", y)
    xmplim = _p("married_exemption_cap", y)
    if y <= 1986:
        spy = pl.min_horizontal(w1, w2)
        txp_joint = pl.min_horizontal(xmps + spy + _p("married_additional_exemption_base", y), pl.lit(xmplim))
    else:
        txp_joint = pl.lit(min(2.0 * xmps, xmplim))
    txp = pl.when(is_joint).then(txp_joint).otherwise(xmps)
    if y >= 1994:
        txp = pl.when(is_hoh).then(_p("hoh_exemption_1994plus", y)).otherwise(txp)
    ageex = aged_count() * _p("aged_exemption", y)
    depex = pl.col("depx") * _p("dependent_exemption", y)
    exemp = depex + ageex + txp

    # --- Part B income ---
    b1inc = wages + d17
    b2inc = pl.col("pensions")
    untax = pl.col("ma_untax")
    if 1982 <= y <= 1989:
        b2inc = b2inc + untax
    if y <= 1989:
        b1inc = b1inc + pl.col("ma_schede")
        binc = b1inc + b2inc
    else:
        binc = b1inc + b2inc + pl.col("ma_schede") + untax
        if y == 2009:
            binc = binc - untax + ui_total
        if y >= 2020:
            nsize = pl.when(is_joint).then(2.0).otherwise(1.0) + pl.col("depx").floor()
            ulevel = 2.0 * _p("ui_exclusion_poverty_level", y) * nsize
            hyun = pl.col("agi") + pl.col("gssi") - pl.col("taxable_social_security") + ui_total - untax
            cap = _p("ui_exclusion_cap_per_person", y)
            unded = pl.when(hyun <= ulevel).then(
                pl.min_horizontal(ui_total - pl.col("sui"), pl.lit(cap)) + pl.min_horizontal(pl.col("sui"), pl.lit(cap))
            ).otherwise(0.0)
            binc = pl.when(ui_total > 0).then(binc + ui_total - untax - unded).otherwise(binc)
        if y >= 1999:
            binc = binc + (pl.col("intrec") - 100.0 * n_tp).clip(0, None)

    # --- Part B deductions ---
    # Payroll tax and Social Security benefits (`comnew(84)`), each up to $2,000.
    fica = pl.min_horizontal(pl.col("ma_c183"), pl.lit(2000.0)) + pl.min_horizontal(pl.col("gssi").clip(0, None), pl.lit(2000.0))
    setax = pl.col("ma_setax")

    ndep13 = pl.col("dep13").clip(None, 2.0).floor()
    d209 = pl.col("depx") - pl.col("dep18")
    ndep = (d209 + ndep13).clip(None, 2.0).floor()
    if y <= 1996:
        ch = pl.lit(600.0)
    elif y <= 2000:
        ch = pl.lit(1200.0)
    elif y == 2001:
        ch = 2400.0 * ndep13
    elif y <= 2020:
        ch = 3600.0 * ndep13
    else:
        ch = pl.lit(0.0)
    if y == 1982:
        ch = pl.min_horizontal(ch, 2000.0 * pl.col("dep13"))
    ch = pl.when(is_sep).then(0.0).otherwise(ch)
    ch1 = pl.lit(0.0)
    chcred = pl.lit(0.0)
    if y < 2010:
        ch1 = pl.min_horizontal(pl.col("childcare"), 2400.0 * ndep13)
    elif y == 2010:
        ch1 = pl.min_horizontal(pl.col("childcare"), 4800.0 * ndep13)
    elif y >= 2021:
        chcred = pl.min_horizontal(pl.col("childcare"), 240.0 * ndep13)
    earned = pl.col("earned_income")
    # A joint return splits earnings by spouse, with self-employment tax
    # shared by business income.
    setaxh = setax * business_h / d17
    setaxw = setax * business_w / d17
    earnww_se = (semp - 0.5 * (setax - setaxh - setaxw)).clip(0, None)
    earnh = pl.when(d17 > 0).then(
        (w1 + business_h - 0.5 * setaxh).clip(0, None) + earnww_se / 2
    ).otherwise(w1 + (earned - w1 - w2).clip(0, None) / 2)
    earnw = pl.when(d17 > 0).then(
        (w2 + business_w - 0.5 * setaxw).clip(0, None) + earnww_se / 2
    ).otherwise(w2 + (earned - w1 - w2).clip(0, None) / 2)
    ch1 = pl.when(is_joint).then(pl.min_horizontal(ch1, earnh, earnw)).otherwise(pl.min_horizontal(ch1, earned)).clip(0, None)
    chcred = pl.when(is_joint).then(pl.min_horizontal(chcred, earnh, earnw)).otherwise(pl.min_horizontal(chcred, earned)).clip(0, None)
    ch = pl.max_horizontal(ch, ch1)
    has_dep = ndep > 0
    ch = pl.when(has_dep).then(ch).otherwise(0.0)
    chcred = pl.when(has_dep).then(chcred).otherwise(0.0)

    # Half of rent paid, from 1981.
    rnt = pl.lit(0.0)
    if y >= 1981:
        rnt = float(MA_PARAMS["rent_deduction_share"]) * pl.col("rentpaid")
        if y >= 2001:
            rnt = pl.min_horizontal(rnt, _p("rent_deduction_cap", y) / pl.when(is_sep).then(2.0).otherwise(1.0))
        elif y >= 1982:
            rnt = pl.min_horizontal(rnt, _p("rent_deduction_cap", y))
            if y >= 1997:
                rnt = pl.when(is_sep).then(
                    pl.min_horizontal(rnt, float(MA_PARAMS["rent_deduction_cap_separate_1997_2000"]))
                ).otherwise(rnt)
    # Non-property income enters as a negative deduction (`data(30)`).
    bded = fica + ch + rnt - pl.col("nonprop")
    df = df.with_columns(
        _ma_binc=binc,
        _ma_bded=bded,
        _ma_exemp=exemp,
        _ma_chcred=chcred,
        _ma_ndep=ndep,
        _ma_b1inc=b1inc,
        _ma_b2inc=b2inc,
    )
    binc = pl.col("_ma_binc")
    bded = pl.col("_ma_bded")
    exemp = pl.col("_ma_exemp")
    chcred = pl.col("_ma_chcred")
    ndep = pl.col("_ma_ndep")
    b1inc = pl.col("_ma_b1inc")
    b2inc = pl.col("_ma_b2inc")
    tbinc1 = (binc - bded).clip(0, None)
    tbinc2 = (tbinc1 - exemp).clip(0, None)

    # --- Part A income ---
    stcg = pl.col("stcg")
    ltcg = pl.col("ltcg")
    divs = pl.col("dividends")
    intrec = pl.col("intrec")
    if y <= 1996:
        gnx = _p("capital_gains_exclusion", y)
        if y <= 1982:
            cg = ltcg - (ltcg * gnx).clip(0, None) + stcg
        else:
            cg = (ltcg + stcg) - ((ltcg + stcg) * gnx).clip(0, None)
        cfd = pl.when(cg < 0).then(pl.min_horizontal(pl.lit(1000.0), -cg)).otherwise(0.0)
        cg = cg.clip(0, None)
        ainc = cg + (intrec + divs - cfd).clip(0, None)
    elif y <= 1998:
        ainc = divs + intrec
    else:
        ainc = stcg.clip(0, None)

    binc_for_agi = binc
    b1_1989 = b2_1989 = None
    if y <= 1988:
        binc_mod = (binc - bded).clip(0, None)
        xtra = (exemp - binc_mod).clip(0, None)
        binc_for_agi = (binc_mod - exemp).clip(0, None)
        ainc = (ainc - x_flag * xtra).clip(0, None)
    elif y == 1989:
        loss1 = b1inc.clip(None, 0)
        loss2 = b2inc.clip(None, 0)
        b1 = (b1inc + loss2).clip(0, None)
        xded = pl.min_horizontal(bded, bded - b1).clip(0, None)
        b1 = (b1 - bded).clip(0, None)
        xex = (exemp - b1).clip(0, None)
        b1 = (b1 - exemp).clip(0, None)
        b2 = (b2inc + loss1).clip(0, None)
        b2 = (b2 - xded).clip(0, None)
        xex2 = (xex - b2).clip(0, None)
        b2 = (b2 - xex).clip(0, None)
        ainc = (ainc - x_flag * xex2).clip(0, None)
        b1_1989, b2_1989 = b1, b2
    elif y <= 1998:
        bagi = binc - bded
        exded = pl.when((bagi < exemp) & not_sep).then(exemp - bagi).otherwise(0.0)
        ainc = (ainc - exded).clip(0, None)

    binc1 = pl.lit(0.0)
    tbinc3 = pl.lit(0.0)
    cinc = pl.lit(0.0)
    if y >= 1999:
        divrec = divs
        capgn = stcg + ltcg
        capgn = pl.when(capgn >= 0).then(capgn).otherwise(
            pl.max_horizontal(capgn, pl.when(is_sep).then(-1500.0).otherwise(-3000.0))
        )  # `comnew(6)`: net gain actually in federal AGI
        xlin4 = (pl.min_horizontal(divrec, pl.lit(2000.0)) - (-stcg.clip(None, 0))).clip(0, None)
        binc1 = pl.when(capgn >= 0).then(divrec).otherwise(divrec - pl.min_horizontal(xlin4, capgn.abs()))
        excxmp = pl.when((binc - bded < exemp) & not_sep).then(exemp - (binc - bded).clip(0, None)).otherwise(0.0)

        # MA Schedule B (Part 1-4) and Schedule D worksheet, as transcribed.
        xl20 = pl.when(stcg < 0).then(pl.min_horizontal(pl.lit(2000.0), divrec, -stcg)).otherwise(0.0)
        xl21 = stcg + xl20
        xl22 = pl.when((stcg < 0) & (xl21 < 0) & (ltcg > 0)).then(pl.min_horizontal(-xl21, ltcg)).otherwise(0.0)
        xl24 = stcg.clip(0, None)
        xl25 = pl.when((stcg > 0) & (ltcg < 0)).then(pl.min_horizontal(stcg, -ltcg)).otherwise(0.0)
        xl28 = xl24 - xl25
        xl31 = divrec - xl20
        dxl15 = pl.when(ltcg >= 0).then(ltcg - xl22).otherwise(ltcg + xl22)
        xl32 = pl.when((xl31 > 0) & (dxl15 < 0)).then(
            pl.min_horizontal(
                (pl.min_horizontal(pl.lit(2000.0), divrec) - xl20).clip(0, None),
                (ltcg - xl22).clip(None, 0).abs(),
            )
        ).otherwise(0.0)
        xl35 = xl31 - xl32 + xl28
        xl37 = (xl35 - excxmp).clip(0, None)
        xl38 = pl.when(xl37 >= divs).then(divrec).otherwise(xl37)
        tbinc3 = xl38
        ainc = xl37 - xl38

        dxl16 = pl.when(dxl15 < 0).then(
            pl.min_horizontal(
                (pl.min_horizontal(divs, pl.lit(2000.0)) - (-stcg.clip(None, 0))).clip(0, None),
                (-ltcg.clip(None, 0)),
            )
        ).otherwise(0.0)
        dxl19 = (dxl16 + dxl15).clip(0, None)
        wdxl1 = xl35.clip(0, None)
        wdxl4 = exemp - tbinc1
        wdxl6 = wdxl4 - pl.min_horizontal(wdxl1, wdxl4)
        dxl20 = pl.when((wdxl4 <= 0) | (wdxl6 <= 0)).then(0.0).otherwise(pl.min_horizontal(dxl19, wdxl6))
        cinc = (dxl19 - dxl20).clip(0, None)
    elif y >= 1997:
        cinc = pl.col("ltg")  # `comnew(15)` (1987+ federal `ltg`)

    df = df.with_columns(
        _ma_binc_for_agi=binc_for_agi,
        _ma_binc1=binc1,
        _ma_tbinc2=tbinc2,
        _ma_tbinc3=tbinc3,
        _ma_ainc=ainc,
        _ma_cinc=cinc,
    )
    binc_for_agi = pl.col("_ma_binc_for_agi")
    binc1 = pl.col("_ma_binc1")
    tbinc2 = pl.col("_ma_tbinc2")
    tbinc3 = pl.col("_ma_tbinc3")
    ainc = pl.col("_ma_ainc")
    cinc = pl.col("_ma_cinc")

    taxbin = tbinc2 + tbinc3
    rate_b = _p("part_b_rate", y)
    if y == 1989:
        statxb = 0.05 * b2_1989 + rate_b * b1_1989.clip(0, None)
    else:
        statxb = rate_b * taxbin
    statxa = _p("part_a_rate", y) * ainc
    statxc = _p("part_c_rate", y) * cinc.clip(0, None) if y >= 1997 else pl.lit(0.0)
    surtax = _p("surtax", y)
    if y == 1984:
        statxb = pl.when(binc_for_agi > 60000.0).then(surtax * statxb).otherwise(statxb)
        statxa = pl.when(ainc > 60000.0).then(surtax * statxa).otherwise(statxa)
        pretax = statxa + statxb
    else:
        pretax = (statxa + statxb + statxc) * surtax

    # --- Massachusetts AGI and No Tax Status / Limited Income Credit ---
    ma_agi = (binc_for_agi + binc1 + stcg + ltcg + pl.min_horizontal(intrec, 100.0 * n_tp)).clip(0, None)
    df = df.with_columns(_ma_pretax=pretax, _ma_agi=ma_agi)
    pretax = pl.col("_ma_pretax")
    ma_agi = pl.col("_ma_agi")
    ntscr = pl.lit(0.0)
    txcr = pl.lit(0.0)
    scred = pl.lit(0.0)
    if 1984 <= y <= 1994:
        nts1 = _p("no_tax_status_single", y)
        nts2 = _p("no_tax_status_joint", y)
        s1 = is_single | is_hoh
        ntscr = pl.when(s1 & (ma_agi <= nts1)).then(pretax).when(is_joint & (ma_agi <= nts2)).then(pretax).otherwise(0.0)
        txcr = (
            pl.when(s1 & (ma_agi > nts1) & (ma_agi <= 14000.0) & (pretax >= 0.1 * (ma_agi - nts1)))
            .then((pretax - 0.1 * (ma_agi - nts1)).clip(0, None))
            .when(is_joint & (ma_agi > nts2) & (ma_agi <= 21000.0))
            .then((pretax - 0.1 * (ma_agi - nts2)).clip(0, None))
            .otherwise(0.0)
        )
    elif y >= 1995:
        nts1 = _p("no_tax_status_single", y)
        nts2 = _p("no_tax_status_joint", y)
        ntsr = pl.when(is_joint).then(nts2 + _p("no_tax_status_married_addon", y)).otherwise(nts2)
        licrr = pl.when(is_joint).then(_p("limited_income_credit_limit_married", y)).otherwise(
            _p("limited_income_credit_limit_hoh", y)
        )
        numdep = pl.col("depx").floor()
        s2 = is_joint | is_hoh
        lo = ntsr + numdep * 1000.0
        hi = licrr + numdep * 1750.0
        ntscr = pl.when(is_single & (ma_agi <= nts1)).then(pretax).when(s2 & (ma_agi <= lo)).then(pretax).otherwise(0.0)
        txcr = (
            pl.when(is_single & (ma_agi > nts1) & (ma_agi <= 14000.0))
            .then((pretax - 0.1 * (ma_agi - nts1)).clip(0, None))
            .when(s2 & (ma_agi > lo) & (ma_agi <= hi))
            .then((pretax - 0.1 * (ma_agi - lo)).clip(0, None))
            .otherwise(0.0)
        )
    else:
        scred = 8.0 * n_tp + 4.0 * pl.col("depx")

    statax = (pretax - (ntscr + txcr + scred)).clip(0, None)
    if y >= 1997:
        statax = statax - _p("eitc_rate", y) * pl.col("eitc")
    if y >= 2021:
        statax = statax - pl.max_horizontal(chcred, ndep * 180.0)

    # Senior circuit breaker credit (2001+, refundable).
    cbcred = pl.lit(0.0)
    if y >= 2001:
        limits = MA_PARAMS["circuit_breaker_income_limit"]
        hymax = (
            pl.when(is_joint).then(float(resolve_year(limits["married_joint"], y)))
            .when(is_hoh).then(float(resolve_year(limits["head_of_household"], y)))
            .when(is_single).then(float(resolve_year(limits["single"], y)))
            .otherwise(0.0)
        )
        ptax = pl.max_horizontal(pl.col("proptax"), float(MA_PARAMS["circuit_breaker_rent_share"]) * pl.col("rentpaid"))
        hycb = (ma_agi + pl.col("gssi") + pl.col("transfers") - ageex - depex).clip(0, None)
        cbcred = pl.when(hycb <= hymax).then((ptax - float(MA_PARAMS["circuit_breaker_income_share"]) * hycb).clip(0, None)).otherwise(0.0)
        cbcred = pl.min_horizontal(cbcred, _p("circuit_breaker_max", y))
        cbcred = pl.when(not_sep & (aged_count() > 0)).then(cbcred).otherwise(0.0)
        statax = statax - cbcred

    earncr = _p("eitc_rate", y) * pl.col("eitc") if y >= 1997 else pl.lit(0.0)
    dependent_credit = pl.max_horizontal(chcred, ndep * 180.0) if y >= 2021 else pl.lit(0.0)
    state_chcr = pl.when(chcred > ndep * 180.0).then(chcred).otherwise(0.0) if y >= 2021 else pl.lit(0.0)
    result = with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=ma_agi,
        exemptions=exemp,
        taxable_income=taxbin + ainc + cinc - fica,
        child_care_credit=state_chcr,
        eic=earncr,
        credits=ntscr + txcr + scred + earncr + dependent_credit + cbcred,
        rate=rate_b,
    )
    return result.drop(
        "_ma_binc",
        "_ma_bded",
        "_ma_exemp",
        "_ma_chcred",
        "_ma_ndep",
        "_ma_b1inc",
        "_ma_b2inc",
        "_ma_binc_for_agi",
        "_ma_binc1",
        "_ma_tbinc2",
        "_ma_tbinc3",
        "_ma_ainc",
        "_ma_cinc",
        "_ma_pretax",
        "_ma_agi",
    )
