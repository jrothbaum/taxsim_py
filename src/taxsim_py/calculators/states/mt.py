"""Montana individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.eitc import eitc_age_eligible, eitc_filer_eligible, federal_eitc
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    checkpoint,
    household_income,
    interpolate_table,
    unemployment_total,
    REVERSE_FALLING_TABLE_WEIGHTS,
    forced_standard,
    optimal_split,
    split_agi,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mt" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
FEDERAL_EITC_PARAMS = pl.read_csv(PARAMETERS_ROOT / "national" / "eitc.csv")
FEDERAL_EITC_MISC = load_yaml(PARAMETERS_ROOT / "national" / "eitc_misc.yaml")


def _brackets(y: int) -> list[list[float]]:
    p = YearParams(MT_PARAMS, y)
    factor = p.num("bracket_inflation")
    return [[float(start) * factor, float(rate)] for start, rate in p.value("brackets")]


def _compute_taxsim_2024(df: pl.DataFrame, p: YearParams, flate: float) -> pl.DataFrame:
    """TAXSIM's 2024 on routine: federal AGI less the larger federal deduction, the aged exemption, and
    a two-rate schedule with a lower rate on long-term gains below the first-rate limit; no subtractions."""
    agi = pl.col("agi")
    stded = pl.col("standard_deduction")
    xitded = pl.col("itemized_deduction")
    exemp = aged_count() * p.num("taxsim_exemption")
    # The state income tax deducted federally (within the cap) is added back, up to the itemized deduction above the standard.
    state_tax_added_back = pl.min_horizontal(
        (pl.col("salt_capped") - pl.col("otheritem") - pl.col("proptax")).clip(0, None), (xitded - stded).clip(0, None)
    )
    taxinc = (
        agi - pl.max_horizontal(stded, xitded) - exemp - pl.col("senior_deduction") + state_tax_added_back
    ).clip(0, None)
    schedule = p.value("taxsim_two_rate_schedule")
    group = pl.when(files_joint()).then(pl.lit("married_joint")).when(files_head_of_household()).then(
        pl.lit("head_of_household")
    ).otherwise(pl.lit("single"))

    def by_group(key: str) -> pl.Expr:
        return pl.when(group == "married_joint").then(float(schedule["married_joint"][key])).when(
            group == "head_of_household"
        ).then(float(schedule["head_of_household"][key])).otherwise(float(schedule["single"][key]))

    upper, low, high, subtraction = by_group("upper"), by_group("rate_low"), by_group("rate_high"), by_group("subtraction")
    gains = pl.min_horizontal(pl.col("ltcg").clip(0, None), taxinc)
    ordinary = taxinc - gains
    ordinary_tax = pl.when(ordinary <= upper).then(low * ordinary).otherwise(high * ordinary - subtraction)
    low_gains = pl.min_horizontal(gains, (upper - ordinary).clip(0, None))
    gains_tax = 0.03 * low_gains + 0.041 * (gains - low_gains)
    taxbc = ordinary_tax + gains_tax
    earncr = p.num("eitc_match_rate") * pl.col("eitc")
    df = df.with_columns(siitax=(taxbc - earncr) * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        eic=earncr,
        credits=earncr,
        rate=pl.when(ordinary <= upper).then(low).otherwise(high),
    )


def compute_mt_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Montana income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, MT_PARAMS)
    y = effective_year
    p = YearParams(MT_PARAMS, effective_year)

    # Household income (`hy`) is read before TAXSIM's projected-year deflation.
    df = df.with_columns(
        mt_ui=unemployment_total(),
        mt_household_income_undeflated=household_income(behavior),
        mt_half_setax=0.5 * pl.col("payroll_setax"),
    )
    df = deflate_for_extrapolation(df, flate, extra=("mt_ui", "mt_half_setax"))

    if y >= 2024 and p.in_effect("taxsim_two_rate_schedule") and not behavior.compute_montana_tax_from_2024_per_statute:
        return _compute_taxsim_2024(df, p, flate)

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
    # Montana subtracts the unemployment compensation in federal AGI; TAXSIM
    # subtracts all of it (it adds the 2020 federal exclusion back first).
    ui_subtraction = pl.col("mt_ui")
    if y != 2020 and behavior.subtract_only_taxable_unemployment_in_montana:
        ui_subtraction = pl.col("taxable_unemployment")
    subtr = cg_exclusion + ui_subtraction
    if p.in_effect("aged_interest_exclusion"):
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
        if not behavior.compute_montana_retirement_exclusion_per_statute:
            # TAXSIM: no age test; the cap scales with taxpayers (from 2010).
            taxpayers = pl.max_horizontal(pl.lit(1.0), ntp)
            retmax_t = pl.min_horizontal(
                p.num("retirement_exclusion_max") * (taxpayers * index if y >= 2010 else 1.0), pensions
            )
            old = pl.lit(1.0)  # TAXSIM divides by a variable that is never set (always 1)
            xl3 = pl.min_horizontal(delta, pensions / old)
            xl6 = rate * (fed_agi / old - agiret).clip(0, None)
            retexc = old * (xl3 - xl6).clip(0, None)
            retexc = pl.when(fed_agi / old < agiret + delta).then(
                pl.when(fed_agi / old < agiret).then(retmax_t).otherwise((retmax_t - rate * (fed_agi - agiret)).clip(0, None))
            ).otherwise(retexc)
        if p.in_effect("old_age_subtraction"):
            retexc = p.num("old_age_subtraction") * aged
        agi = (agi - retexc).clip(0, None)
        subtr = subtr + retexc
    if y >= 1984:
        agi = agi - ui_subtraction
        if not behavior.subtract_montana_separate_capital_loss_once:
            # TAXSIM subtracts a separate filer's capital loss a second time.
            agi = agi + pl.when(is_sep & (fullcg < 0)).then(
                pl.max_horizontal(pl.lit(p.num("separate_capital_loss_floor")), fullcg)
            ).otherwise(0.0)
    # Social Security on Montana's worksheet in place of the federal amount.
    ssagi = pl.lit(0.0)
    if y >= 1984:
        gssi = pl.col("gssi")
        transfers = pl.lit(0.0) if behavior.exclude_transfers_from_provisional_income else pl.col("transfers")
        provisional = 0.5 * gssi + fed_agi - pl.col("taxable_social_security") + transfers
        xl8 = subtr + pl.col("mt_half_setax")
        single_class = pl.col("filing_status").is_in(["single", "head_of_household"])
        base_1 = pl.when(single_class).then(float(MT_PARAMS["social_security_base"]["single"])).otherwise(
            float(MT_PARAMS["social_security_base"]["married"]) / sep
        )
        base_2 = pl.when(single_class).then(float(MT_PARAMS["social_security_second_base"]["single"])).otherwise(
            float(MT_PARAMS["social_security_second_base"]["married"]) / sep
        )
        xl11 = provisional - xl8 - base_1
        if behavior.floor_montana_social_security_worksheet:
            xl11 = xl11.clip(0, None)
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
    if p.value("conforms_to_federal_standard_deduction"):
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
    if p.in_effect("federal_tax_rebate"):
        rebate = p.value("federal_tax_rebate")
        fed_tax = fed_tax - rebate["per_taxpayer"] * ntp - rebate["per_dependent"] * depx
    fed_tax = fed_tax.clip(0, None)
    if y >= 2005:
        fed_tax = pl.min_horizontal(p.num("federal_tax_deduction_cap_per_taxpayer") * ntp, fed_tax)
    if not p.value("federal_tax_deduction"):
        fed_tax = pl.lit(0.0)
    xitded = (deducp - salt_ded).clip(0, None) + fed_tax
    if p.in_effect("itemized_limit_rate"):
        if y >= 2013:
            threshold = (
                p.num("itemized_limit_threshold_base")
                * p.num("itemized_limit_inflation")
                * by_filing_status(MT_PARAMS["itemized_limit_status_multiplier"])
            )
        else:
            threshold = p.num("itemized_limit_threshold_base") * p.num("itemized_limit_inflation") / sep
        fraction = p.num("itemized_limit_fraction")
        if y in (2011, 2012) and not behavior.suspend_montana_itemized_limit_through_2012:
            # TAXSIM's suspension test is true only in 2010.
            fraction = 1.0
        reduce = pl.min_horizontal(
            p.num("itemized_limit_max_share") * (deducp - salt_ded),
            p.num("itemized_limit_rate") * (agi - threshold),
        ) * fraction
        xitded = xitded - pl.when(agi > threshold).then(reduce).otherwise(0.0)
    if p.in_effect("child_care_expense_cap"):
        caps = p.value("child_care_expense_cap")
        ich = depx.clip(1, 3).floor()
        care_cap = pl.when(ich >= 3).then(float(caps[2])).when(ich >= 2).then(float(caps[1])).otherwise(float(caps[0]))
        chexp = (
            pl.min_horizontal(pl.col("childcare"), care_cap)
            - p.num("child_care_phaseout_rate") * (agi - p.num("child_care_phaseout_start")).clip(0, None)
        ).clip(0, None)
    else:
        chexp = pl.lit(0.0)
    xitded = xitded + pl.when(depx > 0).then(chexp).otherwise(0.0)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    df, (stded, xitded) = checkpoint(df, mt_stded=stded, mt_xitded=xitded)
    deduc = pl.max_horizontal(stded, xitded)

    # --- Exemptions and tax ---
    xmp = p.num("exemption")
    exemp = (ntp + depx + aged) * xmp
    # 2025 on: Montana lets filers subtract the federal senior deduction (like the federal return).
    federal_schedule_1a = pl.col("senior_deduction") if y >= 2025 else pl.lit(0.0)
    df, (taxinc,) = checkpoint(df, mt_taxinc=(agi - deduc - exemp - federal_schedule_1a).clip(0, None))
    two_rate = p.in_effect("two_rate_schedule")
    brackets = [[0.0, 0.0]] if two_rate else _brackets(y)
    if two_rate:
        d = p.value("two_rate_schedule")
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
    df, (statax,) = checkpoint(df, mt_table_tax=statutory_tax if two_rate else bracket_tax(taxinc, brackets))

    # --- Married couples: tax as separate returns if lower ---
    wages_total = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    agih = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + p.num("joint_split_other_income_share") * (
        agi - wages_total
    )
    agiw = agi - agih
    if not behavior.split_montana_joint_returns_per_statute:
        # TAXSIM's `splitagi`: each spouse's own income, with the rest shared equally.
        agih, agiw = split_agi(agi, 0.0, 0.0)
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
    if not behavior.split_montana_joint_returns_per_statute:
        # TAXSIM: each spouse's own exemption (plus one for being 65 or older); dependents' exemptions
        # go where they cut the larger taxable income first.
        exemph = xmp * (1.0 + ((aged > 0) & (pl.col("page") >= 65)).cast(pl.Float64))
        exempw = xmp * (1.0 + ((aged > 0) & (pl.col("sage") >= 65)).cast(pl.Float64))
        exempd = (federal_exemption_count(y) - ntp) * xmp
        dependent_h, dependent_w = optimal_split(exempd, (agih - dedh).clip(0, None), (agiw - dedw).clip(0, None))
        exemph, exempw = exemph + dependent_h, exempw + dependent_w
    split_tax = bracket_tax((agih - dedh - exemph).clip(0, None), brackets) + bracket_tax(
        (agiw - dedw - exempw).clip(0, None), brackets
    )
    rate = pl.when(is_joint).then(
        bracket_rate((agiw - dedw - exempw).clip(0, None), brackets)
    ).otherwise(bracket_rate(taxinc, brackets))
    separate_on_joint = is_joint & (split_tax < statax) if not two_rate else pl.lit(False)
    taxhi = bracket_tax((agih - xitd_each - exemph).clip(0, None), brackets)
    taxwi = bracket_tax((agiw - xitd_each - exempw).clip(0, None), brackets)
    if not two_rate:
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
        if not behavior.read_montana_elderly_credit_multiplier_per_statute:
            # TAXSIM's `tablk2` weights a falling table correctly, unlike `tablki`.
            token = REVERSE_FALLING_TABLE_WEIGHTS.set(False)
            try:
                multiplier = interpolate_table(hy, MT_PARAMS["taxsim_elderly_credit_multiplier"])
            finally:
                REVERSE_FALLING_TABLE_WEIGHTS.reset(token)
        if y <= 1982:
            pcred = (ptax - hy * multiplier).clip(0, p.num("elderly_credit_max"))
        else:
            hy1 = (hy - p.num("elderly_credit_income_exclusion")).clip(0, None)
            hynet = interpolate_table(hy1, MT_PARAMS["elderly_credit_income_share"]) * hy1
            pcred = (ptax - hynet).clip(0, p.num("elderly_credit_max"))
            if p.in_effect("elderly_credit_income_limit"):
                pcred = pl.when(hy >= p.num("elderly_credit_income_limit")).then(0.0).otherwise(pcred)
            if y >= 1999:
                pcred = multiplier * pcred
        pcred = pl.when(aged > 0).then(pcred).otherwise(0.0)
        statax = statax - pcred
    howcrd = pl.lit(0.0)
    if p.in_effect("homeowner_credit"):
        howcrd = pl.when(pl.col("proptax") > 0).then(p.num("homeowner_credit")).otherwise(0.0)
        statax = statax - howcrd
    earncr = p.num("eitc_match_rate") * pl.col("eitc")
    statax = statax - earncr
    if p.in_effect("taxsim_rebate") and not behavior.leave_out_one_time_state_rebates:
        # TAXSIM pays it per taxpayer, capped by the tax left after the nonrefundable credits; a couple
        # filing separately on one return gets one rebate per spouse, each capped by that spouse's tax.
        per_taxpayer = p.value("taxsim_rebate")["per_taxpayer"]
        taxbc = pl.col("mt_tax")
        rebate = pl.min_horizontal(per_taxpayer * ntp, taxbc - cgcred)
        # Spouses filing separately on one return each get the rebate, capped by the tax on their share as
        # if they itemized (whatever the return does) less their own earned income credit.
        status = pl.col("filing_status")
        nkids = pl.col("dep18").clip(0, 3)
        disqy = capgn.clip(0, None) + pl.col("dividends") + pl.col("intrec") + pl.col("otherprop").clip(0, None)
        eitc_params = FEDERAL_EITC_PARAMS.filter(pl.col("year") == y)
        dylim = float(resolve_year(FEDERAL_EITC_MISC["dylim"], y))

        def spouse_credit(earned: pl.Expr) -> pl.Expr:
            credit = federal_eitc(earned, pl.col("agi"), disqy, status, nkids, eitc_params, dylim)
            childless = nkids == 0
            eligible = eitc_filer_eligible(childless, y) & eitc_age_eligible(childless, y)
            return p.num("eitc_match_rate") * pl.when(eligible).then(credit).otherwise(0.0)

        earned_h = pl.col("pwages") + pl.col("psemp")
        earned_w = pl.col("swages") + pl.col("ssemp")
        separate_rebate = pl.min_horizontal(per_taxpayer, taxhi - spouse_credit(earned_h)) + pl.min_horizontal(
            per_taxpayer, taxwi - spouse_credit(earned_w)
        )
        rebate = pl.when(separate_on_joint).then(separate_rebate).otherwise(rebate)
        statax = statax - pl.when(pl.col("mstat") == 8).then(0.0).otherwise(rebate)

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
