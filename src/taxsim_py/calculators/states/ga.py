"""Georgia individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import by_filing_status, dividend_input_adjustment, forced_standard, interpolate_table, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

GA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ga" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")

def compute_ga_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, GA_PARAMS)
    p = YearParams(GA_PARAMS, effective_year)
    p87 = YearParams(PRE1987_PARAMS, effective_year)

    df = df.with_columns(
        ga_sep=separate_divisor(),
        ga_taxpayers=taxpayer_count(),
    )
    df = deflate_for_extrapolation(df, flate, extra=("tax_before_credits",))

    # --- AGI ---
    df = df.with_columns(ga_agi=pl.col("agi"))

    # 2020: unemployment compensation excluded federally is taxable.
    if effective_year == 2020:
        ui_total = unemployment_total()
        df = df.with_columns(
            ga_agi=pl.col("ga_agi") + ui_total - pl.col("taxable_unemployment")
        )

    # 1982-1986: Georgia kept 1981 federal law, adding back the two-earner
    # deduction, part of the IRA deduction and federally excluded
    # unemployment compensation.
    if 1982 <= effective_year <= 1986:
        two_earner_rate = p87.num("two_earner_deduction_rate")
        two_earner_cap = p87.num("two_earner_deduction_cap")
        twoded = (
            two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).clip(0, two_earner_cap)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + twoded)
        # IRA contributions are not an input, so a return with little earned
        # income adds back up to the full amount.
        earnings = pl.col("wages") + (pl.col("psemp") + pl.col("ssemp") + pl.col("otherprop").clip(0, None)).clip(0, None)
        ira = p.value("ira_addback")
        iramax = pl.when(files_joint()).then(float(ira["limit_joint"])).otherwise(float(ira["limit_other"]))
        iralim = pl.min_horizontal(earnings, iramax)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + (ira["amount"] - iralim).clip(0, ira["amount"]))
        ui_total = unemployment_total()
        thresholds = p.value("unemployment_threshold")
        gemp = by_filing_status(thresholds["georgia"])
        femp = by_filing_status(thresholds["federal"])
        stutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - gemp).clip(0, None), ui_total)
        fdutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - femp).clip(0, None), ui_total)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + (stutx - fdutx))

    # Retirement income exclusion for taxpayers 65 or older.
    aged = aged_count()
    rtmax = p.num("retirement_exclusion_max")
    rtexc = pl.min_horizontal(pl.col("agi"), rtmax * aged).clip(0, None)
    if p.in_effect("retirement_exclusion_earned_limit"):
        rtexc = pl.when(
            pl.col("earned_income") > p.num("retirement_exclusion_earned_limit") * pl.col("ga_taxpayers")
        ).then(0.0).otherwise(rtexc)
    if p.in_effect("retirement_exclusion_earnings_cap"):
        cap = p.num("retirement_exclusion_earnings_cap")
        sep = pl.col("ga_sep")
        capgn = pl.max_horizontal(pl.col("stcg") + pl.col("ltcg"), -p["retirement_exclusion_capital_loss_limit"] / sep)
        schedule_e = pl.col("otherprop") + pl.col("scorp")
        investment = pl.col("intrec") + pl.col("dividends") + dividend_input_adjustment() + schedule_e
        rhy = (investment + pl.col("pensions")).clip(0, None) + pl.when(capgn >= 0).then(capgn / sep).otherwise(0.5 * capgn / sep)
        single = pl.min_horizontal(pl.lit(rtmax), pl.min_horizontal(pl.col("earned_income"), cap) + rhy)
        se_half = 0.5 * (pl.col("psemp") + pl.col("ssemp")).clip(0, None)
        hearn = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + se_half
        wearn = pl.min_horizontal(pl.col("pwages"), pl.col("swages")) + se_half
        if effective_year >= 2018:
            hearn = hearn + pl.col("pbusinc") + pl.col("sbusinc")
            wearn = wearn + pl.col("pprofinc") + pl.col("sprofinc")
        rterw = pl.min_horizontal(wearn, cap)
        rterh = pl.min_horizontal(hearn, cap)
        rhyw = 0.5 * investment + pl.col("pensions") + pl.when(capgn >= 0).then(0.5 * capgn / sep).otherwise(p["retirement_exclusion_spouse_loss_share"] * capgn / sep)
        couple_one = pl.min_horizontal(pl.lit(rtmax), rterw + rhyw) + rterh
        couple_two = pl.min_horizontal(pl.lit(rtmax), rterw + 0.5 * rhy) + pl.min_horizontal(pl.lit(rtmax), rterh + 0.5 * rhy)
        rtexc = (
            pl.when(pl.col("ga_taxpayers") == 1).then(single)
            .when(pl.col("ga_taxpayers") > 1).then(pl.when(aged < 2).then(couple_one).otherwise(couple_two))
            .otherwise(rtexc)
        )
    df = df.with_columns(ga_agi=pl.col("ga_agi") - pl.when(aged > 0).then(rtexc).otherwise(0.0))
    # Social Security benefits are exempt from 1988.
    if effective_year >= 1988:
        df = df.with_columns(ga_agi=pl.col("ga_agi") - pl.col("taxable_social_security"))

    # --- Exemptions ---
    taxpayers = pl.col("ga_taxpayers")
    if effective_year <= 1986:
        exemption = p.num("exemption")
        ga_exemp = taxpayers * p.num("taxpayer_exemption") + (pl.col("depx") + aged_count()) * exemption
        ga_exemp = ga_exemp + pl.when(
            (files_head_of_household()) & (pl.col("depx") > 0)
        ).then(p.num("head_of_household_dependent_exemption")).otherwise(0.0)
    elif not p.in_effect("dependent_exemption"):
        exemption = p.num("exemption")
        taxpayer_amount = p.num("taxpayer_exemption") if p.in_effect("taxpayer_exemption") else exemption
        ga_exemp = taxpayers * taxpayer_amount + pl.col("depx") * exemption
    elif not p.in_effect("exemption"):
        ga_exemp = pl.col("depx") * p.num("dependent_exemption")
    elif p.in_effect("married_exemption"):
        ga_exemp = (
            pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(p.num("exemption") * taxpayers)
            .otherwise(p.num("married_exemption") * taxpayers)
            + pl.col("depx") * p.num("dependent_exemption")
        )
    else:
        ga_exemp = p.num("exemption") * taxpayers + pl.col("depx") * p.num("dependent_exemption")
    df = df.with_columns(ga_exemp=ga_exemp)

    # --- Standard deduction ---
    is_married = pl.col("filing_status").is_in(["married_joint", "married_separate"])
    sep = pl.col("ga_sep")
    if p.in_effect("standard_deduction_percent"):
        single = p.value("standard_deduction_percent", "single")
        married = p.value("standard_deduction_percent", "married")
        stded_single = (single["rate"] * pl.col("ga_agi")).clip(single["floor"], single["ceiling"])
        stded_married = (married["rate"] * pl.col("ga_agi")).clip(married["floor"] / sep, married["ceiling"] / sep)
        df = df.with_columns(ga_stded=pl.when(is_married).then(stded_married).otherwise(stded_single))
    else:
        flat_s = p.num("standard_deduction", "single")
        flat_m = p.num("standard_deduction", "married")
        aged_std = p.num("aged_standard_deduction")
        df = df.with_columns(
            ga_stded=pl.when(is_married).then(flat_m / sep).otherwise(flat_s) + aged_std * aged_count()
        )

    # --- Itemized deduction ---
    # Federal itemized deductions (zero when not itemizing) less the state
    # income or sales tax deduction.
    if effective_year <= 1986:
        itemizes_indicator = pl.col("pre1987_itemizes").cast(pl.Float64)
        df = df.with_columns(
            ga_xitded=(pl.col("pre1987_deduc") - pl.col("state_sales_or_income_tax_ded")) * itemizes_indicator
        )
    else:
        itemizes_indicator = pl.col("itemizes").cast(pl.Float64)
        df = df.with_columns(
            ga_xitded=(pl.col("itemized_deduction") - pl.col("state_sales_or_income_tax_ded")) * itemizes_indicator
        )

    if effective_year == 1999:
        df = df.with_columns(ga_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ga_xitded")))

    df = df.with_columns(ga_deduc=pl.max_horizontal(pl.col("ga_xitded"), pl.col("ga_stded")))
    df = df.with_columns(ga_taxinc=(pl.col("ga_agi") - pl.col("ga_deduc") - pl.col("ga_exemp")).clip(0, None))

    # --- Tax ---
    if p.in_effect("flat_rate"):
        rate = p.num("flat_rate")
        df = df.with_columns(ga_statax=rate * pl.col("ga_taxinc"))
        rate_expr = pl.lit(rate)
    else:
        brackets = p.value("brackets")
        uses_married = pl.col("filing_status") != "single"
        taxy = pl.col("ga_taxinc") * sep
        stat_married = bracket_tax(taxy, brackets["married"]) / sep
        stat_single = bracket_tax(pl.col("ga_taxinc"), brackets["single"])
        df = df.with_columns(ga_statax=pl.when(uses_married).then(stat_married).otherwise(stat_single))
        rate_expr = pl.when(uses_married).then(
            bracket_rate(taxy, brackets["married"])
        ).otherwise(bracket_rate(pl.col("ga_taxinc"), brackets["single"]))

    # --- Credits ---
    if p.in_effect("child_care_credit_expenses"):
        cc = p.value("child_care_credit_expenses")
        chmax = (pl.col("depx") * cc["cap_per_dependent"]).clip(float(cc["cap_min"]), float(cc["cap_max"]))
        chexp = pl.min_horizontal(pl.col("childcare"), chmax, pl.col("wages").clip(0, None))
        df = df.with_columns(ga_chcr=chexp * float(cc["rate"]))
    elif p.in_effect("child_care_credit_federal_share"):
        rate = p.num("child_care_credit_federal_share")
        chcare = pl.min_horizontal(pl.col("federal_chcr").clip(0, None), pl.col("tax_before_credits").clip(0, None))
        df = df.with_columns(ga_chcr=rate * chcare)
    else:
        df = df.with_columns(ga_chcr=pl.lit(0.0))

    if p.in_effect("low_income_credit_flat"):
        # Heads of household count as two taxpayers.
        txp = pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(1.0).otherwise(2.0)
        lic = p.value("low_income_credit_flat")
        flat = float(lic["per_taxpayer"])
        thr = float(lic["agi_threshold_per_taxpayer"])
        df = df.with_columns(
            ga_ycred=(flat * txp - (pl.col("agi") - thr * txp)).clip(0, flat * txp)
        )
    elif p.in_effect("low_income_credit_per_exemption"):
        rows = [[float(t), float(r)] for t, r in p.value("low_income_credit_per_exemption")]
        expr = interpolate_table(pl.col("agi"), rows)
        df = df.with_columns(ga_ycred=expr * (pl.col("ga_taxpayers") + pl.col("depx") + aged_count()))
    else:
        df = df.with_columns(ga_ycred=pl.lit(0.0))
    df = df.with_columns(ga_ycred=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ga_ycred")))

    if p.value("low_income_credit_refundable"):
        df = df.with_columns(
            ga_statax=(pl.col("ga_statax") - pl.col("ga_chcr")).clip(0, None) - pl.col("ga_ycred")
        )
    else:
        df = df.with_columns(
            ga_statax=(pl.col("ga_statax") - pl.col("ga_chcr") - pl.col("ga_ycred")).clip(0, None)
        )

    df = df.with_columns(siitax=pl.col("ga_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("ga_agi"),
        exemptions=pl.col("ga_exemp"),
        standard_deduction=pl.col("ga_stded"),
        itemized_deductions=pl.col("ga_xitded"),
        taxable_income=pl.col("ga_taxinc"),
        child_care_credit=pl.col("ga_chcr"),
        credits=pl.col("ga_chcr") + pl.col("ga_ycred"),
        rate=rate_expr,
    )
