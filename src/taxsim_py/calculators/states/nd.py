"""North Dakota individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    checkpoint,
    dividend_exclusion_addback,
    pre1987_federal_itemizing,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

ND_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nd" / "income_tax.yaml")


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def _schedule_2009(uppers: list[float], y: int) -> list[list[float]]:
    rates = resolve_year(ND_PARAMS["rates"], y)
    starts = [0.0, *[float(u) for u in uppers]]
    return [[start, float(rate)] for start, rate in zip(starts, rates)]


def _single_schedule(y: int) -> list[list[float]]:
    """Single filers' schedule for 2001 on (the marriage credit reuses it)."""
    p = YearParams(ND_PARAMS, y)
    if y <= 2008:
        return _scaled(ND_PARAMS["brackets_2001"]["single"], p.num("bracket_inflation_2001"))
    return _schedule_2009(ND_PARAMS["bracket_uppers"][y]["single"], y)


def _federal_tax_deduction(y: int) -> pl.Expr:
    """Federal income tax deducted through 2000 (`fded`)."""
    if y <= 1986:
        pretax = pl.col("pre1987_pretax")
        credit = pl.col("credit")
        earncr = pl.col("pre1987_earncr")
        almtax = pl.col("pre1987_almtax")
    else:
        # `pretax = max(regtax, almtax, tax + credit + eitc + chcr1)` with
        # `tax = taxaft - earncr - chcr1` and `eitc = min(earncr, taxaft)`.
        regtax = pl.col("regular_tax")
        almtax = pl.col("amt")
        credit = pl.col("nonrefundable_credits")  # `comnew(58)`
        earncr = pl.col("eitc")
        before = regtax + almtax if y >= 2000 else regtax
        # Before 1998 the reported credit total does not reduce federal tax.
        taxaft = (before - (credit if y >= 1998 else 0.0)).clip(0, None)
        # `regtax` in the maximum is the schedule tax (from 1991 ordinary
        # rates on all income, `comnew(28)`).
        pretax = pl.max_horizontal(pl.col("schedule_tax"), almtax, taxaft - earncr + pl.min_horizontal(earncr, taxaft) + credit)
    return (pretax - credit - earncr + almtax).clip(0, None)


