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


def _tables(y: int, taxsim_schedule: bool):
    params = YearParams(NJ_PARAMS, y)
    if taxsim_schedule and params.in_effect("taxsim_brackets"):
        return params.value("taxsim_brackets")
    return params.value("brackets")


def _schedule_tax(income: pl.Expr, uses_married: pl.Expr, y: int, taxsim_schedule: bool = False) -> pl.Expr:
    tables = _tables(y, taxsim_schedule)
    if isinstance(tables, list):
        return bracket_tax(income, tables)
    return (
        pl.when(uses_married)
        .then(bracket_tax(income, tables["married"]))
        .otherwise(bracket_tax(income, tables["single"]))
    )


def _schedule_rate(income: pl.Expr, uses_married: pl.Expr, y: int, taxsim_schedule: bool = False) -> pl.Expr:
    tables = _tables(y, taxsim_schedule)
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
        nj_household_income_undeflated=household_income(behavior),
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
    taxsim_schedule = not behavior.tax_new_jersey_top_rate_after_1982_per_statute
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
    if not behavior.exclude_nonproperty_income_from_new_jersey_income:
        agi = agi + pl.col("nonprop")
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
    if not behavior.exclude_new_jersey_pensions_per_statute:
        # TAXSIM excludes the share of pensions its income table allows for anyone 62 or older.
        single_rows = [[100000.0, 1.0], [125000.0, 0.375], [150000.0, 0.1875], [1.0e20, 0.0]]
        married_rows = [[100000.0, 1.0], [125000.0, 0.5], [150000.0, 0.25], [1.0e20, 0.0]]
        frac = pl.when(txp >= 2).then(interpolate_table(agi, married_rows)).otherwise(interpolate_table(agi, single_rows))
        oldest = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        fed_less_ss = pl.col("agi") - pl.col("taxable_social_security")
        retmax = pl.when(txp >= 2).then(100_000.0).otherwise(75_000.0)
        if y >= 2021:
            penex = pl.when(agi < 150_000).then(frac * pensions).otherwise(0.0)
            other_cap = frac * agi - frac * pensions
            earned = pl.col("pwages") + pl.col("psemp").clip(0, None) + pl.col("pbusinc") + pl.col("pprofinc")
            retoth = pl.when((other_cap > 0) & (earned <= 3000)).then(other_cap).otherwise(0.0)
        else:
            penex = pl.min_horizontal(pl.when(frac < 1).then(frac * pensions).otherwise(retmax), pensions)
            othmax = pl.when(frac < 1).then(frac * fed_less_ss).otherwise(retmax)
            unused = (othmax - penex).clip(0, None)
            earned = (
                pl.col("pwages") + pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc")
            )
            retoth = pl.when(earned <= 3000).then(pl.min_horizontal(unused, agi.clip(0, None))).otherwise(0.0)
        eligible_age = oldest >= 62
        agi = agi - pl.when(eligible_age).then(penex + retoth).otherwise(0.0)
    if behavior.exclude_new_jersey_pensions_per_statute:
        excluded = agi - pensions.clip(0, deduct)
        unused = (deduct - pensions).clip(0, None)
        earnings = wages + pl.col("psemp") + pl.col("ssemp")
        excluded = pl.when(earnings <= float(NJ_PARAMS["other_retirement_exclusion_earnings_limit"])).then(
            excluded - pl.min_horizontal(unused, excluded.clip(0, None))
        ).otherwise(excluded)
        agi = pl.when(aged > 0).then(excluded).otherwise(agi)
    # Gross income counts the full net gain, never a net loss; TAXSIM swaps
    # the federal gain for the raw one, so a loss beyond $3,000 lowers income.
    if y < 1987 and not behavior.tax_full_capital_gains_in_new_jersey_before_1987:
        # TAXSIM counts the federal gain after the 60% exclusion.
        nj_agi = agi
    elif behavior.disallow_new_jersey_capital_losses:
        nj_agi = agi - capgn.clip(0, None) + fullcg.clip(0, None)
    else:
        nj_agi = agi - capgn + fullcg
    df, (agi, capgn) = checkpoint(df, nj_agi=nj_agi, nj_capgn=capgn)

    if not behavior.compute_new_jersey_tax_per_statute:
        return _taxsim_tax(df, y, flate, p, behavior, agi, nj_agi_expr=None)

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
    statax = _schedule_tax(taxinc, uses_married, y, taxsim_schedule)
    rate = pl.when(no_tax).then(0.0).otherwise(_schedule_rate(taxinc, uses_married, y, taxsim_schedule))
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
        if not behavior.pay_new_jersey_renter_credit_per_statute:
            # TAXSIM pays $65 to homeowners and $35 to everyone else.
            pcred = pl.when(proptax > 0).then(65.0).otherwise(35.0)
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
        statpr = _schedule_tax(income_pr, uses_married, y, taxsim_schedule)
        rate_pr = _schedule_rate(income_pr, uses_married, y, taxsim_schedule)
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


