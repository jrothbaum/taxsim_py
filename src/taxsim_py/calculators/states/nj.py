"""New Jersey gross income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import is_dependent_filer, aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, checkpoint, household_income, interpolate_table, with_state_detail, dividend_input_adjustment
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NJ_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nj" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def _schedule_tax(income: pl.Expr, uses_married: pl.Expr, y: int) -> pl.Expr:
    tables = YearParams(NJ_PARAMS, y).value("brackets")
    if isinstance(tables, list):
        return bracket_tax(income, tables)
    return (
        pl.when(uses_married)
        .then(bracket_tax(income, tables["married"]))
        .otherwise(bracket_tax(income, tables["single"]))
    )


def _schedule_rate(income: pl.Expr, uses_married: pl.Expr, y: int) -> pl.Expr:
    tables = YearParams(NJ_PARAMS, y).value("brackets")
    if isinstance(tables, list):
        return bracket_rate(income, tables)
    return (
        pl.when(uses_married)
        .then(bracket_rate(income, tables["married"]))
        .otherwise(bracket_rate(income, tables["single"]))
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
    renter_middle_married = a.num("renter_middle_flat") if y >= 2001 else between(renter_min)
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


def compute_nj_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate New Jersey gross income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, NJ_PARAMS)
    y = effective_year
    p = YearParams(NJ_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = df.with_columns(
        nj_dividends=pl.col("dividends") + dividend_adjustment,
        # Household income is read before projected-year deflation.
        nj_household_income_undeflated=household_income(),
        # Federal Schedule E income: other property income, plus S
        # corporation income from 1987.
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
    if p.in_effect("pension_exclusion_phaseout"):
        household = pl.col("nj_household_income_undeflated")
        def phased(rows):
            share = pl.lit(0.0)
            for limit, fraction in reversed(rows):
                share = pl.when(household <= float(limit)).then(float(fraction)).otherwise(share)
            return share
        share = pl.lit(0.0)
        for status, rows in p.value("pension_exclusion_phaseout").items():
            share = pl.when(pl.col("filing_status") == status).then(phased(rows)).otherwise(share)
        deduct = deduct * share
    elif p.in_effect("pension_exclusion_household_income_limit"):
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
    limits = p.value("no_tax_threshold")
    if not isinstance(limits, dict):
        threshold = float(limits) / sep
        no_tax = agi < threshold if 1994 <= y <= 1996 else agi <= threshold
    elif "single" in limits:
        no_tax = pl.when(is_single).then(agi <= limits["single"]).otherwise(agi <= limits["other"] / sep)
    else:
        nts = pl.when(is_hoh).then(float(NJ_PARAMS["head_of_household_no_tax_taxpayers"])).otherwise(txp)
        no_tax = agi <= float(limits["per_taxpayer"]) * nts

    # --- Taxable income and tax ---
    exemp = (
        (txp + aged) * p.num("exemption_per_taxpayer") + depx * p.num("exemption_per_dependent")
    )
    df, (taxinc,) = checkpoint(df, nj_taxinc=(agi - exemp).clip(0, None))
    rentpaid = pl.col("rentpaid")
    rescr = pl.lit(0.0)
    if p.in_effect("property_tax_floor"):
        floor = interpolate_table(taxinc, p.value("property_tax_floor")) / sep
        rd = p.value("rent_deduction")
        pded = pl.when(proptax > 0).then(pl.max_horizontal(floor, proptax)).otherwise(0.0)
        # Renters without property tax deduct part of their rent.
        rded = pl.when((proptax < 1) & (rentpaid > 0)).then(
            pl.max_horizontal(
                float(rd["floor_share"]) * floor,
                float(rd["share"]) * rentpaid,
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
    # Property tax counts rent (with any federal exemption).
    ptax = proptax.clip(0, None) + pl.when((rentpaid > 0) & (federal_exemption_count(y) > 0)).then(
        float(NJ_PARAMS["property_tax_rent_share"]) * rentpaid
    ).otherwise(0.0)
    pcred = pl.lit(0.0)
    if p.in_effect("renter_credit"):
        rc = p.value("renter_credit")
        pcred = pl.when(rentpaid > 0).then(
            float(rc["amount"])
            + pl.when(aged > 0).then(float(rc["aged_addition"])).otherwise(0.0)
        ).otherwise(0.0)
    elif p.in_effect("low_income_property_credit"):
        lc = p.value("low_income_property_credit")
        low = agi <= p.num("no_tax_threshold") / sep
        pcred = (
            pl.when(low & (proptax > 0)).then(float(lc["homeowner"]))
            .when(low & (rentpaid > 0)).then(float(lc["renter"]))
            .otherwise(0.0)
        )
    rebate = pl.lit(0.0)
    if p.in_effect("homestead_rebate"):
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
    if behavior.apply_state_childless_eitc_minimum_age and y >= 2020:
        # A childless filer who meets every federal requirement except the
        # age test, and is old enough for New Jersey, gets the flat amount
        # printed on NJ-1040 line 58: the match rate times the federal
        # childless maximum, rounded to dollars ($224 for 2022).
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        childless = pl.col("num_children") == 0
        too_young = (older > 0) & (older < float(resolve_year(NJ_PARAMS["eitc_childless_minimum_age"], y)))
        too_old = pl.lit(bool(resolve_year(NJ_PARAMS["eitc_childless_maximum_age_applies"], y))) & (older >= 65)
        # Federal also bars childless returns on which everyone is 65 or older;
        # New Jersey removed that limit from 2021 and still bars dependents.
        meets_other_rules = pl.col("eitc_before_filer_test" if y >= 2021 else "eitc_before_age_test") > 0
        flat = (p.num("eitc_match_rate") * pl.col("max_credit")).round(0)
        earncr = (
            pl.when(childless & ~is_dependent_filer() & ~too_young & ~too_old & meets_other_rules & (pl.col("eitc") <= 0))
            .then(flat)
            .when(childless & (too_young | too_old))
            .then(0.0)
            .otherwise(earncr)
        )
    if p.in_effect("eitc_income_limit"):
        earncr = pl.when(
            (agi <= p.num("eitc_income_limit")) & (depx > 0)
        ).then(earncr).otherwise(0.0)
    statax = statax - earncr
    if p.in_effect("child_tax_credit"):
        # Refundable, per child under six, by NJ taxable income.
        credit_rows = p.value("child_tax_credit")
        statax = statax - bracket_rate(taxinc - 0.005, credit_rows) * pl.col("dep6")

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
