"""New Jersey gross income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, checkpoint, household_income, interpolate_table, with_state_detail, dividend_input_adjustment
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NJ_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nj" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def _schedule_tax(income: pl.Expr, uses_married: pl.Expr, y: int) -> pl.Expr:
    if y <= 1990:
        return bracket_tax(income, resolve_year(NJ_PARAMS["brackets_through_1990"], y))
    tables = YearParams(NJ_PARAMS["brackets"], y)
    return (
        pl.when(uses_married)
        .then(bracket_tax(income, tables.value("married")))
        .otherwise(bracket_tax(income, tables.value("single")))
    )


def _schedule_rate(income: pl.Expr, uses_married: pl.Expr, y: int) -> pl.Expr:
    if y <= 1990:
        return bracket_rate(income, resolve_year(NJ_PARAMS["brackets_through_1990"], y))
    tables = YearParams(NJ_PARAMS["brackets"], y)
    return (
        pl.when(uses_married)
        .then(bracket_rate(income, tables.value("married")))
        .otherwise(bracket_rate(income, tables.value("single")))
    )


def _homestead_rebate(
    agi: pl.Expr, ptax: pl.Expr, proptax: pl.Expr, rentpaid: pl.Expr, aged: pl.Expr, married: pl.Expr,
    sep: pl.Expr, y: int,
) -> pl.Expr:
    """Homestead property tax rebate (1990-2008), refundable."""
    p = YearParams(NJ_PARAMS, y)
    a = YearParams(NJ_PARAMS["homestead_rebate_aged"], y)
    low, middle, top = (float(v) for v in a["tier_limits"])
    reb = (ptax - float(a["income_share"]) * agi).clip(0, None)
    maximum = a.num("maximum")

    def between(minimum: float) -> pl.Expr:
        return pl.max_horizontal(pl.lit(minimum), pl.min_horizontal(pl.lit(maximum), reb))

    owner_min = a.num("owner_minimum")
    owner = (
        pl.when(agi <= low).then(between(owner_min))
        .when(agi <= middle).then(pl.when(married).then(between(owner_min)).otherwise(a.num("owner_middle_other")))
        .when(agi <= top).then(a.num("owner_top"))
        .otherwise(0.0)
    )
    renter_min = a.num("renter_minimum")
    renter_middle_married = float(a["renter_middle_flat"][2001]) if y >= 2001 else between(renter_min)
    renter = (
        pl.when(agi <= low).then(between(renter_min))
        .when(agi <= middle).then(pl.when(married).then(renter_middle_married).otherwise(a.num("renter_middle_other")))
        .when(agi <= top).then(a.num("renter_top"))
        .otherwise(0.0)
    )
    aged_rebate = pl.when(proptax > 0).then(owner).when(rentpaid > 0).then(renter).otherwise(0.0)

    under65 = pl.when((proptax > 0) & (agi < p.num("homestead_rebate_income_limit"))).then(
        p.num("homestead_rebate")
    ).otherwise(0.0)
    under65 = pl.when((rentpaid > 0) & (agi < p.num("homestead_rebate_renter_income_limit"))).then(
        p.num("homestead_rebate_renter")
    ).otherwise(under65)
    return pl.when(aged > 0).then(aged_rebate).otherwise(under65) / sep


def compute_nj_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate New Jersey gross income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(NJ_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = df.with_columns(
        nj_dividends=pl.col("dividends") + dividend_adjustment,
        # Household income (`hy`) is read before TAXSIM's projected-year deflation.
        nj_household_income_undeflated=household_income(),
        # Federal Schedule E income (`comnew(8)`): other property income, plus
        # S corporation income from 1987.
        nj_schede=pl.col("otherprop") + (pl.col("scorp") if year >= 1987 else 0.0),
    )
    df = deflate_for_extrapolation(df, flate, extra=("nj_dividends", "nj_schede"))

    is_single = files_single()
    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    uses_married = is_joint | is_hoh
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    proptax = pl.col("proptax")

    fullcg = pl.col("stcg") + pl.col("ltcg")
    if y >= 1987:
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        capgn = pl.max_horizontal(fullcg, -loss_limit / flate / sep)
    else:
        capgn = pl.col("pre1987_capgn")

    # --- Gross income ---
    wages = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    semp = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
    # Business income is not deflated in projected years.
    business = semp + pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc")
    agi = (
        wages + pl.col("nj_dividends") + pl.col("intrec") + business.clip(0, None) + capgn.clip(0, None)
        + pl.col("nj_schede").clip(0, None) + pl.col("pensions")
    )
    if y < 1992:
        agi = agi + pl.col("taxable_unemployment") + pl.col("taxable_social_security")
    # Pension exclusion for taxpayers 65 or older; unused exclusion covers
    # other income when earnings are small.
    deduct = by_filing_status(
        {status: resolve_year(amounts, y) for status, amounts in NJ_PARAMS["pension_exclusion"].items()}
    )
    if y >= 2005:
        deduct = pl.when(pl.col("nj_household_income_undeflated") <= p.num("pension_exclusion_household_income_limit")).then(deduct).otherwise(0.0)
    pensions = pl.col("pensions")
    excluded = agi - pensions.clip(0, deduct)
    unused = (deduct - pensions).clip(0, None)
    earnings = wages + pl.col("psemp") + pl.col("ssemp")
    excluded = pl.when(earnings <= float(NJ_PARAMS["other_retirement_exclusion_earnings_limit"])).then(
        excluded - pl.min_horizontal(unused, excluded.clip(0, None))
    ).otherwise(excluded)
    agi = pl.when(aged > 0).then(excluded).otherwise(agi)
    df, (agi, capgn) = checkpoint(df, nj_agi=agi - capgn + fullcg, nj_capgn=capgn)

    # --- No-tax status ---
    if y <= 1999:
        threshold = p.num("no_tax_threshold") / sep
        no_tax = agi < threshold if 1994 <= y <= 1996 else agi <= threshold
    elif y == 2000:
        limits = NJ_PARAMS["no_tax_threshold_2000"]
        no_tax = pl.when(is_single).then(agi <= limits["single"]).otherwise(agi <= limits["other"] / sep)
    else:
        nts = pl.when(is_hoh).then(float(NJ_PARAMS["head_of_household_no_tax_taxpayers"])).otherwise(txp)
        no_tax = agi <= p.num("no_tax_threshold_per_taxpayer") * nts

    # --- Taxable income and tax ---
    exemp = (
        (txp + aged) * p.num("exemption_per_taxpayer") + depx * p.num("exemption_per_dependent")
    )
    df, (taxinc,) = checkpoint(df, nj_taxinc=(agi - exemp).clip(0, None))
    rentpaid = pl.col("rentpaid")
    rescr = pl.lit(0.0)
    if 1985 <= y <= 1989:
        floor = interpolate_table(taxinc, NJ_PARAMS["property_tax_floor_1985_1989"]) / sep
        pded = pl.when(proptax > 0).then(pl.max_horizontal(floor, proptax)).otherwise(0.0)
        # Renters without property tax deduct part of their rent.
        rded = pl.when((proptax < 1) & (rentpaid > 0)).then(
            pl.max_horizontal(
                float(NJ_PARAMS["rent_deduction_floor_share_1985_1989"]) * floor,
                float(NJ_PARAMS["rent_deduction_share_1985_1989"]) * rentpaid,
            )
        ).otherwise(0.0)
        applies = agi > p.num("no_tax_threshold") / sep
        rescr = pl.when(applies).then(
            NJ_PARAMS["property_tax_excess_credit_rate"] * (pded + rded - taxinc).clip(0, None)
        ).otherwise(0.0)
        taxinc = pl.when(applies).then((taxinc - pded - rded).clip(0, None)).otherwise(taxinc)
    statax = _schedule_tax(taxinc, uses_married, y)
    rate = pl.when(no_tax).then(0.0).otherwise(_schedule_rate(taxinc, uses_married, y))
    reported_exemp = pl.when(no_tax).then(0.0).otherwise(exemp)
    df, (statax, taxinc) = checkpoint(
        df,
        nj_tax=pl.when(no_tax).then(0.0).otherwise(statax),
        nj_taxinc_final=pl.when(no_tax).then(0.0).otherwise(taxinc),
    )

    # --- Property tax credits and rebates ---
    # Property tax counts rent (`comnew(68) > 0`: any federal exemption).
    ptax = proptax.clip(0, None) + pl.when((rentpaid > 0) & (federal_exemption_count(y) > 0)).then(
        float(NJ_PARAMS["property_tax_rent_share"]) * rentpaid
    ).otherwise(0.0)
    pcred = pl.lit(0.0)
    if y <= 1984:
        pcred = pl.when(rentpaid > 0).then(
            float(NJ_PARAMS["renter_credit_through_1984"])
            + pl.when(aged > 0).then(float(NJ_PARAMS["renter_credit_aged_addition_through_1984"])).otherwise(0.0)
        ).otherwise(0.0)
    elif y <= 1989:
        low = agi <= p.num("no_tax_threshold") / sep
        pcred = (
            pl.when(low & (proptax > 0)).then(float(NJ_PARAMS["low_income_homeowner_credit_1985_1989"]))
            .when(low & (rentpaid > 0)).then(float(NJ_PARAMS["low_income_renter_credit_1985_1989"]))
            .otherwise(0.0)
        )
    rebate = pl.lit(0.0)
    if 1990 <= y <= 2008:
        rebate = _homestead_rebate(agi, ptax, proptax, rentpaid, aged, uses_married, sep, y)
        if y >= 2004:
            rebate = pl.when(rentpaid > 0).then(rebate).otherwise(0.0)
    if y >= 1996:
        exemp_pr = exemp
        pded = pl.min_horizontal(
            p.num("property_tax_deduction_cap") / sep, p.num("property_tax_deduction_share") * ptax
        )
        df, (income_pr,) = checkpoint(df, nj_taxinc_property=(agi - exemp_pr - pded).clip(0, None))
        statpr = _schedule_tax(income_pr, uses_married, y)
        rate_pr = _schedule_rate(income_pr, uses_married, y)
        credit_amount = p.num("property_tax_credit") / sep
        eligible = ptax > 2
        take_deduction = eligible & (statax - statpr >= credit_amount)
        pcred = pl.when(eligible & ~take_deduction & (proptax + rentpaid > 0) & (taxinc > 0)).then(
            credit_amount
        ).otherwise(pcred)
        statax = pl.when(take_deduction).then(statpr).otherwise(statax)
        taxinc = pl.when(take_deduction).then(income_pr).otherwise(taxinc)
        rate = pl.when(eligible).then(rate_pr).otherwise(rate)
        reported_exemp = pl.when(eligible).then(exemp_pr).otherwise(reported_exemp)

    statax = (statax - rescr - pcred).clip(0, None) - rebate

    # --- Earned income credit ---
    earncr = p.num("eitc_match_rate") * pl.col("eitc")
    if 2000 <= y <= 2006:
        earncr = pl.when(
            (agi <= float(NJ_PARAMS["eitc_income_limit_2000_2006"])) & (depx > 0)
        ).then(earncr).otherwise(0.0)
    statax = statax - earncr

    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=reported_exemp,
        taxable_income=taxinc,
        property_credit=pcred,
        eic=earncr,
        credits=rescr + pcred + earncr + rebate,
        rate=rate,
    )
