"""Wisconsin individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    checkpoint,
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

WI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "wi" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(WI_PARAMS[name], year))


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[start * factor, rate] for start, rate in brackets]


def _low_income_deduction(rows: list[list[float]], agi: pl.Expr, depx: pl.Expr, depadd: pl.Expr) -> pl.Expr:
    """TAXSIM `witab`: the amount for the first row whose bound is at least AGI.

    Row amounts are by number of dependents; more dependents than the row
    lists take the last amount plus the add-on for each extra dependent.
    """
    width = len(rows[0]) - 1
    amounts = tier_values(
        agi, [float(r[0]) for r in rows], *[[float(r[k]) for r in rows] for k in range(1, width + 1)]
    )
    value = amounts[-1]
    for count in reversed(range(width - 1)):
        value = pl.when(depx == count).then(amounts[count]).otherwise(value)
    return value + (depx - (width - 1)).clip(0, None) * depadd


def _standard_deduction(rows: list[list[float]], agi: pl.Expr, index: float) -> pl.Expr:
    """First row whose AGI bound is at least AGI: max(0, amount - rate * (AGI - start))."""
    uppers = [float(r[0]) if r[0] >= 1.0e19 else float(r[0]) * index for r in rows]
    amount, rate, start = tier_values(
        agi, uppers, [float(r[1]) * index for r in rows], [float(r[2]) for r in rows], [float(r[3]) * index for r in rows]
    )
    return (amount - rate * (agi - start)).clip(0, None)


def compute_wi_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Wisconsin income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = WI_PARAMS
    df = with_defaults(df, (
        "stcg", "ltcg", "ui", "pui", "sui", "proptax", "mortgage", "charity_cash", "childcare", "rentpaid",
        "depx", "dep13", "psemp", "ssemp", "pbusinc", "pprofinc", "sbusinc", "sprofinc", "gssi",
        "taxable_unemployment", "taxable_social_security", "earned_income", "federal_chcr", "eitc",
        "pre1987_capgn", "pre1987_capded", "pre1987_earncr", "pre1987_twoded", "pre1987_almtax", "amt_income",
        "state_sales_or_income_tax_ded", "itemized_before_limit",
    ))
    df = with_default(df, "itemizes", False)
    adjustments = [
        float(resolve_year(STATE_ADJUSTMENT_PARAMS[name], y))
        for name in ("household_income_dividend_adjustment", "household_income_record_adjustment")
    ]
    # Household income (`hy`) is read before projected years are deflated.
    df = df.with_columns(wi_hy=household_income(*adjustments), wi_ui=unemployment_total())
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "stcg", "ltcg", "wi_ui", "proptax", "mortgage", "charity_cash", "childcare",
            "rentpaid", "psemp", "ssemp", "pbusinc", "pprofinc", "sbusinc", "sprofinc", "gssi", "agi",
            "taxable_unemployment", "taxable_social_security", "earned_income", "federal_chcr", "eitc",
            "amt_income", "state_sales_or_income_tax_ded", "itemized_before_limit",
        ],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    single_like = (status == "single") | (status == "head_of_household")
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    dependent_filer = is_dependent_filer()
    fed_agi = pl.col("agi")
    ss_taxable = pl.col("taxable_social_security")
    ui = pl.col("wi_ui")
    stcg, ltcg = pl.col("stcg"), pl.col("ltcg")
    fullcg = stcg + ltcg

    # --- Wisconsin AGI ---
    agi = fed_agi
    if y == 2020:
        agi = agi + ui - pl.col("taxable_unemployment")
    if y == 2021:
        base = pl.when(is_joint).then(p["unemployment_base_2021"]["married_joint"]).otherwise(
            p["unemployment_base_2021"]["other"]
        )
        half = p["unemployment_share"] * (fed_agi - (base + ss_taxable)).clip(0, None)
        agi = agi - (ui - pl.min_horizontal(ui, half)).clip(0, None)
    # Capital gains and losses.
    if y <= 1981:
        c = p["capital_loss_1981"]
        loss = pl.when(stcg < 0).then(ltcg).otherwise(fullcg)
        limited = pl.max_horizontal(loss, -c["limit_per_taxpayer"] * txp)
        change = pl.min_horizontal(limited - c["share"] * loss, pl.lit(0.0))
        in_range = (loss > -c["range_per_taxpayer"] * txp) & (loss < 0)
        agi = agi + pl.when((fullcg <= 0) & in_range).then(change).otherwise(0.0)
    elif y >= 1987:
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        # The federal loss limit reaches the state deflated in projected years.
        caplss = pl.max_horizontal(fullcg, -loss_limit / flate / sep).abs()
        closswi = pl.min_horizontal(caplss, pl.lit(float(p["capital_loss_limit_1987"])), agi.clip(0, None))
        agi = agi + pl.when(fullcg <= 0).then(caplss - closswi).otherwise(0.0)
    if y <= 1983:
        agi = agi + pl.when(fullcg > 0).then(_p("capital_gain_exclusion_addback", y) * pl.col("pre1987_capded")).otherwise(0.0)
    elif y >= 1987:
        agi = agi - pl.when(fullcg > 0).then(_p("capital_gain_exclusion", y) * pl.min_horizontal(fullcg, ltcg)).otherwise(0.0)
    if 1982 <= y <= 1986:
        agi = agi + pl.col("pre1987_twoded")
    if 1986 <= y <= 2007:
        agi = agi - pl.when(pl.col("gssi") > 0).then((ss_taxable - pl.col("gssi") / 2).clip(0, None)).otherwise(0.0)
    elif y >= 2008:
        agi = agi - ss_taxable
    # Unemployment compensation up to half of federal AGI above a base.
    ui_base = by_filing_status(p["unemployment_base"])
    ui_taxable = pl.min_horizontal(ui, p["unemployment_share"] * (fed_agi - ui_base - ss_taxable).clip(0, None))
    if y != 2020:
        agi = agi - pl.when(ui > 0).then(ui - ui_taxable).otherwise(0.0)
    # Child and dependent care expenses (2011 on, with a federal credit).
    if y >= 2011:
        children = pl.when(pl.col("dep13") > 0).then(pl.col("dep13")).otherwise(depx).clip(None, 2)
        child = pl.min_horizontal(pl.col("childcare"), _p("child_care_subtraction_per_child", y) * children)
        child = pl.when(is_joint).then(
            pl.min_horizontal(child, pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).otherwise(child)
        agi = agi - pl.when(pl.col("federal_chcr") > 0).then(child).otherwise(0.0)
    df, (agi,) = checkpoint(df, wi_agi=agi)

    # --- Standard deduction ---
    depadd = interpolate_table(agi, resolve_year(p["dependent_addition"], y))
    if y <= 1978:
        c = p["standard_deduction_1978"]
        stded = (c["rate"] * agi).clip(0, c["cap"]) + depadd
    elif y <= 1985:
        stded = pl.lit(0.0)
    else:
        tables = resolve_year(p["standard_deduction"], y)
        index = _p("standard_deduction_index", y) if y >= 2000 else 1.0
        stded = (
            pl.when(status == "single").then(_standard_deduction(tables["single"], agi, index))
            .when(status == "head_of_household").then(_standard_deduction(tables["head_of_household"], agi, index))
            .when(is_joint).then(_standard_deduction(tables["married_joint"], agi, index))
            .otherwise(_standard_deduction(tables["married_separate"], agi, index))
        )
    d = p["dependent_standard_deduction"]
    stded = pl.when(dependent_filer).then(
        pl.min_horizontal(stded, pl.max_horizontal(pl.lit(float(d["floor"])), pl.col("earned_income") + d["earned_addition"]))
    ).otherwise(stded)
    if y <= 1985:
        tables = resolve_year(p["low_income_deduction"], y)

        def low(key: str) -> pl.Expr:
            return _low_income_deduction(tables[key], agi, depx, depadd)

        lowinc = (
            pl.when(single_like & (aged == 0)).then(low("single_0"))
            .when(single_like).then(low("single_1"))
            .when(aged == 0).then(low("married_0"))
            .when(aged == 1).then(low("married_1"))
            .otherwise(low("married_2"))
        )
        stded = pl.max_horizontal(stded, lowinc)

    # --- Itemized deductions (a credit from 1986) ---
    contr = pl.col("charity_cash")
    # TAXSIM keeps mortgage interest as a single-precision whole number.
    inrst = pl.col("mortgage").cast(pl.Int64).cast(pl.Float32).cast(pl.Float64)
    if y <= 1985:
        # The federal gross itemized total (`comnew(30)`); from 1979 less
        # state income and property taxes.
        gross, _, _ = pre1987_federal_itemizing(y)
        xitded = gross
        if y >= 1979:
            xitded = gross - pl.col("state_sales_or_income_tax_ded") - pl.col("proptax")
    else:
        xitded = contr + inrst
    xitded = xitded.clip(0, None)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    df, (stded, xitded) = checkpoint(df, wi_stded=stded, wi_xitded=xitded)

    if y <= 1985:
        taxinc = (agi - pl.max_horizontal(xitded, stded)).clip(0, None)
    else:
        taxinc = (agi - stded).clip(0, None)
    exemp = pl.lit(0.0)
    if y >= 2000:
        exemp = pl.when(dependent_filer).then(0.0).otherwise(
            _p("exemption", y) * (depx + txp) + _p("exemption_aged", y) * aged
        )
    df, (taxinc,) = checkpoint(df, wi_taxinc=(taxinc - exemp).clip(0, None))

    # --- Tax ---
    schedules = resolve_year(p["brackets"], y)
    index = _p("bracket_index", y)
    if y <= 1985:
        table = _scaled(schedules["all"], index)
        # Joint returns: the lesser of income splitting and the spouses'
        # shares, whose lookup leaves the rate at the higher earner's share.
        yh = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + (taxinc - pl.col("pwages") - pl.col("swages")) / 2.0
        yw = taxinc - yh
        split = 2.0 * bracket_tax(taxinc / 2.0, table)
        spouses = bracket_tax(yh.clip(0, None), table) + bracket_tax(yw.clip(0, None), table)
        statax = pl.when(is_joint).then(pl.min_horizontal(split, spouses)).otherwise(bracket_tax(taxinc, table))
        rate = pl.when(is_joint).then(bracket_rate(yh.clip(0, None), table)).otherwise(bracket_rate(taxinc, table))
    else:
        single_index = _p("single_bracket_index", y) if 2014 <= y <= 2019 else index
        single = _scaled(schedules["single"], single_index)
        joint = _scaled(schedules["married_joint"], index)
        separate = schedules["married_separate"]
        statax = pl.when(single_like).then(bracket_tax(taxinc, single)).when(is_joint).then(bracket_tax(taxinc, joint))
        rate = pl.when(single_like).then(bracket_rate(taxinc, single)).when(is_joint).then(bracket_rate(taxinc, joint))
        if separate is None:
            # 1986 has no schedule for separate returns.
            statax = statax.otherwise(0.0)
            rate = rate.otherwise(0.0)
        else:
            separate = _scaled(separate, index)
            statax = statax.otherwise(bracket_tax(taxinc, separate))
            rate = rate.otherwise(bracket_rate(taxinc, separate))
    if y == 1979:
        r = p["tax_reduction_1979"]
        statax = statax - pl.min_horizontal(r["rate"] * statax, pl.lit(float(r["cap"])))
    if y == 1983:
        statax = statax * p["surtax_1983"]
    df, (statax,) = checkpoint(df, wi_tax_before_credits=statax)

    # --- Credits ---
    hy = pl.col("wi_hy")
    if y <= 1985:
        c = p["exemption_credit_1985"]
        exempc = c["taxpayer"] * txp + c["aged"] * aged + c["dependent"] * depx + pl.when(
            status == "head_of_household"
        ).then(float(c["head_of_household"])).otherwise(0.0)
    elif y <= 1996:
        c = p["exemption_credit_1996"]
        exempc = c["aged"] * aged + c["dependent"] * depx
    elif y <= 1999:
        c = p["exemption_credit_1999"]
        limit = by_filing_status(c["income_limit"])
        reduction = c["phaseout_rate"] * (hy - limit)
        per_return = pl.when(is_joint).then(c["aged"] * aged).otherwise(float(c["aged"]))
        aged_credit = (
            pl.when(hy <= limit).then(per_return)
            .when(hy <= limit + c["width"]).then(per_return - reduction)
            .otherwise(0.0)
        )
        exempc = c["dependent"] * depx + pl.when(aged > 0).then(aged_credit).otherwise(0.0)
    else:
        exempc = pl.lit(0.0)

    xcred = p["itemized_credit_rate"] * (xitded - stded).clip(0, None) if y >= 1986 else pl.lit(0.0)

    renter = (pl.col("proptax") < 1) & (pl.col("rentpaid") > 0)
    if y == 1978:
        c = p["school_property_credit_1978"]
        spcred = pl.when(pl.col("rentpaid") > 0).then(float(c["renter"])).otherwise(
            (c["rate"] * pl.col("proptax")).clip(c["minimum"], c["maximum"])
        )
    elif y >= 1979:
        cmax = _p("school_property_credit_max", y) / sep
        # The renter credit multiplies TAXSIM's renter indicator (1), not rent.
        spcred = pl.when(pl.col("rentpaid") > 0).then(
            pl.min_horizontal(
                _p("school_property_credit_renter_rate", y) * renter.cast(pl.Float64)
                / p["school_property_credit_renter_divisor"],
                cmax,
            )
        ).otherwise(
            pl.min_horizontal(
                _p("school_property_credit_owner_rate", y) * pl.col("proptax") / p["school_property_credit_owner_divisor"],
                cmax,
            )
        )
    else:
        spcred = pl.lit(0.0)

    chcr = p["child_care_credit_share_1984"] * pl.col("federal_chcr") if y in (1984, 1985) else pl.lit(0.0)

    wcred = pl.lit(0.0)
    if y >= 1998:
        c = p["working_families_credit"]
        limit = pl.when(is_joint).then(c["income_limit"]["married_joint"]).otherwise(c["income_limit"]["other"])
        intercept = pl.when(is_joint).then(c["intercept"]["married_joint"]).otherwise(c["intercept"]["other"])
        share = intercept - c["slope"] * agi
        wcred = (
            pl.when(agi <= c["full_income_per_taxpayer"] * txp).then(statax)
            .when(agi <= limit).then(share * (statax - exempc - xcred - spcred).clip(0, None))
            .otherwise(0.0)
        )
        wcred = pl.when(dependent_filer).then(0.0).otherwise(wcred)

    eitc = pl.col("pre1987_earncr") if y <= 1986 else pl.col("eitc")
    earncr = pl.lit(0.0)
    if y in (1984, 1985):
        earncr = p["eitc_share_1984"] * eitc
    elif y == 1994:
        c = p["eitc_1994"]
        earned = pl.col("earned_income")
        income = pl.max_horizontal(agi, earned)

        def own(n: int) -> pl.Expr:
            k = c[n]
            credit = pl.min_horizontal(k["rate"] * earned, pl.lit(float(k["maximum"]))).clip(0, None)
            credit = pl.when(income > c["phaseout_start"]).then(
                (credit - k["phaseout_rate"] * (income - c["phaseout_start"])).clip(0, None)
            ).otherwise(credit)
            return pl.when(is_sep | dependent_filer).then(0.0).otherwise(credit)

        earncr = pl.when(depx == 1).then(own(1)).when(depx == 2).then(own(2)).when(depx >= 3).then(own(3)).otherwise(0.0)
    elif y >= 1989:
        shares = p["eitc_share"]
        share = pl.lit(0.0)
        for n in (1, 2, 3):
            share = pl.when((depx == n) if n < 3 else (depx >= n)).then(_p_nested(shares[n], y)).otherwise(share)
        earncr = share * eitc

    credit = xcred + exempc + earncr + chcr + spcred + wcred
    if y <= 1988:
        statax = (statax - credit).clip(0, None)
    else:
        statax = (statax - exempc - xcred - spcred - wcred).clip(0, None)

    # --- Minimum tax ---
    amt = pl.lit(0.0)
    if 1981 <= y <= 1985:
        c = p["minimum_tax_1985"]
        xtra = (contr + inrst - c["agi_share"] * agi.clip(0, None)).clip(0, None)
        amt = pl.when(xitded > c["itemized_threshold"]).then(
            (pl.col("pre1987_capded") + xtra - c["exemption"]).clip(0, None) * c["rate"]
        ).otherwise(0.0)
    elif y == 1986:
        amt = (p["minimum_tax_1986_federal_share"] * pl.col("pre1987_almtax")).clip(0, None)
    elif 1987 <= y <= 2018:
        alminy = pl.col("amt_income") - ss_taxable
        alminy = alminy - pl.when(ui > 0).then(ui - ui_taxable).otherwise(0.0)
        gains = pl.when(stcg >= 0).then(ltcg.clip(0, None)).otherwise(fullcg.clip(0, None))
        alminy = alminy + _p("capital_gain_exclusion", y) * gains
        rate_out = p["amt_phaseout_rate"]
        exempt_single = (_p("amt_exemption_single", y) - rate_out * (alminy - _p("amt_phaseout_single", y)).clip(0, None)).clip(0, None)
        exempt_married = (
            _p("amt_exemption_married", y) - rate_out * (alminy - _p("amt_phaseout_married", y) / sep).clip(0, None)
        ).clip(0, None)
        alminy = alminy - pl.when(single_like).then(exempt_single).otherwise(exempt_married)
        amt = (p["amt_rate"] * alminy - statax).clip(0, None)
        statax = statax + amt
    df, (statax, amt) = checkpoint(df, wi_after_amt=statax, wi_amt=amt)

    # --- Married couple credit ---
    twocrd = pl.lit(0.0)
    if y >= 1986:
        c = resolve_year(p["married_couple_credit"], y)
        business = 0.5 * (pl.col("psemp") + pl.col("ssemp"))
        earh = (pl.col("pwages") + pl.col("pbusinc") + pl.col("pprofinc") + business).clip(0, None)
        earw = (pl.col("swages") + pl.col("sbusinc") + pl.col("sprofinc") + business).clip(0, None)
        twocrd = pl.when(is_joint).then(c["rate"] * pl.min_horizontal(pl.min_horizontal(earh, earw), pl.lit(float(c["cap"])))).otherwise(0.0)
        statax = (statax - twocrd).clip(0, None)
    # The minimum tax is added again where it exceeds the tax now.
    statax = statax + (amt - statax).clip(0, None)

    # --- Homestead credit (refundable) ---
    c = resolve_year(p["homestead_credit"], y)
    rentpaid = pl.col("rentpaid")
    hymod = (hy - c["dependent_deduction"] * depx).clip(0, None)
    income_test = hy if y <= 1988 else hymod
    # 1989-1990 test net income but reduce the credit on gross income.
    reduce_on = hymod if y >= 1991 else hy
    ptax = pl.min_horizontal(pl.col("proptax") + c["rent_share"] * rentpaid, pl.lit(float(c["tax_cap"])))
    tablea = pl.when(reduce_on > c["income_floor"]).then((reduce_on - c["income_floor"]) * c["income_rate"]).otherwise(0.0)
    tableb = (ptax - tablea).clip(0, None)
    pcred = (
        pl.when(tableb <= 0).then(0.0)
        .when(tableb <= c["small_limit"]).then(c["small_rate"] * tableb)
        .otherwise(c["rate"] * tableb)
    )
    pcred = pl.when(income_test <= c["income_limit"]).then(pcred).otherwise(0.0)
    statax = statax - pcred
    if y >= 1989:
        statax = statax - earncr

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        property_credit=pcred,
        child_care_credit=chcr,
        eic=earncr,
        credits=credit + twocrd + pcred,
        rate=rate,
    )


def _p_nested(values: dict, year: int) -> float:
    return float(resolve_year(values, year))
