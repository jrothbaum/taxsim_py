"""North Dakota individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    checkpoint,
    dividend_exclusion_addback,
    itemize_choice,
    with_default,
    with_defaults,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

ND_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nd" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _p(name: str, year: int) -> float:
    return float(resolve_year(ND_PARAMS[name], year))


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def _schedule_2009(uppers: list[float], y: int) -> list[list[float]]:
    rates = resolve_year(ND_PARAMS["rates"], y)
    starts = [0.0, *[float(u) for u in uppers]]
    return [[start, float(rate)] for start, rate in zip(starts, rates)]


def _single_schedule(y: int) -> list[list[float]]:
    """Single filers' schedule for 2001 on (the marriage credit reuses it)."""
    if y <= 2008:
        return _scaled(ND_PARAMS["brackets_2001"]["single"], _p("bracket_inflation_2001", y))
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
        credit = pl.col("ccc") + pl.col("odc")
        earncr = pl.col("eitc")
        before = regtax + almtax if y >= 2000 else regtax
        taxaft = (before - credit).clip(0, None)
        pretax = pl.max_horizontal(regtax, almtax, taxaft - earncr + pl.min_horizontal(earncr, taxaft) + credit)
    return (pretax - credit - earncr + almtax).clip(0, None)


def compute_nd_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate North Dakota income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = ND_PARAMS
    df = with_defaults(df, (
        "dividends", "intrec", "stcg", "ltcg", "proptax", "otheritem", "mortgage", "depx", "charity_cash",
        "state_sales_or_income_tax_ded", "taxable_income", "regular_tax", "amt", "ccc", "odc", "eitc", "credit",
        "pre1987_pretax", "pre1987_earncr", "pre1987_almtax", "pre1987_taxbc", "pre1987_capgn",
    ))
    df = with_default(df, "itemizes", False)
    dividend_adjustment = float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], y))
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "dividends", "stcg", "ltcg", "charity_cash", "state_sales_or_income_tax_ded",
            "agi", "taxable_income", "regular_tax", "amt", "ccc", "odc", "eitc",
        ],
    )

    status = pl.col("filing_status")
    is_single = status == "single"
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = pl.when(is_joint).then(2.0).otherwise(1.0)
    fed_agi = pl.col("agi")
    addex = pl.when(is_joint | is_hoh).then(p["additional_exemption"]).otherwise(0.0)
    fded = _federal_tax_deduction(y) if y <= 2000 else pl.lit(0.0)

    if y <= 1986:
        # --- AGI ---
        agi = fed_agi
        if y <= 1980:
            capded = (pl.col("stcg") + pl.col("ltcg") - pl.col("pre1987_capgn")).clip(0, None)
            agi = agi + capded * p["capital_gains_deduction_addback_share"]
        elif y <= 1982:
            agi = agi - pl.min_horizontal(pl.col("intrec"), _p("interest_exclusion_per_taxpayer", y) * txp)
            if y == 1982:
                agi = agi + dividend_exclusion_addback(y, dividend_adjustment)
        else:
            agi = agi - pl.min_horizontal(pl.col("intrec"), _p("interest_exclusion_per_taxpayer", y) * txp)
        df, (agi,) = checkpoint(df, nd_agi=agi)

        # --- Federal itemized deduction and zero bracket ---
        gross = (
            pl.col("state_sales_or_income_tax_ded") + pl.col("proptax") + pl.col("otheritem")
            + pl.col("mortgage") + pl.col("charity_cash")
        )
        zbr = by_filing_status({s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], y) for s in _STATUSES})
        exemps = txp + pl.col("depx")
        amex = float(resolve_year(PRE1987_PARAMS["personal_exemption_amount"], y)) * exemps
        natural = gross > zbr
        chosen = natural if y <= 1981 else itemize_choice(natural)
        # Federal itemizing is dropped when it cannot lower taxable income.
        fed_itemizes = chosen & (fed_agi - amex - zbr > 0)
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
        exemp = exemps * _p("exemption_amount", y) + addex
        df, (taxinc,) = checkpoint(df, nd_taxinc=(agi - deduc - exemp).clip(0, None))
        if y == 1977:
            brackets = p["brackets_1977"]
        elif y <= 1982:
            brackets = p["brackets_1978"]
        else:
            brackets = p["brackets_1983"]
        statax = bracket_tax(taxinc, brackets)
    elif y <= 2000:
        # State income tax deducted federally is added back for every return.
        taxinc = (pl.col("taxable_income") + pl.col("state_sales_or_income_tax_ded") - fded - addex).clip(0, None)
        df, (taxinc,) = checkpoint(df, nd_taxinc=taxinc)
        statax = bracket_tax(taxinc, p["brackets_1987"])
    else:
        gshort = pl.col("stcg")
        glong = pl.col("ltcg")
        fullcg = gshort + glong
        cgltg = (
            pl.when((fullcg > 0) & (glong > 0) & (gshort >= 0)).then(glong)
            .when((fullcg > 0) & (glong > 0) & (gshort < 0)).then(fullcg)
            .otherwise(0.0)
        )
        cgexc = _p("capital_gain_exclusion", y) * cgltg.clip(0, None)
        qdiv = _p("qualified_dividend_exclusion", y) * (pl.col("dividends") + dividend_adjustment)
        df, (taxinc,) = checkpoint(df, nd_taxinc=(pl.col("taxable_income") - cgexc - qdiv).clip(0, None))
        if y <= 2008:
            factor = _p("bracket_inflation_2001", y)
            tables = p["brackets_2001"]
            married = _scaled(tables["married"], factor)
            statax = (
                pl.when(is_single).then(bracket_tax(taxinc, _scaled(tables["single"], factor)))
                .when(is_hoh).then(bracket_tax(taxinc, _scaled(tables["head_of_household"], factor)))
                .when(is_sep).then(bracket_tax(taxinc * 2.0, married) / 2.0)
                .otherwise(bracket_tax(taxinc, married))
            )
        else:
            uppers = p["bracket_uppers"][y]
            married = bracket_tax(taxinc * sep, _schedule_2009(uppers["married"], y)) / sep
            statax = (
                pl.when(is_single).then(bracket_tax(taxinc, _schedule_2009(uppers["single"], y)))
                .when(is_hoh).then(bracket_tax(taxinc, _schedule_2009(uppers["head_of_household"], y)))
                .otherwise(married)
            )
    df, (statax,) = checkpoint(df, nd_tax_before_credits=statax)

    # --- Contribution credit (through 1999) ---
    if y <= 1999:
        contcr = pl.min_horizontal(
            p["contribution_credit_rate"] * pl.col("charity_cash"), p["contribution_credit_tax_share"] * statax
        ).clip(None, _p("contribution_credit_cap", y))
        statax = (statax - contcr).clip(0, None)

    # --- Short form: a share of federal tax before credits (1981-2000) ---
    if 1981 <= y <= 2000:
        fedtax = pl.col("pre1987_taxbc") if y <= 1986 else pl.col("regular_tax")
        statax = pl.min_horizontal(statax, _p("short_form_rate", y) * fedtax)
        if y <= 1982:
            statax = (statax - statax.clip(0, p["energy_credit_1981_1982"])).clip(0, None)
    df, (statax,) = checkpoint(df, nd_statax=statax)

    # --- Marriage credit (2007 on, joint returns) ---
    if y >= 2007:
        pw = pl.col("pwages").clip(0, None)
        sw = pl.col("swages").clip(0, None)
        lower = pl.min_horizontal(pw, sw)
        single = _single_schedule(y)
        taxin3 = (lower - _p("marriage_credit_deduction", y)).clip(0, None)
        taxin4 = (taxinc - taxin3).clip(0, None)
        crdmar = (statax - bracket_tax(taxin3, single) - bracket_tax(taxin4, single)).clip(0, _p("marriage_credit_max", y))
        eligible = (
            is_joint & (taxinc > _p("marriage_credit_taxable_income_min", y))
            & (lower > _p("marriage_credit_earnings_min", y))
        )
        statax = pl.when(eligible).then((statax - crdmar).clip(0, None)).otherwise(statax)

    # --- Tax relief credit 2021 ---
    if y == 2021:
        statax = (statax - _p("relief_credit", y) * txp).clip(0, None)

    df = df.with_columns(siitax=statax * flate)
    return df
