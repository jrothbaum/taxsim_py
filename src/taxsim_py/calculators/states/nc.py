"""North Carolina individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    by_filing_status,
    checkpoint,
    dividend_exclusion_addback,
    interpolate_table,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NC_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nc" / "income_tax.yaml")


def _step_down(value: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """Value of the first `[upper, amount]` row whose upper bound is at least `value`, else 0."""
    result = pl.lit(0.0)
    for upper, amount in reversed(rows):
        result = pl.when(value <= upper).then(float(amount)).otherwise(result)
    return result


def _look_1977(income: pl.Expr) -> pl.Expr:
    return bracket_tax(income.clip(0, None), NC_PARAMS["brackets_1977"])


def _rate_1977(income: pl.Expr) -> pl.Expr:
    return bracket_rate(income.clip(0, None), NC_PARAMS["brackets_1977"])


def _pre1989(df: pl.DataFrame, y: int, agi: pl.Expr) -> tuple[pl.DataFrame, pl.Expr, pl.Expr]:
    """Tax through 1988; returns the frame and the joint spouses' separate taxes."""
    p = YearParams(NC_PARAMS, y)
    is_joint = files_joint()
    depx = pl.col("depx")
    agi_joint = agi
    ag = agi.clip(0, None)
    charity = pl.min_horizontal(pl.col("charity_cash"), p["charity_limit_share_pre1989"] * ag)
    xitded = charity + pl.col("proptax") + pl.col("mortgage")
    ch = pl.when(depx >= 2).then(2.0).when(depx > 0).then(1.0).otherwise(0.0)
    if y <= 1978:
        xitded = xitded + pl.col("childcare").clip(None, p["childcare_deduction_cap_1977_1978"])
    elif y <= 1980:
        xitded = xitded + pl.min_horizontal(pl.col("childcare"), ch * p["childcare_deduction_per_child_1979_1980"])
    std_cap = p.num("standard_deduction_cap_pre1989")
    std_rate = p["standard_deduction_rate_pre1989"]
    xmp = p.num("exemption_taxpayer")
    xmp2 = p.num("exemption_additional")
    xmpc = p.num("exemption_dependent")
    df, (xitded,) = checkpoint(df, nc_xitded=xitded)

    # Joint returns: each spouse files separately on a share of AGI.
    pw = pl.col("pwages").clip(0, None)
    sw = pl.col("swages").clip(0, None)
    df, (yh,) = checkpoint(df, nc_yh=pl.min_horizontal(agi, pl.max_horizontal(pw, sw) + (agi - pw - sw) / 2.0))
    yw = agi - yh
    if 1980 <= y <= 1982:
        exclusion = pl.min_horizontal(pl.lit(p["interest_exclusion_1980_1982"]), pl.col("intrec") / 2.0)
        yh = (yh - exclusion).clip(0, None)
        yw = (yw - exclusion).clip(0, None)
    df, (yh, yw) = checkpoint(df, nc_yh_net=yh, nc_yw_net=yw)
    # TAXSIM reuses the child-count variable for the husband's share of
    # dependents, so the joint child care credit counts round(depx / 2).
    joint_ch = (depx * 0.5 + 0.5).floor()
    cw = depx - joint_ch
    # Each spouse 65 or older gets an extra exemption, the husband's first.
    aged = aged_count()
    oh = (aged >= 1).cast(pl.Float64)
    ow = (aged >= 2).cast(pl.Float64)
    eh = xmp2 + oh * xmp + joint_ch * xmpc
    ew = xmp + ow * xmp + cw * xmpc
    sth = pl.min_horizontal(std_rate * yh, pl.lit(std_cap))
    stw = pl.min_horizontal(std_rate * yw, pl.lit(std_cap))
    use_itemized = xitded > sth + stw
    dedh = pl.when(use_itemized).then(0.5 * xitded).otherwise(sth)
    dedw = pl.when(use_itemized).then(0.5 * xitded).otherwise(stw)
    df, (taxyh, taxyw) = checkpoint(
        df, nc_taxyh=(yh - dedh - eh).clip(0, None), nc_taxyw=(yw - dedw - ew).clip(0, None)
    )
    df, (staxh, staxw) = checkpoint(df, nc_staxh=_look_1977(taxyh), nc_staxw=_look_1977(taxyw))

    # Other returns.
    if 1980 <= y <= 1982:
        agi = (agi - pl.col("intrec").clip(None, p["interest_exclusion_1980_1982"])).clip(0, None)
    stded = pl.min_horizontal(std_rate * agi.clip(0, None), pl.lit(std_cap))
    exemp = xmp + aged * xmp2 + depx * xmpc
    single_taxinc = (agi - pl.max_horizontal(stded, xitded) - exemp).clip(0, None)
    df, (single_agi, single_taxinc) = checkpoint(df, nc_single_agi=agi, nc_single_taxinc=single_taxinc)
    single_tax = _look_1977(single_taxinc)

    # Low income credit 1986-1988.
    lcred = pl.lit(0.0)
    if 1986 <= y <= 1988:
        rows = p["low_income_credit"]
        single_tax = (single_tax - _step_down(single_agi, rows)).clip(0, None)
        staxh = (staxh - _step_down(yh, rows)).clip(0, None)
        staxw = (staxw - _step_down(yw, rows)).clip(0, None)
        lcred = pl.when(is_joint).then(_step_down(yh, rows) + _step_down(yw, rows)).otherwise(_step_down(single_agi, rows))
    df, (single_tax, staxh, staxw) = checkpoint(df, nc_single_tax=single_tax, nc_staxh_lc=staxh, nc_staxw_lc=staxw)

    df = df.with_columns(
        nc_taxinc=pl.when(is_joint).then(taxyh + taxyw).otherwise(single_taxinc),
        nc_statax=pl.when(is_joint).then(staxh + staxw).otherwise(single_tax),
        nc_ch=pl.when(is_joint).then(joint_ch).otherwise(ch),
        # Joint returns report no standard deduction or exemptions and the
        # average of the spouses' rates.
        nc_detail_agi=pl.when(is_joint).then(agi_joint).otherwise(single_agi),
        nc_detail_stded=pl.when(is_joint).then(0.0).otherwise(stded),
        nc_detail_xitded=xitded,
        nc_detail_exemp=pl.when(is_joint).then(0.0).otherwise(exemp),
        nc_detail_lcred=lcred,
        nc_detail_rate=pl.when(is_joint).then((_rate_1977(taxyh) + _rate_1977(taxyw)) / 2.0).otherwise(
            _rate_1977(single_taxinc)
        ),
    )
    return df, staxh, staxw


