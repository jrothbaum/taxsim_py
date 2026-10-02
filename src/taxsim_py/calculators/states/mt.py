"""Montana individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    checkpoint,
    household_income,
    interpolate_table,
    unemployment_total,
    forced_standard,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mt" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def _brackets(y: int) -> list[list[float]]:
    p = YearParams(MT_PARAMS, y)
    if y in (2022, 2023):
        return MT_PARAMS["brackets_2022_2023"][y]
    bounds = resolve_year(MT_PARAMS["bracket_bounds"], y)
    if y <= 2004:
        rates = MT_PARAMS["rates_through_2004"]
        factor = p.num("bracket_inflation")
    else:
        rates = MT_PARAMS["rates_from_2005"]
        factor = 1.0
    starts = [0.0] + [float(b) * factor for b in bounds]
    return [[start, float(rate)] for start, rate in zip(starts, rates)]


def compute_mt_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Montana income tax for each row."""
    state_year = "mt" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    y = effective_year
    p = YearParams(MT_PARAMS, effective_year)

    # Household income (`hy`) is read before TAXSIM's projected-year deflation.
    df = df.with_columns(
        mt_ui=unemployment_total(),
        mt_household_income_undeflated=household_income(),
        mt_half_setax=0.5 * payroll_parts(year)["setax"],
    )
    df = deflate_for_extrapolation(df, flate, extra=("mt_ui", "mt_half_setax"))

    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    ntp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    fed_agi = pl.col("agi")
    fullcg = pl.col("stcg") + pl.col("ltcg")
    if y >= 1987:
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        capgn = pl.max_horizontal(fullcg, -loss_limit / flate / sep)
    else:
        capgn = fullcg

    # --- Montana AGI ---
    # `subtr` collects Montana's subtractions for the Social Security worksheet.
    agi = fed_agi
    if y == 2020:
        agi = agi + pl.col("mt_ui") - pl.col("taxable_unemployment")
    cg_exclusion = p.num("capital_gains_exclusion_rate") * capgn.clip(0, None)
    agi = agi - cg_exclusion
    subtr = cg_exclusion + pl.col("mt_ui")
    if 1981 <= y <= 2023:
        # Repealed for 2024 (replaced by the flat age-65 subtraction below).
        excint = pl.min_horizontal(pl.col("intrec"), p.num("aged_interest_exclusion") * aged)
        agi = agi - excint
        subtr = subtr + excint
    if y >= 1991:
        # Retirement income exclusion for taxpayers 65 or older.
        index = p.num("retirement_exclusion_inflation")
        pensions = pl.col("pensions")
        retmax = pl.min_horizontal(
            p.num("retirement_exclusion_max") * (aged * index if y >= 2010 else 1.0), pensions
        )
        delta = p.num("retirement_exclusion_per_taxpayer") * ntp * index
        agiret = p.num("retirement_exclusion_income_start") * index
        rate = p.num("retirement_exclusion_phaseout_rate")
        agi_each = fed_agi / aged
        retexc = aged * (pl.min_horizontal(delta, pensions / aged) - rate * (agi_each - agiret).clip(0, None)).clip(0, None)
        retexc = (
            pl.when(agi_each < agiret).then(retmax)
            .when(agi_each < agiret + delta).then((retmax - rate * (fed_agi - agiret)).clip(0, None))
            .otherwise(retexc)
        )
        retexc = pl.when(aged > 0).then(retexc).otherwise(0.0)
        if y >= 2024:
            # A flat subtraction for each taxpayer 65 or older replaces the pension exclusion.
            retexc = float(p["old_age_subtraction_2024plus"]) * aged
        agi = (agi - retexc).clip(0, None)
        subtr = subtr + retexc
    if y >= 1984:
        agi = agi - pl.col("mt_ui")
        agi = agi + pl.when(is_sep & (fullcg < 0)).then(
            pl.max_horizontal(pl.lit(p.num("separate_capital_loss_floor")), fullcg)
        ).otherwise(0.0)
    # Social Security on Montana's worksheet in place of the federal amount.
    ssagi = pl.lit(0.0)
    if y >= 1984:
        gssi = pl.col("gssi")
        provisional = 0.5 * gssi + fed_agi - pl.col("taxable_social_security") + pl.col("transfers")
        xl8 = subtr + pl.col("mt_half_setax")
        single_class = pl.col("filing_status").is_in(["single", "head_of_household"])
        base_1 = pl.when(single_class).then(float(MT_PARAMS["social_security_base"]["single"])).otherwise(
            float(MT_PARAMS["social_security_base"]["married"]) / sep
        )
        base_2 = pl.when(single_class).then(float(MT_PARAMS["social_security_second_base"]["single"])).otherwise(
            float(MT_PARAMS["social_security_second_base"]["married"]) / sep
        )
        xl11 = provisional - xl8 - base_1
        rate_1, rate_2 = float(MT_PARAMS["social_security_rate_1"]), float(MT_PARAMS["social_security_rate_2"])
        worksheet = pl.min_horizontal(
            pl.min_horizontal(rate_1 * gssi, rate_1 * pl.min_horizontal(xl11, base_2))
            + rate_2 * (xl11 - base_2).clip(0, None),
            rate_2 * gssi,
        )
        ssagi = pl.when((gssi > 0) & (xl8 <= provisional)).then(worksheet).otherwise(0.0)
    agi = agi - pl.col("taxable_social_security") + ssagi

    df, (agi, capgn) = checkpoint(df, mt_agi=agi, mt_capgn=capgn)

    # --- Standard deduction ---
    pct = p.num("standard_deduction_pct")
    cap = p.num("standard_deduction_cap")
    floor = p.num("standard_deduction_floor")

    def standard(income: pl.Expr, taxpayers: pl.Expr | float) -> pl.Expr:
        return pl.max_horizontal(
            floor * taxpayers, pl.min_horizontal(cap * taxpayers, pct * income.clip(0, None))
        )

    std_taxpayers = pl.when(is_hoh).then(float(MT_PARAMS["head_of_household_standard_deduction_taxpayers"])).otherwise(ntp)
    stded = standard(agi, std_taxpayers)
    if y >= 2024:
        # Montana conforms to the federal standard deduction from 2024.
        stded = pl.col("standard_deduction")

    # --- Itemized deductions ---
    agix = fed_agi.clip(0, None)
    alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
    fed_char = pl.when(fed_agi < 0).then(0.0).otherwise(pl.min_horizontal(pl.col("charity_cash"), alim50)).clip(0, None)
    if y <= 1986:
        deducp = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
    elif y <= 2017:
        deducp = (pl.col("proptax") + pl.col("otheritem") + salt_ded).clip(0, None) + pl.col("mortgage") + fed_char
    else:
        deducp = pl.col("itemized_deduction")
    fed_tax = pl.col("fiitax")
    if y == 2008:
        rebate = MT_PARAMS["federal_tax_rebate_2008"]
        fed_tax = fed_tax - rebate["per_taxpayer"] * ntp - rebate["per_dependent"] * depx
    fed_tax = fed_tax.clip(0, None)
    if y >= 2005:
        fed_tax = pl.min_horizontal(p.num("federal_tax_deduction_cap_per_taxpayer") * ntp, fed_tax)
    if y >= 2024:
        fed_tax = pl.lit(0.0)  # the federal income tax deduction ended in 2024
    xitded = (deducp - salt_ded).clip(0, None) + fed_tax
    if 1991 <= y <= 2017:
        if y >= 2013:
            threshold = (
                p.num("itemized_limit_threshold_base")
                * p.num("itemized_limit_inflation")
                * by_filing_status(MT_PARAMS["itemized_limit_status_multiplier"])
            )
        else:
            threshold = p.num("itemized_limit_threshold_base") * p.num("itemized_limit_inflation") / sep
        reduce = pl.min_horizontal(
            p.num("itemized_limit_max_share") * (deducp - salt_ded),
            p.num("itemized_limit_rate") * (agi - threshold),
        ) * p.num("itemized_limit_fraction")
        xitded = xitded - pl.when(agi > threshold).then(reduce).otherwise(0.0)
    caps = MT_PARAMS["child_care_expense_cap"]
    ich = depx.clip(1, 3).floor()
    care_cap = pl.when(ich >= 3).then(float(caps[2])).when(ich >= 2).then(float(caps[1])).otherwise(float(caps[0]))
    chexp = (
        pl.min_horizontal(pl.col("childcare"), care_cap)
        - p.num("child_care_phaseout_rate") * (agi - p.num("child_care_phaseout_start")).clip(0, None)
    ).clip(0, None)
    if y >= 2024:
        chexp = pl.lit(0.0)  # the child and dependent care expense deduction ended in 2024
    xitded = xitded + pl.when(depx > 0).then(chexp).otherwise(0.0)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    df, (stded, xitded) = checkpoint(df, mt_stded=stded, mt_xitded=xitded)
    deduc = pl.max_horizontal(stded, xitded)

    # --- Exemptions and tax ---
    xmp = p.num("exemption")
    exemp = (ntp + depx + aged) * xmp
    df, (taxinc,) = checkpoint(df, mt_taxinc=(agi - deduc - exemp).clip(0, None))
    brackets = _brackets(y) if y < 2024 else [[0.0, 0.0]]
    if y >= 2024:
        # Two-rate schedule by filing status, with net long-term gains taxed on their own scale
        # stacked above the ordinary income.
        d = MT_PARAMS["schedule_2024plus"]
        statuses = ("single", "married_separate", "head_of_household", "married_joint")
        gains = (pl.col("ltcg") + pl.col("stcg")).clip(0, None)
        gains = pl.min_horizontal(pl.col("ltcg").clip(0, None), gains)
        gains_in = pl.min_horizontal(gains, taxinc)
        ordinary = (taxinc - gains_in).clip(0, None)
        tax = pl.lit(0.0)
        for status in statuses:
            row = d[status]
            low, high = float(row["rate_low"]), float(row["rate_high"])
            ordinary_tax = low * ordinary.clip(None, float(row["threshold"])) + high * (ordinary - float(row["threshold"])).clip(0, None)
            room = (float(row["threshold"]) - ordinary).clip(0, None)
            gains_tax = float(d["gains_rate_low"]) * pl.min_horizontal(gains_in, room) + float(d["gains_rate_high"]) * (gains_in - room).clip(0, None)
            tax = pl.when(pl.col("filing_status") == status).then(ordinary_tax + gains_tax).otherwise(tax)
        statutory_tax = tax
    df, (statax,) = checkpoint(df, mt_table_tax=statutory_tax if y >= 2024 else bracket_tax(taxinc, brackets))

    # --- Married couples: tax as separate returns if lower ---
    wages_total = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    agih = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + p.num("joint_split_other_income_share") * (
        agi - wages_total
    )
    agiw = agi - agih
    stdh = standard(agih, 1.0)
    stdw = standard(agiw, 1.0)
    split_stded = stdh + stdw if y >= 1981 else stded
    half = p.num("joint_split_itemized_share")
    xitd_each = pl.when((xitded > 0) & (agi > 0)).then(half * xitded).otherwise(0.0)
    use_itemized = xitded > split_stded
    dedh = pl.when(use_itemized).then(xitd_each).otherwise(stdh)
    dedw = pl.when(use_itemized).then(xitd_each).otherwise(stdw)
    exempw = pl.when(aged > 0).then(2.0 * xmp).otherwise(xmp)
    exemph = exemp - exempw
    split_tax = bracket_tax((agih - dedh - exemph).clip(0, None), brackets) + bracket_tax(
        (agiw - dedw - exempw).clip(0, None), brackets
    )
    rate = pl.when(is_joint).then(
        bracket_rate((agiw - dedw - exempw).clip(0, None), brackets)
    ).otherwise(bracket_rate(taxinc, brackets))
    if y < 2024:
        statax = pl.when(is_joint).then(pl.min_horizontal(statax, split_tax)).otherwise(statax)
    df, (statax,) = checkpoint(df, mt_tax=statax * p.num("surtax"))

    # --- Credits ---
    cgcred = p.num("capital_gains_credit_rate") * capgn.clip(0, None)
    statax = (statax - cgcred).clip(0, None)

    # Elderly homeowner/renter credit (refundable).
    pcred = pl.lit(0.0)
    if y >= 1981:
        hy = pl.col("mt_household_income_undeflated")
        rent_share = float(MT_PARAMS["elderly_credit_rent_share"]) * pl.col("rentpaid")
        if y == 1981:
            ptax = pl.max_horizontal(pl.col("proptax"), rent_share)
        else:
            ptax = pl.col("proptax") + rent_share
        multiplier = interpolate_table(hy, MT_PARAMS["elderly_credit_multiplier"])
        if y <= 1982:
            pcred = (ptax - hy * multiplier).clip(0, float(MT_PARAMS["elderly_credit_max_1981_1982"]))
        else:
            hy1 = (hy - p.num("elderly_credit_income_exclusion")).clip(0, None)
            hynet = interpolate_table(hy1, MT_PARAMS["elderly_credit_income_share"]) * hy1
            pcred = (ptax - hynet).clip(0, p.num("elderly_credit_max"))
            if y == 1998:
                pcred = pl.when(hy >= float(MT_PARAMS["elderly_credit_income_limit_1998"])).then(0.0).otherwise(pcred)
            if y >= 1999:
                pcred = multiplier * pcred
        pcred = pl.when(aged > 0).then(pcred).otherwise(0.0)
        statax = statax - pcred
    howcrd = pl.lit(0.0)
    if y == 2007:
        howcrd = pl.when(pl.col("proptax") > 0).then(float(MT_PARAMS["homeowner_credit_2007"])).otherwise(0.0)
        statax = statax - howcrd
    earncr = p.num("eitc_match_rate") * pl.col("eitc")
    statax = statax - earncr

    reported_stded = pl.when(is_joint & (pl.lit(y) >= 1981)).then(split_stded).otherwise(stded)
    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=exemp,
        standard_deduction=reported_stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        property_credit=pcred,
        eic=earncr,
        credits=cgcred + pcred + howcrd + earncr,
        rate=rate,
    )
