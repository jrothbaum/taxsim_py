"""Georgia individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, with_default as _with_default, forced_standard, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

GA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ga" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

def compute_ga_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = GA_PARAMS
    df = with_defaults(df, ("federal_chcr", "proptax", "otheritem", "mortgage", "depx", "dividends", "intrec", "childcare", "ui", "pui", "sui"))
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "tax_before_credits")
    df = _with_default(df, "taxable_unemployment")
    df = with_defaults(df, ("taxable_social_security", "earned_income", "pre1987_deduc"))

    df = df.with_columns(
        ga_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        # `data(7)` - self/spouse exemption unit count (1, or 2 ONLY for
        # married_joint - matching every other state's own established
        # convention).
        ga_texp=taxpayer_count(),
    )

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`).
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "federal_chcr",
            "pwages", "swages", "wages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "salt_capped", "state_sales_or_income_tax_ded",
            "itemized_deduction", "ccc", "tax_before_credits", "taxable_social_security", "pensions",
            "otherprop", "scorp", "earned_income", "psemp", "ssemp", "pre1987_deduc",
        ],
    )

    # --- AGI ---
    df = df.with_columns(ga_agi=pl.col("agi"))
    # `data(22)` confirmed permanently $0 elsewhere in this project (same
    # state-tax-refund field AL/DC already confirmed inert) - the
    # `law<=1988` subtraction is a no-op, no code needed.

    # 2020: Georgia does NOT conform to federal's CARES/ARPA UI exclusion
    # - add back whatever federal excluded (`data(82)-comnew(78)`, the
    # raw total minus the federally-taxable portion).
    if effective_year == 2020:
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        df = df.with_columns(
            ga_agi=pl.col("ga_agi") + ui_total - pl.col("taxable_unemployment")
        )

    # 2021 $300-cash-contribution addback confirmed permanently inert (no
    # `charity_cash` input this project's schema ever populates - the
    # same gap already documented at the federal level).

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
        iramax = pl.when(pl.col("filing_status") == "married_joint").then(4000.0).otherwise(2000.0)
        iralim = pl.min_horizontal(earnings, iramax)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + (500.0 - iralim).clip(0, 500.0))
        # Georgia's own state-vs-federal unemployment-compensation
        # threshold adjustment (`stutx`/`fdutx`) - a real, GA-specific
        # mechanic distinct from the 2020 UI provision above, using the
        # SAME `data(82)=max(ui,pui+sui)` total.
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        gmp = {1: 20000.0, 2: 25000.0, 3: 20000.0, 4: 20000.0, 5: 20000.0, 6: 0.0, 7: 20000.0}
        fmp = {1: 12000.0, 2: 18000.0, 3: 20000.0, 4: 20000.0, 5: 20000.0, 6: 0.0, 7: 12000.0}
        gemp = (
            pl.when(pl.col("filing_status") == "married_joint").then(gmp[2])
            .when(pl.col("filing_status") == "head_of_household").then(gmp[4])
            .when(pl.col("filing_status") == "married_separate").then(gmp[6])
            .otherwise(gmp[1])
        )
        femp = (
            pl.when(pl.col("filing_status") == "married_joint").then(fmp[2])
            .when(pl.col("filing_status") == "head_of_household").then(fmp[4])
            .when(pl.col("filing_status") == "married_separate").then(fmp[6])
            .otherwise(fmp[1])
        )
        stutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - gemp).clip(0, None), ui_total)
        fdutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - femp).clip(0, None), ui_total)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + (stutx - fdutx))

    # Retirement income exclusion for taxpayers 65 or older.
    aged = aged_count()
    rtmax = float(resolve_year(p["retirement_exclusion_max"], effective_year))
    rtexc = pl.min_horizontal(pl.col("agi"), rtmax * aged).clip(0, None)
    if 1986 <= effective_year <= 1988:
        rtexc = pl.when(
            pl.col("earned_income") > p["retirement_exclusion_earned_limit_1986"] * pl.col("ga_texp")
        ).then(0.0).otherwise(rtexc)
    if effective_year >= 1989:
        cap = float(p["retirement_exclusion_earnings_cap"])
        sep = pl.col("ga_sep")
        capgn = pl.max_horizontal(pl.col("stcg") + pl.col("ltcg"), -3000.0 / sep)
        schedule_e = pl.col("otherprop") + pl.col("scorp")
        investment = pl.col("intrec") + pl.col("dividends") + 0.001 + schedule_e
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
        rhyw = 0.5 * investment + pl.col("pensions") + pl.when(capgn >= 0).then(0.5 * capgn / sep).otherwise(0.25 * capgn / sep)
        couple_one = pl.min_horizontal(pl.lit(rtmax), rterw + rhyw) + rterh
        couple_two = pl.min_horizontal(pl.lit(rtmax), rterw + 0.5 * rhy) + pl.min_horizontal(pl.lit(rtmax), rterh + 0.5 * rhy)
        rtexc = (
            pl.when(pl.col("ga_texp") == 1).then(single)
            .when(pl.col("ga_texp") > 1).then(pl.when(aged < 2).then(couple_one).otherwise(couple_two))
            .otherwise(rtexc)
        )
    df = df.with_columns(ga_agi=pl.col("ga_agi") - pl.when(aged > 0).then(rtexc).otherwise(0.0))
    # Social Security benefits are exempt from 1988.
    if effective_year >= 1988:
        df = df.with_columns(ga_agi=pl.col("ga_agi") - pl.col("taxable_social_security"))

    # --- Exemptions --- (`old`=elderly+blind confirmed permanently $0)
    if effective_year <= 1986:
        pe = float(p["personal_exemption_flat_pre1994"][1960])
        dep_xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        ga_exemp = pl.col("ga_texp") * pe + (pl.col("depx") + aged_count()) * dep_xmp
        hoh_bonus = float(p["hoh_dependent_bonus_pre1987"][1960])
        ga_exemp = ga_exemp + pl.when(
            (pl.col("filing_status") == "head_of_household") & (pl.col("depx") > 0)
        ).then(hoh_bonus).otherwise(0.0)
        df = df.with_columns(ga_exemp=ga_exemp)
    elif effective_year <= 1993:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        df = df.with_columns(ga_exemp=xmp * (pl.col("ga_texp") + pl.col("depx")))
    elif effective_year <= 1997:
        pe = float(p["personal_exemption_flat_pre1994"][1960])
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        df = df.with_columns(ga_exemp=pl.col("ga_texp") * pe + pl.col("depx") * xmp)
    elif effective_year <= 2002:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        df = df.with_columns(ga_exemp=xmp * (pl.col("ga_texp") + pl.col("depx")))
    elif effective_year <= 2012:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        dep_flat = float(p["dependent_exemption_flat_2003plus"][2003])
        df = df.with_columns(ga_exemp=xmp * pl.col("ga_texp") + pl.col("depx") * dep_flat)
    else:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        dep_flat = float(p["dependent_exemption_flat_2003plus"][2003])
        married_sep_amt = float(p["personal_exemption_married_separate_2013plus"][2013])
        df = df.with_columns(
            ga_exemp=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(xmp * pl.col("ga_texp"))
            .otherwise(married_sep_amt * pl.col("ga_texp"))
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
        flat_s = float(resolve_year(p["standard_deduction_flat_1987plus_single_or_hoh"], effective_year))
        flat_m = float(resolve_year(p["standard_deduction_flat_1987plus_married"], effective_year))
        aged_std = float(resolve_year(p["aged_standard_deduction"], effective_year))
        df = df.with_columns(
            ga_stded=pl.when(is_married).then(flat_m / pl.col("ga_sep")).otherwise(flat_s) + aged_std * aged_count()
        )

    # --- Itemized deduction --- (see module docstring point 1)
    if effective_year <= 1986:
        # Federal itemized deductions (zero when not itemizing) less the
        # state income or sales tax deduction.
        df = _with_default(df, "pre1987_deduc")
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

    # --- Bracket tax --- (see module docstring point 2). `nfile==1`
    # (single ONLY) uses the single table directly - married_joint,
    # married_separate, AND head_of_household (nfile 2/2/3, all !=1) run
    # `taxinc*sep` through the SAME doubled-threshold married table, a
    # real, easy-to-miss detail confirmed via oracle probe (a $15,000-
    # wages/HoH/1-dependent/2014 case only matches using the married
    # table, not a "single-or-hoh" one this project's naming convention
    # elsewhere might suggest).
    is_married = pl.col("filing_status") != "single"
    if effective_year <= 2018:
        brackets_single = p["brackets_single_thru_2018"]
        brackets_married = p["brackets_married_thru_2018"]
    else:
        brackets_single = p["brackets_single_2019plus"]
        brackets_married = p["brackets_married_2019plus"]
    taxy = pl.col("ga_taxinc") * pl.col("ga_sep")
    stat_married = bracket_tax(taxy, brackets_married) / pl.col("ga_sep")
    stat_single = bracket_tax(pl.col("ga_taxinc"), brackets_single)
    df = df.with_columns(ga_statax=pl.when(is_married).then(stat_married).otherwise(stat_single))
    rate_expr = pl.when(is_married).then(
        bracket_rate(taxy, brackets_married)
    ).otherwise(bracket_rate(pl.col("ga_taxinc"), brackets_single))

    # --- Credits ---
    # Child/Dependent Care Credit: real 1978-1986, NOT allowable
    # 1987-2005, a percentage of the federal credit (pre-cap) 2006+.
    if 1978 <= effective_year <= 1986:
        cap_floor = float(p["child_care_credit_expense_cap_floor_pre1987"][1960])
        cap_ceiling = float(p["child_care_credit_expense_cap_ceiling_pre1987"][1960])
        rate = float(p["child_care_credit_rate_pre1987"][1960])
        chmax = (pl.col("depx") * 2000.0).clip(cap_floor, cap_ceiling)
        earned_ish = (pl.col("wages")).clip(0, None)
        chexp = pl.min_horizontal(pl.col("childcare"), chmax, earned_ish)
        df = df.with_columns(ga_chcr=chexp * rate)
    elif effective_year >= 2006:
        rate = float(resolve_year(p["child_care_credit_rate_2006plus"], effective_year))
        chcare = pl.min_horizontal(pl.col("federal_chcr").clip(0, None), pl.col("tax_before_credits").clip(0, None))
        df = df.with_columns(ga_chcr=rate * chcare)
    else:
        df = df.with_columns(ga_chcr=pl.lit(0.0))

    # Low-Income Credit: real flat formula <=1986, does NOT exist
    # 1987-1991, a real per-exemption-unit interpolation table 1992+.
    if effective_year <= 1986:
        # `if(mst.eq.1.or.mst.eq.3.or.mst.eq.6) txp=1. else txp=2.` -
        # `mst.eq.3` is the dead internal head_of_household code this
        # project never produces (HoH is really mst 4 or 7 - see the
        # `filing()`/`nfile` convention already established for DC/CT),
        # so this check never actually catches HoH: it falls through to
        # `txp=2`, the SAME as married_joint - confirmed via oracle probe
        # (a $1-wages/HoH/1977 case only matches a full $30 refundable
        # credit, i.e. `txp=2`, not the $15 a `txp=1` reading would give).
        txp = pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(1.0).otherwise(2.0)
        flat = float(p["low_income_credit_flat_per_txp_pre1987"][1960])
        thr = float(p["low_income_credit_agi_threshold_per_txp_pre1987"][1960])
        df = df.with_columns(
            ga_ycred=(flat * txp - (pl.col("agi") - thr * txp)).clip(0, flat * txp)
        )
    elif effective_year >= 1992:
        rows = [[float(t), float(r)] for t, r in p["low_income_credit_table_1992plus"]]
        thresholds = [r[0] for r in rows[:-1]]
        values = [r[1] for r in rows]
        expr = pl.lit(values[-1])
        for i in range(len(thresholds) - 1, -1, -1):
            t_hi = thresholds[i]
            v_hi = values[i]
            if i == 0:
                below = pl.lit(v_hi)
            else:
                t_lo = thresholds[i - 1]
                v_lo = values[i - 1]
                w = (pl.col("agi") - t_lo) / (t_hi - t_lo)
                below = (w * v_lo + (1 - w) * v_hi) if v_hi > v_lo else (w * v_hi + (1 - w) * v_lo)
            expr = pl.when(pl.col("agi") < t_hi).then(below).otherwise(expr)
        df = df.with_columns(ga_ycred=expr * (pl.col("ga_texp") + pl.col("depx") + aged_count()))
    else:
        df = df.with_columns(ga_ycred=pl.lit(0.0))
    df = df.with_columns(ga_ycred=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ga_ycred")))

    # Solar-energy credit confirmed permanently $0 (see module docstring).
    df = df.with_columns(ga_solar=pl.lit(0.0))

    # `if(law.le.2009) statax=max(0,statax-chcr-solar)-ycred; else
    # statax=max(0,statax-chcr-solar-ycred)` - the Low-Income Credit is
    # REFUNDABLE through 2009 (the $0 floor applies BEFORE subtracting
    # it, so `ycred` can push `siitax` negative), nonrefundable 2010+
    # (the floor applies AFTER). Missing this era split entirely clamped
    # every year at 0, silently turning off the refund for every pre-
    # 2010 low-income case - caught by a $1-income single filer showing
    # a real -$26 refund for 2000.
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
