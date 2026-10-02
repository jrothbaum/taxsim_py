"""Georgia individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_input_adjustment, forced_standard, interpolate_table, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

GA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ga" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")

def compute_ga_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    state_year = "ga" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(GA_PARAMS, effective_year)

    df = df.with_columns(
        ga_sep=separate_divisor(),
        # Taxpayers (`data(7)`): 2 only on joint returns.
        ga_taxpayers=taxpayer_count(),
    )

    # Tax before credits is read as `comnew(52)`, which is deflated.
    df = deflate_for_extrapolation(df, flate, extra=("tax_before_credits",))

    # --- AGI ---
    df = df.with_columns(ga_agi=pl.col("agi"))

    # 2020: unemployment compensation federal excluded is taxable
    # (`data(82)-comnew(78)`).
    if effective_year == 2020:
        ui_total = unemployment_total()
        df = df.with_columns(
            ga_agi=pl.col("ga_agi") + ui_total - pl.col("taxable_unemployment")
        )


    # 1982-1986: "Georgia used 1981 federal law" - the IRA adjustment
    # (`stira`) and the two-earner deduction (`comnew(32)`) are added back.
    if 1982 <= effective_year <= 1986:
        two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        twoded = (
            two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).clip(0, two_earner_cap)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + twoded)
        # `stira`: $500 less the federal IRA limit (earnings up to $2,000,
        # $4,000 joint), added back when positive - so a return with little
        # earned income adds up to $500 (IRA contributions are not an input).
        earnings = pl.col("wages") + (pl.col("psemp") + pl.col("ssemp") + pl.col("otherprop").clip(0, None)).clip(0, None)
        ira = p["ira_addback_1982_1986"]
        iramax = pl.when(files_joint()).then(float(ira["limit_joint"])).otherwise(float(ira["limit_other"]))
        iralim = pl.min_horizontal(earnings, iramax)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + (ira["amount"] - iralim).clip(0, ira["amount"]))
        # Georgia's unemployment compensation adjustment (`stutx`/`fdutx`).
        ui_total = unemployment_total()
        thresholds = p["unemployment_threshold_1982_1986"]
        gemp = by_filing_status(thresholds["georgia"])
        femp = by_filing_status(thresholds["federal"])
        stutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - gemp).clip(0, None), ui_total)
        fdutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - femp).clip(0, None), ui_total)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + (stutx - fdutx))

    # Retirement income exclusion for taxpayers 65 or older.
    aged = aged_count()
    rtmax = p.num("retirement_exclusion_max")
    rtexc = pl.min_horizontal(pl.col("agi"), rtmax * aged).clip(0, None)
    if 1986 <= effective_year <= 1988:
        rtexc = pl.when(
            pl.col("earned_income") > p["retirement_exclusion_earned_limit_1986"] * pl.col("ga_taxpayers")
        ).then(0.0).otherwise(rtexc)
    if effective_year >= 1989:
        cap = float(p["retirement_exclusion_earnings_cap"])
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
    if effective_year <= 1986:
        pe = float(p["personal_exemption_flat_pre1994"][1960])
        dep_xmp = p.num("personal_exemption_amount_xmp")
        ga_exemp = pl.col("ga_taxpayers") * pe + (pl.col("depx") + aged_count()) * dep_xmp
        hoh_bonus = float(p["hoh_dependent_bonus_pre1987"][1960])
        ga_exemp = ga_exemp + pl.when(
            (files_head_of_household()) & (pl.col("depx") > 0)
        ).then(hoh_bonus).otherwise(0.0)
        df = df.with_columns(ga_exemp=ga_exemp)
    elif effective_year <= 1993:
        xmp = p.num("personal_exemption_amount_xmp")
        df = df.with_columns(ga_exemp=xmp * (pl.col("ga_taxpayers") + pl.col("depx")))
    elif effective_year <= 1997:
        pe = float(p["personal_exemption_flat_pre1994"][1960])
        xmp = p.num("personal_exemption_amount_xmp")
        df = df.with_columns(ga_exemp=pl.col("ga_taxpayers") * pe + pl.col("depx") * xmp)
    elif effective_year <= 2002:
        xmp = p.num("personal_exemption_amount_xmp")
        df = df.with_columns(ga_exemp=xmp * (pl.col("ga_taxpayers") + pl.col("depx")))
    elif effective_year <= 2012:
        xmp = p.num("personal_exemption_amount_xmp")
        dep_flat = float(p["dependent_exemption_flat_2003plus"][2003])
        df = df.with_columns(ga_exemp=xmp * pl.col("ga_taxpayers") + pl.col("depx") * dep_flat)
    elif behavior.mode.value == "statutory" and effective_year >= 2024:
        # Georgia repealed the personal exemption with the flat-rate law but
        # kept the (raised) dependent exemption.
        df = df.with_columns(ga_exemp=pl.col("depx") * float(p["dependent_exemption_2024plus"]))
    else:
        xmp = p.num("personal_exemption_amount_xmp")
        dep_flat = float(p["dependent_exemption_flat_2003plus"][2003])
        married_sep_amt = float(p["personal_exemption_married_separate_2013plus"][2013])
        df = df.with_columns(
            ga_exemp=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(xmp * pl.col("ga_taxpayers"))
            .otherwise(married_sep_amt * pl.col("ga_taxpayers"))
            + pl.col("depx") * dep_flat
        )

    # --- Standard deduction ---
    if effective_year <= 1982:
        pct = float(p["standard_deduction_pct_pre1983"][1960])
        floor = float(p["standard_deduction_floor_pre1983"][1960])
        ceiling = float(p["standard_deduction_ceiling_pre1983"][1960])
        df = df.with_columns(
            ga_stded=(pct * pl.col("ga_agi")).clip(floor / pl.col("ga_sep"), ceiling / pl.col("ga_sep"))
        )
    elif effective_year <= 1986:
        is_married = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        pct_s = float(p["standard_deduction_pct_1983_1986_single_or_hoh"][1983])
        floor_s = float(p["standard_deduction_floor_1983_1986_single_or_hoh"][1983])
        ceiling_s = float(p["standard_deduction_ceiling_1983_1986_single_or_hoh"][1983])
        pct_m = float(p["standard_deduction_pct_1983_1986_married"][1983])
        floor_m = float(p["standard_deduction_floor_1983_1986_married"][1983])
        ceiling_m = float(p["standard_deduction_ceiling_1983_1986_married"][1983])
        stded_single = (pct_s * pl.col("ga_agi")).clip(floor_s, ceiling_s)
        stded_married = (pct_m * pl.col("ga_agi")).clip(floor_m / pl.col("ga_sep"), ceiling_m / pl.col("ga_sep"))
        df = df.with_columns(ga_stded=pl.when(is_married).then(stded_married).otherwise(stded_single))
    else:
        is_married = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        flat_s = p.num("standard_deduction_flat_1987plus_single_or_hoh")
        flat_m = p.num("standard_deduction_flat_1987plus_married")
        aged_std = p.num("aged_standard_deduction")
        df = df.with_columns(
            ga_stded=pl.when(is_married).then(flat_m / pl.col("ga_sep")).otherwise(flat_s) + aged_std * aged_count()
        )

    # --- Itemized deduction ---
    if effective_year <= 1986:
        # Federal itemized deductions (zero when not itemizing) less the
        # state income or sales tax deduction.
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

    # --- Bracket tax --- Only single returns (`nfile==1`) use the single
    # table; the others run `taxinc*sep` through the married table.
    is_married = pl.col("filing_status") != "single"
    if behavior.mode.value == "statutory" and effective_year >= 2024:
        statax = float(p["flat_rate_2024"]) * pl.col("ga_taxinc")
        rate_expr = pl.lit(float(p["flat_rate_2024"]))
        df = df.with_columns(ga_statax=statax)
    elif effective_year <= 2018:
        brackets_single = p["brackets_single_thru_2018"]
        brackets_married = p["brackets_married_thru_2018"]
    else:
        brackets_single = p["brackets_single_2019plus"]
        brackets_married = p["brackets_married_2019plus"]
    if not (behavior.mode.value == "statutory" and effective_year >= 2024):
        taxy = pl.col("ga_taxinc") * pl.col("ga_sep")
        stat_married = bracket_tax(taxy, brackets_married) / pl.col("ga_sep")
        stat_single = bracket_tax(pl.col("ga_taxinc"), brackets_single)
        df = df.with_columns(ga_statax=pl.when(is_married).then(stat_married).otherwise(stat_single))
        rate_expr = pl.when(is_married).then(
            bracket_rate(taxy, brackets_married)
        ).otherwise(bracket_rate(pl.col("ga_taxinc"), brackets_single))

    # --- Credits ---
    # Child/Dependent Care Credit: 1978-1986, and a share of the federal
    # credit before its liability limit from 2006.
    if 1978 <= effective_year <= 1986:
        cap_floor = float(p["child_care_credit_expense_cap_floor_pre1987"][1960])
        cap_ceiling = float(p["child_care_credit_expense_cap_ceiling_pre1987"][1960])
        rate = float(p["child_care_credit_rate_pre1987"][1960])
        chmax = (pl.col("depx") * p["child_care_credit_expense_per_dependent_pre1987"]).clip(cap_floor, cap_ceiling)
        earned_ish = (pl.col("wages")).clip(0, None)
        chexp = pl.min_horizontal(pl.col("childcare"), chmax, earned_ish)
        df = df.with_columns(ga_chcr=chexp * rate)
    elif effective_year >= 2006:
        rate = p.num("child_care_credit_rate_2006plus")
        chcare = pl.min_horizontal(pl.col("federal_chcr").clip(0, None), pl.col("tax_before_credits").clip(0, None))
        df = df.with_columns(ga_chcr=rate * chcare)
    else:
        df = df.with_columns(ga_chcr=pl.lit(0.0))

    # Low-Income Credit: a flat formula through 1986, none 1987-1991, and a
    # per-exemption table from 1992.
    if effective_year <= 1986:
        # `if(mst.eq.1.or.mst.eq.3.or.mst.eq.6) txp=1. else txp=2.`: head of
        # household (code 4) counts as 2.
        txp = pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(1.0).otherwise(2.0)
        flat = float(p["low_income_credit_flat_per_txp_pre1987"][1960])
        thr = float(p["low_income_credit_agi_threshold_per_txp_pre1987"][1960])
        df = df.with_columns(
            ga_ycred=(flat * txp - (pl.col("agi") - thr * txp)).clip(0, flat * txp)
        )
    elif effective_year >= 1992:
        rows = [[float(t), float(r)] for t, r in p["low_income_credit_table_1992plus"]]
        expr = interpolate_table(pl.col("agi"), rows)
        df = df.with_columns(ga_ycred=expr * (pl.col("ga_taxpayers") + pl.col("depx") + aged_count()))
    else:
        df = df.with_columns(ga_ycred=pl.lit(0.0))
    df = df.with_columns(ga_ycred=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ga_ycred")))

    # The solar energy credit has no TAXSIM input.
    df = df.with_columns(ga_solar=pl.lit(0.0))

    # The Low-Income Credit is refundable through 2009 and nonrefundable
    # after (`statax=max(0,statax-chcr-solar)-ycred`).
    if effective_year <= 2009:
        df = df.with_columns(
            ga_statax=(pl.col("ga_statax") - pl.col("ga_chcr") - pl.col("ga_solar")).clip(0, None) - pl.col("ga_ycred")
        )
    else:
        df = df.with_columns(
            ga_statax=(pl.col("ga_statax") - pl.col("ga_chcr") - pl.col("ga_solar") - pl.col("ga_ycred")).clip(0, None)
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
        credits=pl.col("ga_chcr") + pl.col("ga_solar") + pl.col("ga_ycred"),
        rate=rate_expr,
    )