def compute_nd_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate North Dakota income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(ND_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = deflate_for_extrapolation(df, flate)

    is_single = files_single()
    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    fed_agi = pl.col("agi")
    addex = pl.when(is_joint | is_hoh).then(p["additional_exemption"]).otherwise(0.0)
    fded = _federal_tax_deduction(y) if y <= 2000 else pl.lit(0.0)
    agi = pl.lit(0.0)
    exemp = pl.lit(0.0)
    stded = pl.lit(0.0)
    xitded = pl.lit(0.0)

    if y <= 1986:
        # --- AGI ---
        agi = fed_agi
        if y <= 1980:
            capded = (pl.col("stcg") + pl.col("ltcg") - pl.col("pre1987_capgn")).clip(0, None)
            agi = agi + capded * p["capital_gains_deduction_addback_share"]
        elif y <= 1982:
            agi = agi - pl.min_horizontal(pl.col("intrec"), p.num("interest_exclusion_per_taxpayer") * txp)
            if y == 1982:
                agi = agi + dividend_exclusion_addback(y)
        else:
            if y == 1985:
                agi = agi - pl.col("taxable_social_security")
            agi = agi - pl.min_horizontal(pl.col("intrec"), p.num("interest_exclusion_per_taxpayer") * txp)
        df, (agi,) = checkpoint(df, nd_agi=agi)

        # --- Federal itemized deduction and zero bracket ---
        gross, fed_itemizes, zbr = pre1987_federal_itemizing(y)
        exemps = federal_exemption_count(y)
        xitded = pl.when(fed_itemizes).then(gross).otherwise(0.0)
        if y <= 1980:
            limits = p["standard_deduction_1977_1980"]
            rate = p["standard_deduction_rate_1977_1980"]
            single_std = (rate * agi).clip(limits["single"][0], limits["single"][1])
            married_std = (rate * agi).clip(limits["married"][0] / sep, limits["married"][1] / sep)
            stded = pl.when(is_single | is_hoh).then(single_std).otherwise(married_std)
        else:
            stded = zbr
        deduc = pl.when(fed_itemizes).then(xitded).otherwise(pl.max_horizontal(stded, xitded)) + fded
        exemp = exemps * p.num("exemption_amount") + addex
        df, (taxinc,) = checkpoint(df, nd_taxinc=(agi - deduc - exemp).clip(0, None))
        if y == 1977:
            brackets = p["brackets_1977"]
        elif y <= 1982:
            brackets = p["brackets_1978"]
        else:
            brackets = p["brackets_1983"]
        statax = bracket_tax(taxinc, brackets)
        rate = bracket_rate(taxinc, brackets)
    elif y <= 2000:
        # State income tax deducted federally is added back for every return.
        taxinc = (pl.col("taxable_income") + pl.col("state_sales_or_income_tax_ded") - fded - addex).clip(0, None)
        df, (taxinc,) = checkpoint(df, nd_taxinc=taxinc)
        statax = bracket_tax(taxinc, p["brackets_1987"])
        rate = bracket_rate(taxinc, p["brackets_1987"])
    else:
        gshort = pl.col("stcg")
        glong = pl.col("ltcg")
        fullcg = gshort + glong
        cgltg = (
            pl.when((fullcg > 0) & (glong > 0) & (gshort >= 0)).then(glong)
            .when((fullcg > 0) & (glong > 0) & (gshort < 0)).then(fullcg)
            .otherwise(0.0)
        )
        cgexc = p.num("capital_gain_exclusion") * cgltg.clip(0, None)
        qdiv = p.num("qualified_dividend_exclusion") * (pl.col("dividends") + dividend_adjustment)
        taxinc = (pl.col("taxable_income") - cgexc - qdiv).clip(0, None)
        # Taxable Social Security is subtracted (2019-2020 only up to an AGI limit).
        if y >= 2019:
            ss_taxable = pl.col("taxable_social_security")
            if y <= 2020:
                ss_taxable = pl.when(fed_agi <= p.num("social_security_subtraction_agi_limit_per_taxpayer") * txp).then(
                    ss_taxable
                ).otherwise(0.0)
            taxinc = (taxinc - ss_taxable).clip(0, None)
        df, (taxinc,) = checkpoint(df, nd_taxinc=taxinc)
        if y <= 2008:
            factor = p.num("bracket_inflation_2001")
            tables = p["brackets_2001"]
            married = _scaled(tables["married"], factor)
            statax = (
                pl.when(is_single).then(bracket_tax(taxinc, _scaled(tables["single"], factor)))
                .when(is_hoh).then(bracket_tax(taxinc, _scaled(tables["head_of_household"], factor)))
                .when(is_sep).then(bracket_tax(taxinc * 2.0, married) / 2.0)
                .otherwise(bracket_tax(taxinc, married))
            )
            rate = (
                pl.when(is_single).then(bracket_rate(taxinc, _scaled(tables["single"], factor)))
                .when(is_hoh).then(bracket_rate(taxinc, _scaled(tables["head_of_household"], factor)))
                .when(is_sep).then(bracket_rate(taxinc * 2.0, married))
                .otherwise(bracket_rate(taxinc, married))
            )
        else:
            uppers = p["bracket_uppers"][y]
            married = bracket_tax(taxinc * sep, _schedule_2009(uppers["married"], y)) / sep
            statax = (
                pl.when(is_single).then(bracket_tax(taxinc, _schedule_2009(uppers["single"], y)))
                .when(is_hoh).then(bracket_tax(taxinc, _schedule_2009(uppers["head_of_household"], y)))
                .otherwise(married)
            )
            rate = (
                pl.when(is_single).then(bracket_rate(taxinc, _schedule_2009(uppers["single"], y)))
                .when(is_hoh).then(bracket_rate(taxinc, _schedule_2009(uppers["head_of_household"], y)))
                .otherwise(bracket_rate(taxinc * sep, _schedule_2009(uppers["married"], y)))
            )
    df, (statax,) = checkpoint(df, nd_tax_before_credits=statax)

    # --- Contribution credit (through 1999) ---
    contcr = pl.lit(0.0)
    if y <= 1999:
        contcr = pl.min_horizontal(
            p["contribution_credit_rate"] * pl.col("charity_cash"), p["contribution_credit_tax_share"] * statax
        ).clip(None, p.num("contribution_credit_cap"))
        statax = (statax - contcr).clip(0, None)

    # --- Short form: a share of federal tax before credits (1981-2000) ---
    energy_credit = pl.lit(0.0)
    if 1981 <= y <= 2000:
        fedtax = pl.col("pre1987_taxbc") if y <= 1986 else pl.col("regular_tax")
        short_tax = p.num("short_form_rate") * fedtax
        short_selected = short_tax < statax
        rate = pl.when(short_selected).then(
            p.num("short_form_rate") * pl.col("federal_source_rate") / 100.0
        ).otherwise(rate)
        statax = pl.min_horizontal(statax, short_tax)
        if y <= 1982:
            energy_credit = statax.clip(0, p["energy_credit_1981_1982"])
            statax = (statax - energy_credit).clip(0, None)
    df, (statax,) = checkpoint(df, nd_statax=statax)

    # --- Marriage credit (2007 on, joint returns) ---
    crdmar = pl.lit(0.0)
    if y >= 2007:
        pw = pl.col("pwages").clip(0, None)
        sw = pl.col("swages").clip(0, None)
        lower = pl.min_horizontal(pw, sw)
        single = _single_schedule(y)
        taxin3 = (lower - p.num("marriage_credit_deduction")).clip(0, None)
        taxin4 = (taxinc - taxin3).clip(0, None)
        crdmar = (statax - bracket_tax(taxin3, single) - bracket_tax(taxin4, single)).clip(0, p.num("marriage_credit_max"))
        eligible = (
            is_joint & (taxinc > p.num("marriage_credit_taxable_income_min"))
            & (lower > p.num("marriage_credit_earnings_min"))
        )
        crdmar = pl.when(eligible).then(crdmar).otherwise(0.0)
        statax = (statax - crdmar).clip(0, None)

    # --- Tax relief credit 2021 ---
    relief = pl.lit(0.0)
    if y == 2021:
        relief = p.num("relief_credit") * txp
        statax = (statax - relief).clip(0, None)

    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        credits=contcr + energy_credit + crdmar + relief,
        rate=rate,
    )
