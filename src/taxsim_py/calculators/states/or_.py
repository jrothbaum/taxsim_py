"""Oregon individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    checkpoint,
    dividend_exclusion_addback,
    forced_standard,
    household_income,
    interpolate_table,
    pre1987_federal_itemizing,
    tier_values,
    unemployment_total,
    with_default,
    with_defaults,
    with_state_detail,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

OR_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "or" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(OR_PARAMS[name], year))


def compute_or_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Oregon income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = OR_PARAMS
    df = with_defaults(df, (
        "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "proptax", "otheritem", "mortgage", "depx",
        "childcare", "charity_cash", "state_sales_or_income_tax_ded", "taxable_unemployment", "taxable_income",
        "earned_income", "itemized_deduction", "regular_tax", "fiitax", "ccc", "ccc_uncapped", "odc", "actc",
        "eitc", "making_work_pay", "cares", "credit", "pre1987_taxbc", "pre1987_chcr", "pre1987_twoded",
        "pre1987_pref", "pensions", "gssi", "taxable_social_security", "federal_elder",
        "federal_chcr",
    ))
    df = with_default(df, "itemizes", False)
    dividend_adjustment = float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], y))
    record_adjustment = float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_record_adjustment"], y))
    df = df.with_columns(or_ui=unemployment_total(), or_hh=household_income(dividend_adjustment, record_adjustment))
    df = deflate_for_extrapolation(
        df, flate,
        [
            "federal_chcr", "pwages", "swages", "dividends", "intrec", "stcg", "ltcg", "or_ui", "proptax", "otheritem", "mortgage",
            "childcare", "charity_cash", "state_sales_or_income_tax_ded", "agi", "taxable_unemployment",
            "taxable_income", "earned_income", "itemized_deduction", "regular_tax", "fiitax", "ccc", "odc", "actc",
            "eitc", "making_work_pay", "pensions", "gssi", "taxable_social_security", "federal_elder", "or_hh",
        ],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    single_like = (status == "single") | is_sep
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    fed_agi = pl.col("agi")
    # The federal exemption count is deflated with dollar amounts in projected years.
    exemps = federal_exemption_count(y) / flate
    salt = pl.col("state_sales_or_income_tax_ded")

    # --- AGI ---
    agi = fed_agi
    if 1982 <= y <= 1986:
        divall = pl.col("dividends") + dividend_adjustment - dividend_exclusion_addback(y, dividend_adjustment)
        exclusion = pl.min_horizontal(divall + pl.col("intrec"), p["interest_dividend_exclusion_1982_1986"] * txp)
        agi = agi + pl.col("pre1987_twoded") - exclusion.clip(0, None)
    if 1981 <= y <= 1984:
        threshold = by_filing_status(p["unemployment_threshold_1981_1984"])
        untax = pl.min_horizontal(
            p["unemployment_taxable_share_1981_1984"] * (agi - threshold).clip(0, None), pl.col("or_ui")
        )
        agi = agi + pl.col("taxable_unemployment") - untax
    if y >= 1984:
        agi = agi - pl.col("taxable_social_security")
    df, (agi,) = checkpoint(df, or_agi=agi.clip(0, None))

    # --- Federal values ---
    if y <= 1986:
        gross, _, _ = pre1987_federal_itemizing(y)
        deducp = gross
        taxbc = pl.col("pre1987_taxbc")
        fed_credit = pl.col("credit")
        fed_ccc = pl.col("federal_chcr")
    else:
        deducp = pl.col("itemized_deduction")
        taxbc = pl.col("regular_tax")
        fed_ccc = pl.col("federal_chcr")
        # Nonrefundable federal credits (`comnew(58)`). The federal routine
        # only fills them from 1998 (0 before, logged), and none were
        # nonrefundable in 2021 (child and child care credits were refundable).
        fed_credit = pl.lit(0.0) if y <= 1997 or year == 2021 else fed_ccc + pl.col("odc")

    # --- Federal tax subtraction, limited by AGI ---
    fedtax = (taxbc - fed_credit).clip(0, None)
    if y == 1997:
        fedtax = pl.when(forced_standard()).then(taxbc.clip(0, None)).otherwise(fedtax)
    if y == 2008:
        rebate = p["rebate_2008"]
        fedtax = (fedtax - rebate["taxpayer"] * txp - rebate["dependent"] * depx).clip(0, None)
    fedtax = (fedtax - pl.col("making_work_pay")).clip(0, None)
    fedtax = (fedtax - pl.col("cares")).clip(0, None)
    if y == 2021:
        fedtax = (fedtax - pl.col("actc")).clip(0, None)
    units = pl.when(is_joint | is_hoh).then(2.0).otherwise(1.0)
    fedlim = pl.lit(0.0)
    for bound, divisor in reversed(p["federal_tax_limit_phaseout"]):
        fedlim = pl.when(agi < bound * units).then(_p("federal_tax_limit", y) / sep / divisor).otherwise(fedlim)
    df, (fedtax,) = checkpoint(df, or_fedtax=fedtax.clip(pl.lit(0.0), fedlim))

    # --- Standard deduction ---
    if y <= 1986:
        c = p["standard_deduction_1977_1986"]
        stded = (c["rate"] * agi).clip(c["low"] / sep, c["high"] / sep)
    elif y <= 2001:
        stded = by_filing_status(p["standard_deduction_1987_2001"])
    else:
        stded = by_filing_status(p["standard_deduction_2002"]) * _p("standard_deduction_index_2002", y)
    if y >= 1987:
        stded = stded + aged * by_filing_status(p["standard_deduction_aged_addition"])
        # Returns with no taxpayer (dependent filers).
        if y <= 2001:
            dependent_std = pl.lit(_p("dependent_standard_deduction_floor", y))
        else:
            dependent_std = pl.max_horizontal(
                pl.lit(_p("dependent_standard_deduction_floor", y)),
                pl.col("earned_income") + float(p["dependent_standard_deduction_earned_addition"]),
            )
        stded = pl.when(txp < 1).then(dependent_std).otherwise(stded)

    # --- Itemized deductions ---
    tx = salt if y <= 2017 else pl.lit(0.0)
    if y >= 1991:
        if y <= 2017:
            xlin8 = tx + pl.col("proptax") + pl.col("otheritem")
        else:
            xlin8 = pl.min_horizontal(p["salt_cap_2018"] / sep, pl.col("proptax") + pl.col("otheritem"))
        xtot = xlin8 + pl.col("mortgage") + pl.col("charity_cash")
        if y <= 2017:
            if y <= 2012:
                phas92 = p["itemized_limit_threshold"] * _p("itemized_limit_index", y) / sep
            else:
                phas92 = (
                    _p("itemized_limit_index_2013", y) * p["itemized_limit_threshold_2013"]
                    * by_filing_status(p["itemized_limit_status_factor_2013"])
                )
            over = fed_agi - phas92
            xconst = pl.min_horizontal(p["itemized_limit_share"] * xtot, p["itemized_limit_rate"] * over)
            tx = pl.when((xtot > 0) & (over > 0)).then((xtot - xconst) / xtot * tx).otherwise(tx)
    if y <= 2017:
        xitded = (deducp - tx).clip(0, None)
        if y >= 1992:
            # Taxpayers 65 or older keep the state income tax deduction.
            xitded = pl.when(aged > 0).then(deducp.clip(0, None)).otherwise(xitded)
    else:
        xitded = xtot
        if y == 2020:
            xitded = xitded - pl.when(pl.col("itemizes")).then(0.0).otherwise(
                pl.col("charity_cash").clip(None, p["charity_nonitemizer_2020"])
            )
    xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    deduc = pl.max_horizontal(stded, xitded)
    exemp = exemps * _p("exemption_amount", y) if y <= 1982 else pl.lit(0.0)
    df, (taxinc,) = checkpoint(df, or_taxinc=(agi - deduc - exemp - fedtax).clip(0, None))

    # --- Tax ---
    schedule = resolve_year(p["brackets"], y)
    if "split" in schedule:
        # Joint returns and heads of household: tax on half the income, doubled.
        num = pl.when(single_like).then(1.0).otherwise(2.0)
        statax = num * bracket_tax(taxinc / num, schedule["split"])
        table_rate = bracket_rate(taxinc / num, schedule["split"])
    else:
        statax = pl.when(single_like).then(bracket_tax(taxinc, schedule["single"])).otherwise(
            bracket_tax(taxinc, schedule["married"])
        )
        table_rate = pl.when(single_like).then(bracket_rate(taxinc, schedule["single"])).otherwise(
            bracket_rate(taxinc, schedule["married"])
        )
    if y <= 1986:
        prefs = pl.col("pre1987_pref")
        qualifies = (prefs > p["minimum_tax_preference_threshold"] / sep) | (
            (agi > p["minimum_tax_agi_threshold"] / sep) & (prefs > p["minimum_tax_agi_preference_threshold"] / sep)
        )
        statax = statax + pl.when(qualifies).then(bracket_tax(prefs, p["minimum_tax_brackets"])).otherwise(0.0)
    df, (statax,) = checkpoint(df, or_taxbc=statax.clip(0, None))
    taxbc_state = statax

    # --- Nonrefundable credits ---
    ecred = pl.lit(0.0)
    chcr = pl.lit(0.0)
    share = p["child_care_federal_share"]
    if y <= 1978:
        ecred = p["two_earner_credit_rate"] * pl.col("pre1987_twoded")
        chcr = share * fed_ccc
    elif y <= 1986:
        taxmax = (pl.col("fiitax") - pl.col("credit")).clip(0, None)
        ecred = pl.min_horizontal(p["two_earner_credit_rate"] * taxmax, p["two_earner_credit_rate"] * pl.col("pre1987_twoded"))
        chcr = pl.min_horizontal(share * taxmax, share * fed_ccc)
    elif y <= 1988:
        chcr = share * fed_ccc
    if 1987 <= y <= 2015:
        # Credit for the elderly: a share of the federal credit.
        ecred = float(p["elderly_credit_share"]) * pl.col("federal_elder")
    if 1989 <= y <= 2015:
        children = depx.clip(None, 2) * _p("child_care_expense_per_child", y)
        expenses = pl.min_horizontal(pl.col("earned_income"), pl.col("childcare"), children)
        rate = interpolate_table(pl.col("taxable_income").clip(0, None), p["child_care_rate"])
        chcr = pl.when(fed_ccc > 0).then(rate * expenses).otherwise(0.0)

    gcred = pl.lit(0.0)
    if y >= 1983:
        gcred = _p("exemption_credit", y) * exemps
        if y >= 2013:
            units_credit = pl.when(is_joint | is_hoh).then(2.0).otherwise(1.0)
            gcred = pl.when(fed_agi <= p["exemption_credit_income_limit_2013"] * units_credit).then(gcred).otherwise(0.0)
        if 2007 <= y <= 2012:
            factors = p["exemption_credit_phaseout_factor"]
            base = p["itemized_limit_threshold"] * _p("itemized_limit_index", y)
            phasa = (
                pl.when(is_hoh).then(factors["head_of_household"] * base)
                .when(is_joint | is_sep).then(factors["married"] * base / sep)
                .otherwise(base)
            )
            steps = ((fed_agi - phasa) / (p["exemption_credit_phaseout_step"] / sep) + 1).floor()
            reduced = pl.max_horizontal(
                _p("exemption_credit_minimum", y) * exemps,
                gcred * (1 - steps * p["exemption_credit_phaseout_rate"]),
            )
            gcred = pl.when(fed_agi > phasa).then(reduced).otherwise(gcred)

    # Retirement income credit for taxpayers 65 or older (1991+).
    rcred = pl.lit(0.0)
    if y >= 1991:
        r = p["retirement_income_credit"]
        room = (
            (r["base_per_taxpayer"] * txp - pl.col("gssi")).clip(0, None)
            - (pl.col("or_hh") - r["income_start_per_taxpayer"] * txp).clip(0, None)
        ).clip(0, None)
        rcred = pl.when((aged > 0) & (agi <= r["agi_limit_per_taxpayer"] * txp)).then(
            r["rate"] * pl.min_horizontal(pl.col("pensions"), room)
        ).otherwise(0.0)

    earncr = _p("eitc_rate", y) * pl.col("eitc") if y >= 1997 else pl.lit(0.0)

    # --- Working family child care credit 1997-2015 ---
    numhh = exemps.clip(1, 8).floor()
    famcr = pl.lit(0.0)
    if 1997 <= y <= 2015:
        index = _p("working_family_index", y)
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        capgn = pl.max_horizontal(pl.col("stcg") + pl.col("ltcg"), -loss_limit / flate / sep)
        investment = pl.col("dividends") + dividend_adjustment + pl.col("intrec") + capgn.clip(0, None)
        eligible = (
            ~is_sep & (pl.col("earned_income") >= _p("working_family_earned_min", y))
            & (investment < _p("working_family_investment_max", y))
        )
        shares = p["working_family_shares"]
        share_by_size = pl.lit(0.0)
        for size, thresholds in reversed(list(enumerate(p["working_family_thresholds"], start=1))):
            rows = [[t * index, s] for t, s in zip(thresholds, shares)] + [[1.0e20, shares[-1]]]
            share_by_size = pl.when(numhh == size).then(interpolate_table(fed_agi, rows)).otherwise(share_by_size)
        famcr = pl.when(eligible).then(pl.col("childcare") * share_by_size).otherwise(0.0)
    df, (chcr, gcred, earncr, famcr) = checkpoint(df, or_chcr=chcr, or_gcred=gcred, or_earncr=earncr, or_famcr=famcr)

    credit = ecred + chcr + gcred + rcred
    if 1997 <= y <= 2005:
        credit = credit + earncr
        if y <= 2002:
            credit = credit + famcr
    statax = (statax - credit).clip(0, None)
    if y >= 2003:
        statax = statax - famcr
    if y >= 2006:
        statax = statax - earncr

    credits = credit + (famcr if y >= 2003 else 0.0) + (earncr if y >= 2006 else 0.0)

    # --- Working family household and dependent care credit 2016 on ---
    if y >= 2016:
        num = exemps.floor()
        poverty = resolve_year(p["poverty_line"], y)
        agi_limit = resolve_year(p["dependent_care_agi_limit"], y)
        children = depx.clip(None, 2).floor()
        expens = pl.min_horizontal(
            pl.col("childcare"), p["dependent_care_expense_per_child"] * children, pl.col("earned_income")
        )
        expens = pl.when(is_joint).then(
            pl.min_horizontal(expens, pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).otherwise(expens)
        pov = poverty[0] + poverty[1] * (num - 1)
        base = agi.clip(0, None)
        rows = p["dependent_care_shares"]
        (share_of_poverty,) = tier_values(base / pov, [r[0] for r in rows], [r[1] for r in rows])
        in_range = (base > 0) & (base <= rows[-1][0] * pov) & (base < agi_limit[0] + agi_limit[1] * (numhh - 2))
        wfhdc = pl.when((numhh > 1) & (num > 1) & in_range).then(share_of_poverty * expens).otherwise(0.0)
        statax = statax - wfhdc
        credits = credits + wfhdc

    statax = pl.when(statax > 0).then(statax * _p("tax_multiplier", y)).otherwise(statax)
    if y == 2019:
        # The 2019 surplus credit; TAXSIM also keeps it for later records (logged).
        statax = statax - p["kicker_2019"] * taxbc_state
        credits = credits + p["kicker_2019"] * taxbc_state

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        child_care_credit=chcr,
        eic=earncr,
        credits=credits,
        rate=table_rate,
    )
