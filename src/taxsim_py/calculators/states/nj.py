"""New Jersey gross income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, checkpoint, interpolate_table
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NJ_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nj" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(NJ_PARAMS[name], year))


def _schedule_tax(income: pl.Expr, uses_married: pl.Expr, y: int) -> pl.Expr:
    if y <= 1990:
        return bracket_tax(income, resolve_year(NJ_PARAMS["brackets_through_1990"], y))
    tables = NJ_PARAMS["brackets"]
    return (
        pl.when(uses_married)
        .then(bracket_tax(income, resolve_year(tables["married"], y)))
        .otherwise(bracket_tax(income, resolve_year(tables["single"], y)))
    )


def compute_nj_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate New Jersey gross income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(df, (
        "dividends", "intrec", "psemp", "ssemp", "stcg", "ltcg", "proptax", "depx",
        "taxable_unemployment", "eitc", "pre1987_capgn",
    ))
    dividend_adjustment = float(
        resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], y)
    )
    df = df.with_columns(nj_dividends=pl.col("dividends") + dividend_adjustment)
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "nj_dividends", "intrec", "psemp", "ssemp", "stcg", "ltcg", "proptax",
            "taxable_unemployment", "eitc", "pre1987_capgn",
        ],
    )

    status = pl.col("filing_status")
    is_single = status == "single"
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    uses_married = is_joint | is_hoh
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = pl.when(is_joint).then(2.0).otherwise(1.0)
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
    business = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
    agi = wages + pl.col("nj_dividends") + pl.col("intrec") + business.clip(0, None) + capgn.clip(0, None)
    if y < 1992:
        agi = agi + pl.col("taxable_unemployment")
    df, (agi, capgn) = checkpoint(df, nj_agi=agi - capgn + fullcg, nj_capgn=capgn)

    # --- No-tax status ---
    if y <= 1999:
        threshold = _p("no_tax_threshold", y) / sep
        no_tax = agi < threshold if 1994 <= y <= 1996 else agi <= threshold
    elif y == 2000:
        limits = NJ_PARAMS["no_tax_threshold_2000"]
        no_tax = pl.when(is_single).then(agi <= limits["single"]).otherwise(agi <= limits["other"] / sep)
    else:
        nts = pl.when(is_hoh).then(float(NJ_PARAMS["head_of_household_no_tax_taxpayers"])).otherwise(txp)
        no_tax = agi <= _p("no_tax_threshold_per_taxpayer", y) * nts

    # --- Taxable income and tax ---
    exemp = txp * _p("exemption_per_taxpayer", y) + depx * _p("exemption_per_dependent", y)
    df, (taxinc,) = checkpoint(df, nj_taxinc=(agi - exemp).clip(0, None))
    rescr = pl.lit(0.0)
    if 1985 <= y <= 1989:
        floor = interpolate_table(taxinc, NJ_PARAMS["property_tax_floor_1985_1989"]) / sep
        pded = pl.when(proptax > 0).then(pl.max_horizontal(floor, proptax)).otherwise(0.0)
        applies = agi > _p("no_tax_threshold", y) / sep
        rescr = pl.when(applies).then(
            NJ_PARAMS["property_tax_excess_credit_rate"] * (pded - taxinc).clip(0, None)
        ).otherwise(0.0)
        taxinc = pl.when(applies).then((taxinc - pded).clip(0, None)).otherwise(taxinc)
    statax = _schedule_tax(taxinc, uses_married, y)
    df, (statax, taxinc) = checkpoint(
        df,
        nj_tax=pl.when(no_tax).then(0.0).otherwise(statax),
        nj_taxinc_final=pl.when(no_tax).then(0.0).otherwise(taxinc),
    )

    # --- Property tax credits and rebates ---
    pcred = pl.lit(0.0)
    if 1985 <= y <= 1989:
        pcred = pl.when((agi <= _p("no_tax_threshold", y) / sep) & (proptax > 0)).then(
            float(NJ_PARAMS["low_income_homeowner_credit_1985_1989"])
        ).otherwise(0.0)
    rebate = pl.lit(0.0)
    if 1990 <= y <= 2003:
        rebate = pl.when((proptax > 0) & (agi < _p("homestead_rebate_income_limit", y))).then(
            _p("homestead_rebate", y) / sep
        ).otherwise(0.0)
    if y >= 1996:
        exemp_pr = txp * _p("exemption_per_taxpayer", y) + depx * _p("exemption_per_dependent", y)
        pded = pl.min_horizontal(
            _p("property_tax_deduction_cap", y) / sep, _p("property_tax_deduction_share", y) * proptax
        )
        df, (income_pr,) = checkpoint(df, nj_taxinc_property=(agi - exemp_pr - pded).clip(0, None))
        statpr = _schedule_tax(income_pr, uses_married, y)
        credit_amount = _p("property_tax_credit", y) / sep
        eligible = proptax > 2
        take_deduction = eligible & (statax - statpr >= credit_amount)
        pcred = pl.when(eligible & ~take_deduction & (proptax > 0) & (taxinc > 0)).then(credit_amount).otherwise(pcred)
        statax = pl.when(take_deduction).then(statpr).otherwise(statax)

    statax = (statax - rescr - pcred).clip(0, None) - rebate

    # --- Earned income credit ---
    earncr = _p("eitc_match_rate", y) * pl.col("eitc")
    if 2000 <= y <= 2006:
        earncr = pl.when(
            (agi <= float(NJ_PARAMS["eitc_income_limit_2000_2006"])) & (depx > 0)
        ).then(earncr).otherwise(0.0)
    statax = statax - earncr

    return df.with_columns(siitax=statax * flate)
