"""Montana individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    with_defaults,
    by_filing_status,
    checkpoint,
    unemployment_total,
    forced_standard,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mt" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(MT_PARAMS[name], year))


def _brackets(y: int) -> list[list[float]]:
    bounds = resolve_year(MT_PARAMS["bracket_bounds"], y)
    if y <= 2004:
        rates = MT_PARAMS["rates_through_2004"]
        factor = _p("bracket_inflation", y)
    else:
        rates = MT_PARAMS["rates_from_2005"]
        factor = 1.0
    starts = [0.0] + [float(b) * factor for b in bounds]
    return [[start, float(rate)] for start, rate in zip(starts, rates)]


def compute_mt_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Montana income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "depx", "childcare", "charity_cash", "stcg", "ltcg",
        "ui", "pui", "sui", "state_sales_or_income_tax_ded", "taxable_unemployment", "itemized_deduction",
        "fiitax", "eitc",
    ))

    df = df.with_columns(mt_ui=unemployment_total())
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "childcare", "charity_cash", "stcg", "ltcg",
            "mt_ui", "state_sales_or_income_tax_ded", "taxable_unemployment", "itemized_deduction", "agi",
            "fiitax", "eitc",
        ],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    ntp = pl.when(is_joint).then(2.0).otherwise(1.0)
    depx = pl.col("depx")
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    fed_agi = pl.col("agi")
    fullcg = pl.col("stcg") + pl.col("ltcg")
    if y >= 1987:
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        capgn = pl.max_horizontal(fullcg, -loss_limit / flate / sep)
    else:
        capgn = fullcg

    # --- Montana AGI ---
    agi = fed_agi
    if y == 2020:
        agi = agi + pl.col("mt_ui") - pl.col("taxable_unemployment")
    agi = agi - _p("capital_gains_exclusion_rate", y) * capgn.clip(0, None)
    if y >= 1984:
        agi = agi - pl.col("mt_ui")
        agi = agi + pl.when(is_sep & (fullcg < 0)).then(
            pl.max_horizontal(pl.lit(_p("separate_capital_loss_floor", y)), fullcg)
        ).otherwise(0.0)

    df, (agi, capgn) = checkpoint(df, mt_agi=agi, mt_capgn=capgn)

    # --- Standard deduction ---
    pct = _p("standard_deduction_pct", y)
    cap = _p("standard_deduction_cap", y)
    floor = _p("standard_deduction_floor", y)

    def standard(income: pl.Expr, taxpayers: pl.Expr | float) -> pl.Expr:
        return pl.max_horizontal(
            floor * taxpayers, pl.min_horizontal(cap * taxpayers, pct * income.clip(0, None))
        )

    std_taxpayers = pl.when(is_hoh).then(float(MT_PARAMS["head_of_household_standard_deduction_taxpayers"])).otherwise(ntp)
    stded = standard(agi, std_taxpayers)

    # --- Itemized deductions ---
    agix = fed_agi.clip(0, None)
    alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
    fed_char = pl.when(fed_agi < 0).then(0.0).otherwise(pl.min_horizontal(pl.col("charity_cash"), alim50)).clip(0, None)
    if y <= 1986:
        deducp = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
    elif y <= 2017:
        deducp = (pl.col("proptax") + pl.col("otheritem") + salt_ded).clip(0, None) + pl.col("mortgage") + fed_char
    else:
        deducp = pl.col("itemized_deduction")
    fed_tax = pl.col("fiitax")
    if y == 2008:
        rebate = MT_PARAMS["federal_tax_rebate_2008"]
        fed_tax = fed_tax - rebate["per_taxpayer"] * ntp - rebate["per_dependent"] * depx
    xitded = (deducp - salt_ded).clip(0, None) + pl.min_horizontal(
        _p("federal_tax_deduction_cap_per_taxpayer", y) * ntp, fed_tax.clip(0, None)
    )
    if 1991 <= y <= 2017:
        if y >= 2013:
            threshold = (
                _p("itemized_limit_threshold_base", y)
                * _p("itemized_limit_inflation", y)
                * by_filing_status(MT_PARAMS["itemized_limit_status_multiplier"])
            )
        else:
            threshold = _p("itemized_limit_threshold_base", y) * _p("itemized_limit_inflation", y) / sep
        reduce = pl.min_horizontal(
            _p("itemized_limit_max_share", y) * (deducp - salt_ded),
            _p("itemized_limit_rate", y) * (agi - threshold),
        ) * _p("itemized_limit_fraction", y)
        xitded = xitded - pl.when(agi > threshold).then(reduce).otherwise(0.0)
    caps = MT_PARAMS["child_care_expense_cap"]
    ich = depx.clip(1, 3).floor()
    care_cap = pl.when(ich >= 3).then(float(caps[2])).when(ich >= 2).then(float(caps[1])).otherwise(float(caps[0]))
    chexp = (
        pl.min_horizontal(pl.col("childcare"), care_cap)
        - _p("child_care_phaseout_rate", y) * (agi - _p("child_care_phaseout_start", y)).clip(0, None)
    ).clip(0, None)
    xitded = xitded + pl.when(depx > 0).then(chexp).otherwise(0.0)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    df, (stded, xitded) = checkpoint(df, mt_stded=stded, mt_xitded=xitded)
    deduc = pl.max_horizontal(stded, xitded)

    # --- Exemptions and tax ---
    xmp = _p("exemption", y)
    exemp = (ntp + depx) * xmp
    df, (taxinc,) = checkpoint(df, mt_taxinc=(agi - deduc - exemp).clip(0, None))
    brackets = _brackets(y)
    df, (statax,) = checkpoint(df, mt_table_tax=bracket_tax(taxinc, brackets))

    # --- Married couples: tax as separate returns if lower ---
    wages_total = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    agih = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + _p("joint_split_other_income_share", y) * (
        agi - wages_total
    )
    agiw = agi - agih
    stdh = standard(agih, 1.0)
    stdw = standard(agiw, 1.0)
    split_stded = stdh + stdw if y >= 1981 else stded
    half = _p("joint_split_itemized_share", y)
    xitd_each = pl.when((xitded > 0) & (agi > 0)).then(half * xitded).otherwise(0.0)
    use_itemized = xitded > split_stded
    dedh = pl.when(use_itemized).then(xitd_each).otherwise(stdh)
    dedw = pl.when(use_itemized).then(xitd_each).otherwise(stdw)
    exempw = pl.lit(xmp)
    exemph = exemp - exempw
    split_tax = bracket_tax((agih - dedh - exemph).clip(0, None), brackets) + bracket_tax(
        (agiw - dedw - exempw).clip(0, None), brackets
    )
    statax = pl.when(is_joint).then(pl.min_horizontal(statax, split_tax)).otherwise(statax)
    df, (statax,) = checkpoint(df, mt_tax=statax * _p("surtax", y))

    # --- Credits ---
    cgcred = _p("capital_gains_credit_rate", y) * capgn.clip(0, None)
    statax = (statax - cgcred).clip(0, None)
    if y == 2007:
        statax = statax - pl.when(pl.col("proptax") > 0).then(float(MT_PARAMS["homeowner_credit_2007"])).otherwise(0.0)
    statax = statax - _p("eitc_match_rate", y) * pl.col("eitc")

    return df.with_columns(siitax=statax * flate)