def compute_nc_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate North Carolina income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(NC_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = df.with_columns(nc_ui=unemployment_total())
    df = deflate_for_extrapolation(df, flate, extra=("nc_ui",))

    is_single = files_single()
    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    fed_agi = pl.col("agi")
    ss_taxable = pl.col("taxable_social_security")
    # Retirement income deduction through 2013: $2,000 per taxpayer (per
    # taxpayer 65 or older when there is one).
    pens = pl.lit(0.0)
    if y <= 2013:
        pens = pl.when(pl.col("pensions") > 0).then(pl.when(aged > 0).then(aged).otherwise(txp)).otherwise(0.0)
    pension_deduction = pl.min_horizontal(pl.col("pensions"), float(p["retirement_deduction_per_taxpayer"]) * pens)

    # --- North Carolina AGI (used through 1988) ---
    agi = fed_agi
    if y <= 1986:
        # `data(12) - divall`, floored at 0. In 1981 `divall` nets the joint
        # dividend and interest exclusion, so interest can use it all up.
        dividend_addback = dividend_exclusion_addback(y)
        if y == 1981:
            dividends = pl.col("dividends") + dividend_adjustment
            limit = by_filing_status(
                {s: resolve_year(v, y) for s, v in PRE1987_PARAMS["dividend_exclusion"].items()}
            )
            divall = (dividends + pl.col("intrec") - limit).clip(0, None)
            dividend_addback = (dividends - divall).clip(0, None)
        fullcg = pl.col("stcg") + pl.col("ltcg")
        agi = (
            agi + (fullcg - pl.col("pre1987_capgn")).clip(0, None)
            + dividend_addback
            + pl.col("nc_ui") - pl.col("taxable_unemployment")
        )
        if y >= 1983:
            agi = agi + pl.col("pre1987_twoded")
    if y >= 1984:
        agi = agi - ss_taxable
    df, (agi,) = checkpoint(df, nc_agi=agi)

    if y <= 1988:
        df, staxh, staxw = _pre1989(df, y, agi)
    else:
        # --- Standard deduction ---
        std = p["standard_deduction"]
        stded = (
            pl.when(is_single).then(float(resolve_year(std["single"], y)))
            .when(is_hoh).then(float(resolve_year(std["head_of_household"], y)))
            .otherwise(float(resolve_year(std["married"], y)) / sep)
        )
        if y <= 2013:
            aged_add = p["standard_deduction_aged_addition"]
            stded = stded + aged * pl.when(is_joint | is_sep).then(float(aged_add["married"])).otherwise(
                float(aged_add["single"])
            )
        if 1994 <= y <= 2013:
            earned = pl.col("earned_income") + p.num("dependent_standard_deduction_earned_addition")
            limit = pl.max_horizontal(pl.lit(float(p["dependent_standard_deduction_floor"])), earned)
            stded = pl.when(is_dependent_filer()).then(pl.min_horizontal(stded, limit)).otherwise(stded)
        fed_itemizes = pl.col("itemizes")
        fed_deduc = pl.col("itemized_deduction")
        fed_zbr = pl.when(fed_itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
        salt = pl.col("state_sales_or_income_tax_ded")
        # --- Deduction difference from federal (through 2011) or deduction (2012+) ---
        if y <= 2011:
            deduc = pl.when(fed_itemizes).then(
                pl.min_horizontal(salt, (fed_deduc - stded).clip(0, None))
            ).otherwise((fed_zbr - stded).clip(0, None))
        elif y <= 2013:
            deduc = pl.when(fed_itemizes).then(
                fed_deduc - pl.min_horizontal(salt, (fed_deduc - stded).clip(0, None))
            ).otherwise(stded)
        else:
            xitded = (
                pl.min_horizontal(pl.lit(p["mortgage_property_tax_cap_2014plus"]), pl.col("mortgage") + pl.col("proptax"))
                + pl.col("charity_cash")
            )
            deduc = pl.max_horizontal(stded, xitded)

        # --- Exemptions ---
        # The federal exemption count is deflated with dollar amounts when a
        # later year is projected.
        exemps = federal_exemption_count(y) / flate
        exemp = pl.lit(0.0)
        if 1990 <= y <= 1994:
            exemp = exemps * p.num("exemption_1990_1994")
        elif 1995 <= y <= 2013:
            amex = pl.col("personal_exemptions")
            addition_b = p.num("exemption_addition_b")
            phaseout = by_filing_status(p["exemption_phaseout_agi"])
            fedxf = exemps * p.num("federal_exemption_amount")
            above = exemps * addition_b
            if y != 2010:
                above = pl.when(fedxf > amex).then(addition_b * amex / p.num("federal_exemption_amount")).otherwise(above)
                if 2006 <= y <= 2009:
                    fedphl = by_filing_status(p["exemption_partial_phaseout_start"][y])
                    partial = (fed_agi > fedphl) & (fed_agi - fedphl <= p["exemption_partial_phaseout_width"] / sep)
                    above = pl.when((fedxf > amex) & partial).then(
                        p.num("exemption_partial_share") * exemps * addition_b
                    ).otherwise(above)
            exemp = pl.when(fed_agi > phaseout).then(above).otherwise(exemps * p.num("exemption_addition_a"))
        df, (deduc,) = checkpoint(df, nc_deduc=deduc + exemp)
        # Reported AGI nets taxable Social Security and the pension deduction.
        detail_agi = fed_agi - ss_taxable - pension_deduction
        if y == 2020:
            detail_agi = detail_agi + pl.col("nc_ui") - pl.col("taxable_unemployment") + pl.when(fed_itemizes).then(
                0.0
            ).otherwise(pl.col("charity_cash").clip(None, p["charity_nonitemizer_addback_2020"]))
        if y <= 2011:
            detail_xitded = pl.when(fed_itemizes).then(deduc - exemp).otherwise(0.0)
            detail_exemp = pl.col("personal_exemptions") - exemp
        elif y <= 2013:
            detail_xitded = pl.when(fed_itemizes).then(deduc - exemp).otherwise(0.0)
            detail_exemp = exemp
        else:
            detail_xitded = xitded
            detail_exemp = pl.lit(0.0)

        # --- Taxable income ---
        fed_taxable = fed_agi - pl.when(fed_itemizes).then(fed_deduc).otherwise(fed_zbr) - pl.col("personal_exemptions")
        if y <= 2011:
            taxinc = (fed_taxable + deduc - ss_taxable - pension_deduction).clip(0, None)
        else:
            adjbus = pl.lit(0.0)
            if y <= 2013:
                adjbus = pl.min_horizontal(
                    txp * p["business_income_deduction_2012_2013"],
                    (pl.col("psemp") + pl.col("ssemp")).clip(0, None),
                )
            nc_agi = fed_agi - ss_taxable - pension_deduction
            if y == 2020:
                nc_agi = nc_agi + pl.col("nc_ui") - pl.col("taxable_unemployment") + pl.when(fed_itemizes).then(0.0).otherwise(
                    pl.col("charity_cash").clip(None, p["charity_nonitemizer_addback_2020"])
                )
            taxinc = (nc_agi - adjbus - deduc).clip(0, None)
        if y >= 2018:
            table = p["child_deduction"]
            fed_agix = fed_agi.clip(0, None)
            per_child = (
                pl.when(is_joint).then(interpolate_table(fed_agix, table["married_joint"]))
                .when(is_hoh).then(interpolate_table(fed_agix, table["head_of_household"]))
                .otherwise(interpolate_table(fed_agix, table["single"]))
            )
            taxinc = (taxinc - per_child * pl.col("dep17")).clip(0, None)
        df, (taxinc,) = checkpoint(df, nc_taxinc=taxinc)

        # --- Tax ---
        if y >= 2014:
            statax = p.num("flat_rate") * taxinc
            rate = pl.lit(0.0)
        else:
            tables = p.value("brackets")
            single_tax = bracket_tax(taxinc, tables["single"])
            hoh_tax = bracket_tax(taxinc, tables["head_of_household"])
            if y <= 1990:
                # Joint returns: the lesser of income splitting and the
                # spouses' separate shares, on the married table.
                married = tables["married"]
                pw = pl.col("pwages").clip(0, None)
                sw = pl.col("swages").clip(0, None)
                yh = pl.max_horizontal(pw, sw) + (taxinc - pw - sw) / 2.0
                split = 2.0 * bracket_tax(taxinc / 2.0, married)
                spouses = bracket_tax(yh.clip(0, None), married) + bracket_tax((taxinc - yh).clip(0, None), married)
                married_tax = pl.when(is_joint).then(pl.min_horizontal(split, spouses)).otherwise(
                    bracket_tax(taxinc, married)
                )
                # The split lookup leaves the rate at the higher earner's share.
                married_rate = pl.when(is_joint).then(bracket_rate(yh.clip(0, None), married)).otherwise(
                    bracket_rate(taxinc, married)
                )
            else:
                married_tax = bracket_tax(taxinc * sep, tables["married"]) / sep
                married_rate = bracket_rate(taxinc * sep, tables["married"])
            statax = pl.when(is_single).then(single_tax).when(is_hoh).then(hoh_tax).otherwise(married_tax)
            rate = (
                pl.when(is_single).then(bracket_rate(taxinc, tables["single"]))
                .when(is_hoh).then(bracket_rate(taxinc, tables["head_of_household"]))
                .otherwise(married_rate)
            )
        if 2009 <= y <= 2010:
            surtax = p["surtax_2009_2010"]

            def multiplier(rows: list[list[float]], divisor: pl.Expr | float = 1.0) -> pl.Expr:
                result = pl.lit(1.0)
                for lower, factor in rows:
                    result = pl.when(taxinc > lower / divisor).then(float(factor)).otherwise(result)
                return result

            statax = statax * (
                pl.when(is_single).then(multiplier(surtax["single"]))
                .when(is_hoh).then(multiplier(surtax["head_of_household"]))
                .otherwise(multiplier(surtax["married"], sep))
            )
        ch = pl.when(depx >= 2).then(2.0).when(depx > 0).then(1.0).otherwise(0.0)
        df = df.with_columns(
            nc_statax=statax,
            nc_ch=ch,
            nc_detail_agi=detail_agi,
            nc_detail_stded=stded,
            nc_detail_xitded=detail_xitded,
            nc_detail_exemp=detail_exemp,
            nc_detail_lcred=pl.lit(0.0),
            nc_detail_rate=rate,
        )

    statax = pl.col("nc_statax")
    ch = pl.col("nc_ch")

    # --- Child care credit ---
    chcr = pl.lit(0.0)
    if 1981 <= y <= 2013:
        eligible = pl.min_horizontal(ch * p.num("child_care_expense_per_child"), pl.col("childcare"))
        if y <= 1993:
            chcr = p.num("child_care_rate") * eligible
        else:
            rates = p["child_care_rate_1994"]
            chcr = eligible * (
                pl.when(is_hoh).then(interpolate_table(fed_agi, rates["head_of_household"]))
                .when(is_single).then(interpolate_table(fed_agi, rates["single"]))
                .otherwise(interpolate_table(fed_agi * sep, rates["married"]))
            )
    df, (chcr,) = checkpoint(df, nc_chcr=chcr)
    statax = (statax - chcr).clip(0, None)
    if y <= 1988:
        # Joint returns: the credit comes off the larger spouse's tax, and
        # TAXSIM's sequential test can take it off both.
        staxh2 = pl.when(staxh > staxw).then((staxh - chcr).clip(0, None)).otherwise(staxh)
        staxw2 = pl.when(staxw >= staxh2).then((staxw - chcr).clip(0, None)).otherwise(staxw)
        statax = pl.when(is_joint).then(staxh2 + staxw2).otherwise(statax)

    # --- Child tax credit 1995-2017 ---
    chld = pl.lit(0.0)
    if 1995 <= y <= 2017:
        children = pl.col("dep17") if y >= 1998 else depx
        amount = p.num("child_credit")
        if y <= 2013:
            chld = pl.when(fed_agi < by_filing_status(p["child_credit_agi_limit"])).then(children * amount).otherwise(0.0)
        else:
            low = by_filing_status(p["child_credit_low_agi_2014"])
            high = by_filing_status(p["child_credit_high_agi_2014"])
            chld = (
                pl.when(fed_agi <= low).then(children * p["child_credit_low_amount_2014"])
                .when(fed_agi <= high).then(children * amount)
                .otherwise(0.0)
            )
        statax = (statax - chld).clip(0, None)

    # --- Charitable credit for federal non-itemizers 1997-2013 ---
    contr = pl.lit(0.0)
    if 1997 <= y <= 2013:
        contr = pl.when(pl.col("itemizes")).then(0.0).otherwise(
            (pl.col("charity_cash") - p["charity_credit_floor_share"] * fed_agi).clip(0, None) * p.num("charity_credit_rate")
        )
        statax = (statax - contr).clip(0, None)

    # --- Earned income credit 2008-2013 (refundable) ---
    earncr = pl.lit(0.0)
    if 2008 <= y <= 2013:
        earncr = p.num("eitc_rate") * pl.col("eitc")
        statax = statax - earncr

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=pl.col("nc_detail_agi"),
        exemptions=pl.col("nc_detail_exemp"),
        standard_deduction=pl.col("nc_detail_stded"),
        itemized_deductions=pl.col("nc_detail_xitded"),
        taxable_income=pl.col("nc_taxinc"),
        child_care_credit=chcr,
        eic=earncr,
        credits=pl.col("nc_detail_lcred") + chcr + chld + contr + earncr,
        rate=pl.col("nc_detail_rate"),
    )
