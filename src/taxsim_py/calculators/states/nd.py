"""North Dakota individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
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


def _single_schedule(y: int) -> list[list[float]]:
    """Single filers' schedule for 2001 on (the marriage credit reuses it)."""
    p = YearParams(ND_PARAMS, y)
    return _scaled(p.value("brackets")["single"], p.num("bracket_inflation"))


def _federal_tax_deduction(y: int) -> pl.Expr:
    """Federal income tax deducted through 2000 (`fded`)."""
    if y <= 1986:
        pretax = pl.col("pre1987_pretax")
        credit = pl.col("credit")
        earncr = pl.col("pre1987_earncr")
        almtax = pl.col("pre1987_almtax")
    else:
        # Federal tax before credits as TAXSIM reconstructs it.
        regtax = pl.col("regular_tax")
        almtax = pl.col("amt")
        credit = pl.col("nonrefundable_credits")
        earncr = pl.col("eitc")
        before = regtax + almtax if y >= 2000 else regtax
        # Before 1998 the reported credit total does not reduce federal tax.
        taxaft = (before - (credit if y >= 1998 else 0.0)).clip(0, None)
        # The schedule tax (from 1991 ordinary rates on all income).
        pretax = pl.max_horizontal(pl.col("schedule_tax"), almtax, taxaft - earncr + pl.min_horizontal(earncr, taxaft) + credit)
    return (pretax - credit - earncr + almtax).clip(0, None)


def compute_nd_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate North Dakota income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, ND_PARAMS)
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
    addex = pl.when(is_joint | is_hoh).then(p.num("additional_exemption")).otherwise(0.0) if p.in_effect("additional_exemption") else pl.lit(0.0)
    fded = _federal_tax_deduction(y) if y <= 2000 else pl.lit(0.0)
    agi = pl.lit(0.0)
    exemp = pl.lit(0.0)
    stded = pl.lit(0.0)
    xitded = pl.lit(0.0)

    if y <= 1986:
        # --- AGI ---
        agi = fed_agi
        if p.in_effect("capital_gains_deduction_addback_share"):
            capded = (pl.col("stcg") + pl.col("ltcg") - pl.col("pre1987_capgn")).clip(0, None)
            agi = agi + capded * p.num("capital_gains_deduction_addback_share")
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
        if p.in_effect("standard_deduction_percent"):
            limits = p.value("standard_deduction_percent")
            rate = limits["rate"]
            single_std = (rate * agi).clip(limits["single"][0], limits["single"][1])
            married_std = (rate * agi).clip(limits["married"][0] / sep, limits["married"][1] / sep)
            stded = pl.when(is_single | is_hoh).then(single_std).otherwise(married_std)
        else:
            stded = zbr
        deduc = pl.when(fed_itemizes).then(xitded).otherwise(pl.max_horizontal(stded, xitded)) + fded
        exemp = exemps * p.num("exemption_amount") + addex
        df, (taxinc,) = checkpoint(df, nd_taxinc=(agi - deduc - exemp).clip(0, None))
        brackets = p.value("brackets")
        statax = bracket_tax(taxinc, brackets)
        rate = bracket_rate(taxinc, brackets)
    elif y <= 2000:
        # State income tax deducted federally is added back for every return.
        taxinc = (pl.col("taxable_income") + pl.col("state_sales_or_income_tax_ded") - fded - addex).clip(0, None)
        df, (taxinc,) = checkpoint(df, nd_taxinc=taxinc)
        statax = bracket_tax(taxinc, p.value("brackets"))
        rate = bracket_rate(taxinc, p.value("brackets"))
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
            if p.in_effect("social_security_subtraction_agi_limit_per_taxpayer"):
                ss_taxable = pl.when(fed_agi <= p.num("social_security_subtraction_agi_limit_per_taxpayer") * txp).then(
                    ss_taxable
                ).otherwise(0.0)
            taxinc = (taxinc - ss_taxable).clip(0, None)
        df, (taxinc,) = checkpoint(df, nd_taxinc=taxinc)
        factor = p.num("bracket_inflation")
        tables = {k: _scaled(v, factor) for k, v in p.value("brackets").items()}
        married = bracket_tax(taxinc * sep, tables["married"]) / sep
        statax = (
            pl.when(is_single).then(bracket_tax(taxinc, tables["single"]))
            .when(is_hoh).then(bracket_tax(taxinc, tables["head_of_household"]))
            .otherwise(married)
        )
        rate = (
            pl.when(is_single).then(bracket_rate(taxinc, tables["single"]))
            .when(is_hoh).then(bracket_rate(taxinc, tables["head_of_household"]))
            .otherwise(bracket_rate(taxinc * sep, tables["married"]))
        )
    df, (statax,) = checkpoint(df, nd_tax_before_credits=statax)

    # --- Contribution credit (through 1999) ---
    contcr = pl.lit(0.0)
    if p.in_effect("contribution_credit_cap"):
        contcr = pl.min_horizontal(
            p["contribution_credit_rate"] * pl.col("charity_cash"), p["contribution_credit_tax_share"] * statax
        ).clip(None, p.num("contribution_credit_cap"))
        statax = (statax - contcr).clip(0, None)

    # --- Short form: a share of federal tax before credits (1981-2000) ---
    energy_credit = pl.lit(0.0)
    if p.in_effect("short_form_rate"):
        fedtax = pl.col("pre1987_taxbc") if y <= 1986 else pl.col("regular_tax")
        short_tax = p.num("short_form_rate") * fedtax
        short_selected = short_tax < statax
        rate = pl.when(short_selected).then(
            p.num("short_form_rate") * pl.col("federal_source_rate") / 100.0
        ).otherwise(rate)
        statax = pl.min_horizontal(statax, short_tax)
        if p.in_effect("energy_credit"):
            energy_credit = statax.clip(0, p.num("energy_credit"))
            statax = (statax - energy_credit).clip(0, None)
    df, (statax,) = checkpoint(df, nd_statax=statax)

    # --- Marriage credit (2007 on, joint returns) ---
    crdmar = pl.lit(0.0)
    if y >= 2007:
        pw = pl.col("pwages").clip(0, None)
        sw = pl.col("swages").clip(0, None)
        lower = pl.min_horizontal(pw, sw)
        if y >= 2022:
            # Pensions are shared between spouses.
            half = pl.col("pensions") / 2.0
            lower = pl.min_horizontal(pw + half, sw + half)
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
    if p.in_effect("relief_credit"):
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
