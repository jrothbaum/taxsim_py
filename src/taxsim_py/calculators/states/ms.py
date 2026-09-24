"""Mississippi individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    with_defaults,
    forced_standard,
    by_filing_status,
    checkpoint,
    dividend_exclusion_addback,
    unemployment_total,
    with_default,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MS_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ms" / "income_tax.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(MS_PARAMS[name], year))


def _pp(name: str, year: int) -> float:
    return float(resolve_year(PAYROLL_PARAMS[name], year))


def _by_status_year(name: str, year: int) -> pl.Expr:
    return by_filing_status({status: resolve_year(values, year) for status, values in MS_PARAMS[name].items()})


def compute_ms_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Mississippi income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui",
        "psemp", "ssemp", "depx", "charity_cash", "state_sales_or_income_tax_ded",
        "taxable_unemployment", "itemized_deduction",
    ))
    df = with_default(df, "itemizes", False)

    setax = household_self_employment_tax(
        pl.col("psemp"), pl.col("ssemp"), pl.col("pwages"), pl.col("swages"),
        _pp("se_net_earnings_factor", year), _pp("oasdi_wage_base", year), _pp("se_oasdi_rate", year),
        _pp("se_hi_rate", year), _pp("hi_wage_base", year),
    )
    dividend_adjustment = float(
        resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], y)
    )
    df = df.with_columns(ms_setax=setax, ms_divexc=dividend_exclusion_addback(y, dividend_adjustment))
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "psemp", "ssemp",
            "proptax", "otheritem", "mortgage", "charity_cash", "state_sales_or_income_tax_ded",
            "agi", "taxable_unemployment", "itemized_deduction", "ms_divexc",
        ],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = pl.when(is_joint).then(2.0).otherwise(1.0)
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    setax = pl.col("ms_setax")
    fed_itemizes = pl.col("itemizes")

    agix = pl.col("agi").clip(0, None)
    alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
    fed_char = pl.when(pl.col("agi") < 0).then(0.0).otherwise(
        pl.min_horizontal(pl.col("charity_cash"), alim50)
    ).clip(0, None)

    fed_agi = pl.col("agi")
    if y == 2020:
        fed_agi = fed_agi - pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(_p("charity_nonitemizer_addback", y) / sep, pl.col("charity_cash"))
        )

    # --- Mississippi AGI ---
    if y in (2011, 2012):
        halfse = pl.when(setax <= _p("self_employment_tax_addback_threshold", y)).then(
            _p("self_employment_tax_addback_lower_rate", y) * setax
        ).otherwise(
            _p("self_employment_tax_addback_upper_rate", y) * setax + _p("self_employment_tax_addback_amount", y)
        )
    else:
        halfse = _p("self_employment_tax_addback_rate", y) * setax
    agi = fed_agi + pl.col("ms_divexc") + halfse - _p("self_employment_tax_deduction_rate", y) * setax
    if 1982 <= y <= 1986:
        rate2 = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], y))
        cap2 = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], y))
        agi = agi + pl.when(is_joint).then(
            (rate2 * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)).clip(0, cap2)
        ).otherwise(0.0)
    if y == 2020:
        agi = agi + unemployment_total() - pl.col("taxable_unemployment") + pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(_p("charity_nonitemizer_addback", y), pl.col("charity_cash"))
        )

    df, (agi,) = checkpoint(df, ms_agi=agi)

    # --- Exemptions ---
    exemp = (_by_status_year("exemption", y) + pl.col("depx") * _p("dependent_exemption", y)) / sep

    # --- Standard deduction ---
    if y <= 1979:
        stded = pl.min_horizontal(
            _p("standard_deduction_cap_per_taxpayer", y) * txp,
            _p("standard_deduction_pct", y) * agi.clip(0, None),
        )
    else:
        stded = _by_status_year("standard_deduction", y) / sep

    # --- Itemized deductions (federal total before the high-income limit) ---
    if y <= 1986:
        deducp = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
    elif y <= 2017:
        deducp = (pl.col("proptax") + pl.col("otheritem") + salt_ded).clip(0, None) + pl.col("mortgage") + fed_char
    else:
        deducp = pl.col("itemized_deduction")
    if y < 1991:
        xitded = deducp
    else:
        xitded = (deducp - salt_ded).clip(0, None)
        if y <= 2017:
            if y >= 2013:
                threshold = (
                    _p("itemized_limit_threshold_base", y)
                    * _p("itemized_limit_inflation", y)
                    * by_filing_status(MS_PARAMS["itemized_limit_status_multiplier"])
                )
            else:
                threshold = _p("itemized_limit_threshold_base", y) * _p("itemized_limit_inflation", y) / sep
            dedphs = deducp - pl.col("itemized_deduction")
            limited = (
                deducp - dedphs - (salt_ded - salt_ded * dedphs / deducp)
            ).clip(0, None)
            xitded = pl.when((agi > threshold) & (deducp > 0)).then(limited).otherwise(xitded)
    if y <= 1978:
        contributions = -_p("charity_agi_limit", y) * agi.clip(0, None)
        xitded = (xitded - contributions).clip(0, None)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)

    df, (deduc, exemp) = checkpoint(df, ms_deduc=pl.max_horizontal(stded, xitded), ms_exemp=exemp)
    df, (taxinc,) = checkpoint(df, ms_taxinc=(agi - deduc - exemp).clip(0, None))
    brackets = resolve_year(MS_PARAMS["brackets"], y)
    df, (statax,) = checkpoint(df, ms_table_tax=bracket_tax(taxinc, brackets))

    # --- Married couples: tax as separate returns if lower ---
    wages_total = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    agih = pl.min_horizontal(agi, pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + _p("joint_split_other_income_share", y) * (agi - wages_total))
    agiw = agi - agih
    exemph = exemp * agih / agi
    exempw = exemp - exemph
    taxyh1 = (agih - exemph).clip(0, None)
    taxyw1 = (agiw - exempw).clip(0, None)
    deducw = pl.min_horizontal(taxyw1, deduc * agiw / agi)
    deduch = deduc - deducw
    split_tax = bracket_tax((taxyh1 - deduch).clip(0, None), brackets) + bracket_tax(
        (taxyw1 - deducw).clip(0, None), brackets
    )
    statax = pl.when(is_joint & (agi > 0)).then(pl.min_horizontal(statax, split_tax)).otherwise(statax)

    return df.with_columns(siitax=statax * flate)
