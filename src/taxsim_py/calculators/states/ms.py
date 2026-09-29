"""Mississippi individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_joint, files_separate, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    forced_standard,
    by_filing_status,
    checkpoint,
    dividend_exclusion_addback,
    pre1987_federal_itemizing,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MS_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ms" / "income_tax.yaml")


def _by_status_year(name: str, year: int) -> pl.Expr:
    return by_filing_status({status: resolve_year(values, year) for status, values in MS_PARAMS[name].items()})


def compute_ms_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Mississippi income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(MS_PARAMS, effective_year)

    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    dividend_input_adjustment()
    df = df.with_columns(ms_setax=setax, ms_divexc=dividend_exclusion_addback(y))
    df = deflate_for_extrapolation(df, flate, extra=("ms_divexc",))

    is_joint = files_joint()
    is_sep = files_separate()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
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
            pl.min_horizontal(p.num("charity_nonitemizer_addback") / sep, pl.col("charity_cash"))
        )

    # --- Mississippi AGI ---
    if y in (2011, 2012):
        halfse = pl.when(setax <= p.num("self_employment_tax_addback_threshold")).then(
            p.num("self_employment_tax_addback_lower_rate") * setax
        ).otherwise(
            p.num("self_employment_tax_addback_upper_rate") * setax + p.num("self_employment_tax_addback_amount")
        )
    else:
        halfse = p.num("self_employment_tax_addback_rate") * setax
    agi = fed_agi + pl.col("ms_divexc") + halfse - p.num("self_employment_tax_deduction_rate") * setax
    # Retirement income is exempt, up to a cap per taxpayer through 1993.
    if y <= 1993:
        agi = agi - pl.min_horizontal(pl.col("pensions"), p.num("retirement_exclusion_per_taxpayer") * txp)
    else:
        agi = agi - pl.col("pensions")
    # Through 1979 other non-property income is excluded.
    if y <= 1979:
        agi = agi - pl.col("nonprop")
    if 1982 <= y <= 1986:
        agi = agi + pl.col("pre1987_twoded")
    # Social Security is exempt from 1984; before, all benefits are income.
    if y >= 1984:
        agi = agi - pl.col("taxable_social_security")
    else:
        agi = agi + pl.col("gssi") - pl.col("taxable_social_security")
    if y == 2020:
        agi = agi + unemployment_total() - pl.col("taxable_unemployment") + pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(p.num("charity_nonitemizer_addback"), pl.col("charity_cash"))
        )

    df, (agi,) = checkpoint(df, ms_agi=agi)

    # --- Exemptions ---
    exemp = (
        _by_status_year("exemption", y) + pl.col("depx") * p.num("dependent_exemption")
        + aged_count() * p.num("aged_exemption")
    ) / sep

    # --- Standard deduction ---
    if y <= 1979:
        stded = pl.min_horizontal(
            p.num("standard_deduction_cap_per_taxpayer") * txp,
            p.num("standard_deduction_pct") * agi.clip(0, None),
        )
    else:
        stded = _by_status_year("standard_deduction", y) / sep

    # --- Itemized deductions (federal total before the high-income limit) ---
    if y <= 1986:
        deducp, _, _ = pre1987_federal_itemizing(y)
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
                    p.num("itemized_limit_threshold_base")
                    * p.num("itemized_limit_inflation")
                    * by_filing_status(MS_PARAMS["itemized_limit_status_multiplier"])
                )
            else:
                threshold = p.num("itemized_limit_threshold_base") * p.num("itemized_limit_inflation") / sep
            dedphs = deducp - pl.col("itemized_deduction")
            limited = (
                deducp - dedphs - (salt_ded - salt_ded * dedphs / deducp)
            ).clip(0, None)
            xitded = pl.when((agi > threshold) & (deducp > 0)).then(limited).otherwise(xitded)
    if y <= 1978:
        contributions = -p.num("charity_agi_limit") * agi.clip(0, None)
        xitded = (xitded - contributions).clip(0, None)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)

    df, (stded, xitded, deduc, exemp) = checkpoint(
        df,
        ms_stded=stded,
        ms_xitded=xitded,
        ms_deduc=pl.max_horizontal(stded, xitded),
        ms_exemp=exemp,
    )
    df, (taxinc,) = checkpoint(df, ms_taxinc=(agi - deduc - exemp).clip(0, None))
    brackets = resolve_year(MS_PARAMS["brackets"], y)
    df, (statax,) = checkpoint(df, ms_table_tax=bracket_tax(taxinc, brackets))

    # --- Married couples: tax as separate returns if lower ---
    wages_total = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    agih = pl.min_horizontal(agi, pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + p.num("joint_split_other_income_share") * (agi - wages_total))
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
    rate_expr = pl.when(is_joint & (agi > 0)).then(
        bracket_rate((taxyw1 - deducw).clip(0, None), brackets)
    ).otherwise(bracket_rate(taxinc, brackets))
    statax = pl.when(is_joint & (agi > 0)).then(pl.min_horizontal(statax, split_tax)).otherwise(statax)

    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        rate=rate_expr,
    )