def _table(p: YearParams, name: str):
    return p.value(name)


def _taxsim_tax(df: pl.DataFrame, y: int, flate: float, p: YearParams, behavior: BehaviorProfile, agi: pl.Expr, nj_agi_expr=None) -> pl.DataFrame:
    """The 2026 TAXSIM build's New Jersey tax (`njtax77` through 2020, `njtax21` from 2021)."""
    taxsim_schedule = True
    is_joint = files_joint()
    is_hoh = files_head_of_household()
    is_sep = files_separate()
    uses_married = is_joint | is_hoh
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    proptax = pl.col("proptax")
    rentpaid = pl.col("rentpaid")
    married_units = pl.when(uses_married).then(2.0).otherwise(1.0)

    exemp = (txp + aged) * p.num("exemption_per_taxpayer") + depx * p.num("exemption_per_dependent")
    taxic = (agi - exemp).clip(0, None)

    # No-tax status.
    if y >= 2021:
        notax = agi <= 10_000.0 * married_units / sep
    elif y >= 1993:
        base = float(resolve_year(NJ_PARAMS["taxsim_no_tax_threshold"], min(y, 2001)))
        if y == 2000:
            base = base * married_units * 0.75
        elif y >= 2001:
            base = base * married_units
        notax = agi <= base / sep
    else:
        notax = pl.lit(False)

    table_tax = _schedule_tax(taxic, uses_married, y, taxsim_schedule)
    rate = _schedule_rate(taxic, uses_married, y, taxsim_schedule)
    statax = pl.when(notax).then(0.0).otherwise(table_tax)
    taxic = pl.when(notax).then(0.0).otherwise(taxic)
    taxbc = statax
    taxinc = taxic
    # From 2021 TAXSIM remembers the property tax input on its standard-deduction pass.
    ptax = (pl.col("original_proptax") if y >= 2021 else proptax) + 0.18 * rentpaid

    # Property tax relief credit.
    pcred = pl.lit(0.0)
    aged_any = aged > 0
    if y <= 1984:
        pcred = pl.when(proptax > 0).then(65.0).otherwise(35.0)
    elif y <= 1989:
        owner_floor = interpolate_table(taxic, p["taxsim_property_floor_owner"]) / sep
        renter_floor = interpolate_table(taxic, p["taxsim_property_floor_renter"]) / sep
        deduction = pl.when(proptax > 0).then(pl.max_horizontal(owner_floor, proptax)).otherwise(
            pl.max_horizontal(renter_floor, 0.18 * rentpaid)
        )
        pcred = pl.when(agi > 3000.0 / sep).then(0.02 * (deduction - taxic).clip(0, None)).otherwise(0.0)
    elif y <= 2008:
        eligible = pl.lit(y <= 2003) | (pl.lit(y >= 2004) & (rentpaid > 0))
        xcess = (ptax - 0.05 * agi).clip(0, None)
        top = float(resolve_year(NJ_PARAMS["taxsim_homestead_credit_top_tier"], y)) if y >= 1998 else 0.0
        later = y >= 2001
        owner_cap = 775.0 if later else 500.0
        renter_low = 100.0 if later else 65.0
        owner_aged = (
            pl.when(agi <= 35000).then(xcess.clip(150, owner_cap) / sep)
            .when(agi <= 70000).then(pl.when(married_units == 2).then(xcess.clip(150, owner_cap) / sep).otherwise(150.0 / sep))
            .when(agi <= 100000).then(100.0 / sep)
            .otherwise(0.0)
        )
        renter_aged = (
            pl.when(agi <= 35000).then(xcess.clip(renter_low, owner_cap) / sep)
            .when(agi <= 70000).then(
                pl.when(married_units == 2).then(
                    (xcess.clip(65, 500) / sep) if not later else pl.lit(100.0) / sep
                ).otherwise((65.0 if not later else 100.0) / sep)
            )
            .when(agi <= 100000).then(top / sep)
            .otherwise(0.0)
        )
        young_owner = pl.when((proptax > 0) & (agi < 40000)).then(90.0 / sep).otherwise(0.0)
        if y <= 1998:
            renter_young = pl.when(agi < 40000).then(30.0 / sep).otherwise(0.0)
        elif y == 1999:
            renter_young = pl.when(agi < 100000).then(40.0 / sep).otherwise(0.0)
        elif y == 2000:
            renter_young = pl.when(agi < 100000).then(60.0 / sep).otherwise(0.0)
        elif y <= 2003:
            renter_young = pl.when(agi < 100000).then(100.0 / sep).otherwise(0.0)
        else:
            renter_young = pl.lit(150.0) / sep
        young = pl.when(rentpaid > 0).then(renter_young).otherwise(young_owner)
        aged_credit = pl.when(proptax > 0).then(owner_aged).when(rentpaid > 0).then(renter_aged).otherwise(0.0)
        pcred = pl.when(eligible).then(pl.when(aged_any).then(aged_credit).otherwise(young)).otherwise(0.0)
    if y >= 2021:
        pcred = pl.when(notax).then(0.0).otherwise(
            pl.when((proptax > 0) & (agi < 40000)).then(90.0 / sep).when(rentpaid > 0).then(150.0 / sep).otherwise(0.0)
        )

    # Property tax deduction or credit.
    if y >= 1996:
        cap = 15000.0 if y >= 2021 else p.num("property_tax_deduction_cap")
        share = 1.0 if y >= 2021 else p.num("property_tax_deduction_share")
        minimum = 50.0 if y >= 2021 else p.num("property_tax_credit")
        pded = pl.min_horizontal(cap / sep, share * ptax)
        taxid = (taxic - pded).clip(0, None)
        # The deduction path is taxed on the 1991 schedule through 2003.
        taxd = _schedule_tax(taxid, uses_married, 1991 if y <= 2003 else y, taxsim_schedule and y > 2003)
        applies = ptax > (0.0 if y >= 2021 else 2.0)
        take = (statax - taxd) >= minimum / sep
        new_taxinc = pl.when(take).then(taxid).otherwise(taxic)
        new_taxbc = pl.when(take).then(taxd).otherwise(statax)
        new_pcred = pl.when(take).then(0.0).otherwise(pcred if y >= 2021 else minimum / sep)
        taxinc = pl.when(applies).then(new_taxinc).otherwise(taxinc)
        taxbc = pl.when(applies).then(new_taxbc).otherwise(taxbc)
        pcred = pl.when(applies).then(new_pcred).otherwise(pcred)
        if y >= 2021:
            taxbc = pl.when(applies & notax).then(0.0).otherwise(taxbc)
            pcred = pl.when(applies & notax & ~aged_any).then(0.0).otherwise(pcred)
        rate = pl.when(applies).then(_schedule_rate(taxid, uses_married, 1991 if y <= 2003 else y, taxsim_schedule and y > 2003)).otherwise(rate)
    taxinc_for_credits = taxinc

    # Child care and child tax credits.
    chcr_nonref = pl.lit(0.0)
    chcr_ref = pl.lit(0.0)
    if 2018 <= y <= 2020:
        chcr_nonref = pl.col("federal_chcr") * interpolate_table(taxinc_for_credits, p["taxsim_child_care_nonrefundable"])
    elif y >= 2021:
        chcr_ref = pl.col("federal_chcr") * interpolate_table(taxinc_for_credits - 0.001, p["taxsim_child_care_refundable"])
    ctc = pl.lit(0.0)
    if y >= 2022:
        ctc = pl.col("dep6") * interpolate_table(taxinc_for_credits, p.value("taxsim_child_tax_credit"))

    # Earned income credit.
    earncr = pl.lit(0.0)
    rate_eitc = p.num("eitc_match_rate")
    eitc = pl.col("eitc")
    before_test = pl.col("eitc_before_filer_test")
    if y >= 2000:
        if y <= 2006:
            earncr = pl.when((agi <= 20000) & (depx > 0)).then(rate_eitc * eitc).otherwise(0.0)
        else:
            earncr = rate_eitc * (before_test if y >= 2024 else eitc)
            low, high = (19, 115) if y == 2021 else (25, 64)
            page, sage = pl.col("page"), pl.col("sage")
            in_range = ((page >= low) & (page <= high)) | ((sage >= low) & (sage <= high)) | ((page + sage) == 0)
            minimum = float(resolve_year(NJ_PARAMS["taxsim_eitc_minimum"], y))
            primary_earn = pl.col("pwages") if y < 2021 else pl.col("pwages") + pl.col("psemp").clip(0, None)
            spouse_earn = pl.col("swages") if y < 2021 else pl.col("swages") + pl.col("ssemp").clip(0, None)
            qualifies = ((primary_earn > 0) & (page >= 18)) | ((spouse_earn > 0) & (sage >= 18))
            flat = pl.when(qualifies & ~is_dependent_filer() & ~is_hoh).then(minimum).otherwise(0.0)
            applies_min = (depx == 0) & ~in_range & (before_test > 0)
            earncr = pl.when(applies_min).then(pl.max_horizontal(flat, earncr)).otherwise(earncr)

    refundable_nonref = chcr_nonref + pcred
    refundable = earncr + chcr_ref + ctc
    if y <= 1984:
        credit = pl.min_horizontal(refundable_nonref, taxbc) + refundable
    else:
        credit = refundable_nonref + refundable
    if y >= 2021:
        statax_final = taxbc - (earncr + ctc + chcr_ref + pcred)
    else:
        statax_final = pl.when(notax).then(pl.min_horizontal(taxbc - refundable - pcred, pl.lit(0.0))).otherwise(taxbc - credit)
    reported_exemp = pl.when(notax).then(0.0).otherwise(exemp)
    return with_state_detail(
        df.with_columns(siitax=statax_final * flate),
        agi=agi,
        exemptions=reported_exemp,
        taxable_income=taxinc,
        property_credit=pcred,
        eic=earncr,
        credits=credit,
        rate=rate,
    )
