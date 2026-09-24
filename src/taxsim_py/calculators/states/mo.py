"""Missouri individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, by_filing_status, checkpoint, with_default, forced_standard, itemize_choice
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MO_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mo" / "income_tax.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _p(name: str, year: int) -> float:
    return float(resolve_year(MO_PARAMS[name], year))


def _pp(name: str, year: int) -> float:
    return float(resolve_year(PAYROLL_PARAMS[name], year))


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def compute_mo_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Missouri income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "depx", "charity_cash", "state_sales_or_income_tax_ded",
        "itemized_deduction", "standard_deduction", "regular_tax", "ccc", "ccc_uncapped", "odc", "actc",
        "eitc", "making_work_pay", "addmed", "fiitax", "pre1987_taxbc", "pre1987_chcr", "pre1987_earncr",
    ))
    df = with_default(df, "itemizes", False)

    setax = household_self_employment_tax(
        pl.col("psemp"), pl.col("ssemp"), pl.col("pwages"), pl.col("swages"),
        _pp("se_net_earnings_factor", year), _pp("oasdi_wage_base", year), _pp("se_oasdi_rate", year),
        _pp("se_hi_rate", year), _pp("hi_wage_base", year),
    )
    df = df.with_columns(mo_setax=setax)
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "charity_cash",
            "state_sales_or_income_tax_ded", "agi", "itemized_deduction", "standard_deduction",
            "regular_tax", "ccc", "odc", "actc", "eitc", "making_work_pay",
        ],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = pl.when(is_joint).then(2.0).otherwise(1.0)
    depx = pl.col("depx")
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    setax = pl.col("mo_setax")
    agi = pl.col("agi")
    wages_total = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    split_share = _p("joint_split_other_income_share", y)
    agih = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + split_share * (agi - wages_total)
    agiw = agi - agih

    # --- Federal return values as Missouri reads them ---
    if y <= 1986:
        deducp = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
        fed_zbr = by_filing_status(
            {s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], y) for s in _STATUSES}
        )
        fed_itemizes = deducp > fed_zbr if y <= 1981 else itemize_choice(deducp > fed_zbr)
        dedphs = pl.lit(0.0)
    else:
        fed_itemizes = pl.col("itemizes")
        fed_zbr = pl.when(fed_itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
        agix = agi.clip(0, None)
        alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
        fed_char = pl.when(agi < 0).then(0.0).otherwise(pl.min_horizontal(pl.col("charity_cash"), alim50)).clip(0, None)
        if y <= 2017:
            deducp = (pl.col("proptax") + pl.col("otheritem") + salt_ded).clip(0, None) + pl.col("mortgage") + fed_char
        else:
            deducp = pl.col("itemized_deduction")
        dedphs = deducp - pl.col("itemized_deduction") if 1991 <= y <= 2017 else pl.lit(0.0)

    # --- Exemptions ---
    exemp = (
        pl.when(is_hoh)
        .then(_p("exemption_head_of_household", y))
        .otherwise(_p("exemption_per_taxpayer", y) * txp)
        + depx * _p("exemption_per_dependent", y)
    )
    if y == 2017:
        bonus = _p("low_income_exemption", y)
        limit = _p("low_income_exemption_limit", y)
        joint_bonus = pl.when(agih < limit).then(bonus).otherwise(0.0) + pl.when(agiw < limit).then(bonus).otherwise(0.0)
        exemp = exemp + pl.when(is_joint).then(joint_bonus).otherwise(pl.when(agi < limit).then(bonus).otherwise(0.0))

    # --- Itemized deductions (only when itemizing federally) ---
    rate = _p("payroll_tax_rate", y)
    above_rate = _p("payroll_tax_rate_above_base", y)
    ceiling = _p("payroll_tax_wage_base", y)

    def payroll_tax(wages: pl.Expr) -> pl.Expr:
        return pl.min_horizontal(wages, pl.lit(ceiling)) * rate + (wages - ceiling).clip(0, None) * above_rate

    fica = pl.when(is_joint).then(payroll_tax(pl.col("pwages")) + payroll_tax(pl.col("swages"))).otherwise(
        payroll_tax(wages_total)
    )
    socsec = fica + setax + (pl.col("addmed") if y >= 2013 else 0.0)
    xitded = (deducp + pl.min_horizontal(socsec, _p("payroll_tax_deduction_cap", y) * txp) - salt_ded).clip(0, None)
    if y == 1993:
        septx = pl.min_horizontal(_p("self_employment_tax_deduction_cap_1993", y) * txp, setax)
    elif y in (2011, 2012):
        septx = pl.when(setax <= _p("self_employment_tax_deduction_threshold", y)).then(
            _p("self_employment_tax_deduction_lower_rate", y) * setax
        ).otherwise(
            _p("self_employment_tax_deduction_upper_rate", y) * setax + _p("self_employment_tax_deduction_amount", y)
        )
    else:
        septx = _p("self_employment_tax_deduction_rate", y) * setax
    xitded = (xitded - septx).clip(0, None)
    stax = salt_ded
    if 1993 <= y <= 2017:
        if y >= 2013:
            threshold = (
                _p("itemized_limit_threshold_base", y)
                * _p("itemized_limit_inflation", y)
                * by_filing_status(MO_PARAMS["itemized_limit_status_multiplier"])
            )
        else:
            threshold = _p("itemized_limit_threshold_base", y) * _p("itemized_limit_inflation", y) / sep
        xtot = salt_ded + pl.col("proptax") + pl.col("mortgage") + pl.col("charity_cash")
        over = agi - threshold
        xconst = pl.min_horizontal(_p("itemized_limit_max_share", y) * xtot, _p("itemized_limit_rate", y) * over)
        stax = pl.when((xtot > 0) & (over > 0)).then((xtot - xconst) / xtot * salt_ded).otherwise(salt_ded)
    xitded = (xitded - dedphs + (salt_ded - stax)).clip(0, None)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    df, (deduc,) = checkpoint(
        df, mo_deduc=pl.when(fed_itemizes).then(pl.max_horizontal(fed_zbr, xitded)).otherwise(fed_zbr)
    )

    # --- Deduction for federal income tax ---
    if y <= 1986:
        fedtax = pl.col("pre1987_taxbc") - pl.col("pre1987_chcr") - pl.col("pre1987_earncr")
    else:
        if y == 2021:
            ccc_used = pl.lit(0.0)
        elif y >= 1998:
            ccc_used = pl.col("ccc")
        else:
            ccc_used = pl.min_horizontal(pl.col("ccc_uncapped"), pl.col("regular_tax"))
        if year == 2021:
            ctc_used = pl.col("actc")
        elif year >= 1998:
            ctc_used = pl.col("odc")
        else:
            ctc_used = pl.lit(0.0)
        fedtax = pl.col("regular_tax") - ccc_used - pl.col("eitc") - ctc_used - pl.col("making_work_pay")
    fedtax = pl.min_horizontal(fedtax.clip(0, None), _p("federal_tax_deduction_cap_per_taxpayer", y) * txp)
    if y >= 2019:
        share = pl.lit(0.0)
        for upper, value in reversed(MO_PARAMS["federal_tax_deduction_share_2019"]):
            share = pl.when(agi <= upper).then(pl.lit(float(value))).otherwise(share)
        fedtax = share * fedtax

    df, (fedtax, exemp) = checkpoint(df, mo_fedtax=fedtax, mo_exemp=exemp)
    df, (taxinc,) = checkpoint(df, mo_taxinc=(agi - deduc - fedtax - exemp).clip(0, None))
    aif = _p("bracket_inflation", y)
    brackets = _scaled(resolve_year(MO_PARAMS["brackets"], y), aif)
    df, (taxh, taxw) = checkpoint(df, mo_taxinc_h=taxinc * agih / agi, mo_taxinc_w=taxinc * agiw / agi)
    statax = bracket_tax(taxinc, brackets)
    split_tax = bracket_tax(taxh, brackets) + bracket_tax(taxw, brackets)
    statax = pl.when(is_joint & (agi > 0)).then(pl.min_horizontal(statax, split_tax)).otherwise(statax)
    statax = pl.when(taxinc < _p("minimum_taxable_income", y) * aif).then(0.0).otherwise(statax)

    return df.with_columns(siitax=statax * flate)
