"""Virginia individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    by_filing_status,
    checkpoint,
    pre1987_federal_itemizing,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

VA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "va" / "income_tax.yaml")


def _brackets(y: int) -> list[list[float]]:
    p = YearParams(VA_PARAMS, y)
    rows = [list(r) for r in VA_PARAMS["brackets_rates"]]
    rows[3][0] = p.num("brackets_top_start")
    return rows


def compute_va_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Virginia income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(VA_PARAMS, effective_year)
    df = df.with_columns(va_schede=pl.col("otherprop") + (pl.col("scorp") if year >= 1987 else 0.0))
    dividend_adjustment = dividend_input_adjustment()
    # Self-employment tax (`comnew(175)`) is not deflated in projected years.
    df = deflate_for_extrapolation(df, flate, extra=("va_schede",))

    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    single_type = (files_single()) | is_hoh
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    fed_agi = pl.col("agi")
    salt = pl.col("state_sales_or_income_tax_ded")

    # --- AGI (unemployment compensation is not taxed) ---
    agi = fed_agi - pl.col("taxable_unemployment")
    if y <= 1987:
        agi = agi - p.num("aged_deduction_1977_1987") * aged
    if 1982 <= y <= 1986:
        agi = agi + pl.col("pre1987_twoded")
    if y >= 1984:
        agi = agi - pl.col("taxable_social_security")
    df, (agi,) = checkpoint(df, va_agi=agi)
    detail_agi = agi

    # No return below the filing minimum.
    if y <= 1978:
        must_file = pl.lit(True)
    elif y <= 1986:
        must_file = agi >= p["filing_minimum_1979"]
    elif y <= 2004:
        m = p["filing_minimum_1987"]
        must_file = agi >= pl.when(is_joint | is_sep).then(m["married"]).otherwise(m["single"]) / sep
    else:
        must_file = agi >= p["filing_minimum_2005"] * txp / sep

    # --- Retirement subtraction (1989) and age deduction (1990+) ---
    pensions = pl.col("pensions")
    elder = pl.lit(0.0)
    if y == 1989:
        r = p["retirement_subtraction_1989"]
        subtraction = (
            pl.when(pensions <= r["full_limit"]).then(pensions.clip(0, r["full_limit"]))
            .when((pensions >= r["partial_start"]) & (pensions <= r["partial_end"]))
            .then(((r["base"] - pensions) / r["divisor"]).clip(0, r["cap"]))
            .otherwise(0.0)
        )
        agi = agi - pl.when(aged >= 1).then(subtraction).otherwise(0.0)
    elif y >= 1990:
        amount = p.num("age_deduction")
        elder = pl.min_horizontal(fed_agi, pl.lit(amount) if y == 1992 else amount * aged)
        if y >= 2004:
            limits = p["age_deduction_income_limit"]
            ylimit = pl.when(is_joint).then(float(limits["married_joint"])).otherwise(float(limits["single"]))
            over = fed_agi - pl.col("taxable_social_security") - ylimit
            elder = pl.when(over > 0).then(pl.when(over > elder).then(0.0).otherwise(elder - over)).otherwise(elder)
        elder = pl.when(aged >= 1).then(elder).otherwise(0.0)
        agi = agi - elder
    df, (agi, elder) = checkpoint(df, va_agi_after_age=agi, va_elder=elder)

    # --- Standard deduction ---
    if y <= 1986:
        c = p["standard_deduction_1986"]
        stded = (c["rate"] * agi).clip(c["low"] / sep, c["high"] / sep)
    elif y <= 1988:
        c = p["standard_deduction_1987"] if y == 1987 else p["standard_deduction_1988"]
        stded = pl.when(is_joint).then(c["joint"]).otherwise(c["other"])
    elif y <= 2004:
        c = p["standard_deduction_1989"]
        stded = pl.when(is_joint | is_sep).then(c["married"] / sep).otherwise(c["single"])
    else:
        stded = p.num("standard_deduction_per_taxpayer") * txp

    # --- Itemized deductions ---
    if y <= 1986:
        gross, fed_itemizes, _ = pre1987_federal_itemizing(y)
        itemized = gross
    else:
        fed_itemizes = pl.col("itemizes")
        itemized = pl.col("itemized_deduction")
    if y <= 2017:
        xitded = pl.when(fed_itemizes).then(itemized - salt).otherwise(0.0)
        if y >= 1991:
            if y <= 2012:
                phas92 = p["itemized_limit_threshold"] * p.num("itemized_limit_index") / sep
            else:
                phas92 = (
                    p.num("itemized_limit_index_2013") * p["itemized_limit_threshold_2013"]
                    * by_filing_status(p["itemized_limit_status_factor_2013"])
                )
            ag = fed_agi.clip(0, None)
            xtot = salt + pl.col("proptax") + pl.col("mortgage") + pl.col("charity_cash")
            xconst = pl.min_horizontal(p["itemized_limit_share"] * xtot, p["itemized_limit_rate"] * (ag - phas92))
            limited = (itemized - (xtot - xconst) / xtot * salt).clip(0, None)
            xitded = pl.when(fed_itemizes & (ag >= phas92) & (ag > phas92) & (xtot > 0)).then(limited).otherwise(xitded)
    elif y == 2018:
        other = pl.col("proptax") + pl.col("otheritem")
        stt = pl.min_horizontal(salt, pl.min_horizontal(p["salt_cap"] / sep, other + salt) - other)
        xitded = pl.when(fed_itemizes).then(pl.col("itemized_before_limit") - stt).otherwise(0.0)
    else:
        deduc1 = pl.col("charity_cash") + salt + pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
        threshold = by_filing_status(p["itemized_limit_2019"])
        sttax = pl.min_horizontal(p["salt_cap"] / sep, salt)
        dedphs = pl.min_horizontal(
            p["itemized_limit_rate"] * (fed_agi - threshold),
            p["itemized_limit_share"] * (deduc1 - salt + sttax).clip(0, None),
        )
        perc = pl.when(deduc1 > 0).then((dedphs / deduc1).clip(0, 1)).otherwise(0.0)
        limited = deduc1 - dedphs - pl.when(deduc1 > 0).then(sttax * (1 - perc)).otherwise(0.0)
        over = fed_agi > threshold * p.num("itemized_limit_2019_test_index")
        xitded = pl.when(fed_itemizes).then(pl.when(over).then(limited).otherwise(deduc1 - salt)).otherwise(0.0)

    child = depx.clip(None, 2) * p.num("child_care_deduction_per_child") if y >= 1979 else pl.lit(0.0)
    child = pl.min_horizontal(pl.col("childcare"), child)
    deduc = pl.max_horizontal(stded, xitded) + child
    if y <= 2004:
        exemp = (txp + depx + aged) * p.num("exemption")
    else:
        exemp = (txp + depx) * p.num("exemption") + float(p["aged_exemption_2005"]) * aged
    df, (taxinc, xitded, stded) = checkpoint(
        df, va_taxinc=(agi - deduc - exemp).clip(0, None), va_xitded=xitded, va_stded=stded
    )

    # --- Tax ---
    brackets = _brackets(y)
    statax = bracket_tax(taxinc, brackets)
    rate = bracket_rate(taxinc, brackets)
    credits = pl.lit(0.0)
    earncr = pl.lit(0.0)
    if y <= 1999:
        # Joint returns: the lesser of joint tax and combined separate taxes.
        pw = pl.col("pwages")
        sw = pl.col("swages")
        agiw = 0.5 * (agi - pw - sw) + sw
        share_w = agiw / agi
        dedw = pl.when(xitded > stded).then(xitded * share_w).otherwise(stded * share_w)
        taxyw = (agiw - dedw - exemp * share_w).clip(0, None)
        taxyh = taxinc - taxyw
        separate = bracket_tax(taxyh.clip(0, None), brackets) + bracket_tax(taxyw, brackets)
        statax = pl.when(is_joint & (agi > 0)).then(pl.min_horizontal(statax, separate)).otherwise(statax)
        # The separate lookups leave the rate at the wife's share.
        rate = pl.when(is_joint & (agi > 0)).then(bracket_rate(taxyw, brackets)).otherwise(rate)

    # No tax at or below the AGI thresholds.
    if y <= 2004:
        z = p["zero_tax_agi_2004"]
        zero = pl.when(single_type).then(agi <= z["single"]).otherwise(agi <= z["married_per_taxpayer"] * txp)
    else:
        zero = agi <= p.num("zero_tax_agi_per_taxpayer") * txp
    statax = pl.when(zero).then(0.0).otherwise(statax)
    df, (statax,) = checkpoint(df, va_statax=statax)

    # --- Spouse tax adjustment (2000 on) ---
    if y >= 2000:
        capgn = pl.col("stcg") + pl.col("ltcg")
        ss = pl.col("taxable_social_security")
        othinc = (
            pl.col("dividends") + dividend_adjustment + pl.col("intrec")
            + (pl.col("psemp") + pl.col("ssemp")).clip(0, None) + capgn - 0.5 * pl.col("setax")
            + pl.col("pensions") + ss + pl.col("va_schede").clip(0, None)
        )
        # TAXSIM gives the wife the primary taxpayer's business and
        # professional income and the husband the spouse's.
        wife = pl.col("pbusinc") + pl.col("pprofinc") + pl.col("swages") + 0.5 * othinc
        husb = pl.col("sbusinc") + pl.col("sprofinc") + pl.col("pwages") + 0.5 * othinc
        wife = pl.min_horizontal(wife, husb)
        aged_w = (aged > 0).cast(pl.Float64)
        taxy23 = (
            wife - aged_w * p["spouse_adjustment_age_deduction"] - 0.5 * ss - p.num("exemption")
            - p["aged_exemption_2005"] * aged_w
        ).clip(0, None)
        taxy24 = (taxinc - taxy23).clip(0, None)
        taxy25 = taxinc / 2
        stat26 = bracket_tax(pl.min_horizontal(taxy23, taxy25), brackets)
        stat27 = bracket_tax(pl.max_horizontal(taxy24, taxy25), brackets)
        twocrd = (statax - stat26 - stat27).clip(0, p["spouse_adjustment_cap"])
        adjusted = is_joint & (taxinc > p["spouse_adjustment_min_taxable"])
        statax = pl.when(adjusted).then((statax - twocrd).clip(0, None)).otherwise(statax)
        rate = pl.when(adjusted).then(bracket_rate(pl.max_horizontal(taxy24, taxy25), brackets)).otherwise(rate)
        credits = pl.when(adjusted).then(twocrd).otherwise(0.0)

        # --- Credit for low-income individuals ---
        people = depx + txp
        limit = p.num("low_income_limit") + (people - 1) * p.num("low_income_limit_per_person")
        crlow = pl.when(agi <= limit).then(p["low_income_credit_per_person"] * people).otherwise(0.0)
        if y >= 2006:
            earncr = p["eitc_rate_2006"] * pl.col("eitc")
            crlow = pl.max_horizontal(earncr, crlow)
        # Not with the age deduction.
        crlow = pl.when(elder > 0).then(0.0).otherwise(crlow)
        crlow = pl.min_horizontal(statax, crlow)
        statax = statax - crlow
        credits = credits + crlow

    # Old age credit (through 1989).
    if y <= 1989:
        resid = (agi - p["old_age_credit_income_start"]).clip(0, None)
        ocred = p["old_age_credit_rate"] * (
            p.num("old_age_credit_base") * aged - pl.col("gssi") - 2.0 * resid
        ).clip(0, None)
        if y == 1989:
            ocred = pl.when(pensions >= p["old_age_credit_retirement_limit_1989"]).then(0.0).otherwise(ocred)
        statax = (statax - ocred).clip(0, None)
        credits = credits + ocred

    statax = pl.when(must_file).then(statax.clip(0, None)).otherwise(0.0)
    df = df.with_columns(siitax=statax * flate)
    # TAXSIM returns before the worksheet for returns below the filing minimum.
    worksheet = {
        "exemptions": exemp,
        "standard_deduction": stded,
        "itemized_deductions": xitded,
        "taxable_income": taxinc,
        "eic": earncr,
        "credits": credits,
        "rate": rate,
    }
    return with_state_detail(
        df,
        agi=pl.when(must_file).then(agi).otherwise(detail_agi),
        **{k: pl.when(must_file).then(v).otherwise(0.0) for k, v in worksheet.items()},
    )
