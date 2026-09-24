"""Michigan individual income tax calculator."""

import polars as pl

from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, household_income, unemployment_total, with_default as _with_default
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year
from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS

MI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mi" / "income_tax.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(MI_PARAMS[name], year))


def _adj(name: str, year: int) -> float:
    return float(resolve_year(STATE_ADJUSTMENT_PARAMS[name], year))


def _pp(name: str, year: int) -> float:
    return float(resolve_year(PAYROLL_PARAMS[name], year))


def compute_mi_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Michigan income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(df, ("dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "psemp", "ssemp", "proptax", "depx"))
    df = _with_default(df, "eitc")

    is_joint = pl.col("filing_status") == "married_joint"
    n_tp = pl.when(is_joint).then(2.0).otherwise(1.0)  # `data(7)`

    # `comnew(175)`: household SE tax, outside the dispatcher's deflation
    # range - computed at the real year on real (undeflated) earnings.
    setax = household_self_employment_tax(
        pl.col("psemp"), pl.col("ssemp"), pl.col("pwages"), pl.col("swages"),
        _pp("se_net_earnings_factor", year), _pp("oasdi_wage_base", year), _pp("se_oasdi_rate", year),
        _pp("se_hi_rate", year), _pp("hi_wage_base", year),
    )
    ui_total = unemployment_total()
    hh_income = household_income(
        _adj("household_income_dividend_adjustment", y), _adj("household_income_record_adjustment", y)
    )
    df = df.with_columns(mi_setax=setax, mi_hy=hh_income, mi_hh=hh_income)

    df = deflate_for_extrapolation(
        df, flate,
        ["pwages", "swages", "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "proptax",
         "agi", "eitc", "mi_hh"],
    )

    # --- AGI ---
    agi = pl.col("agi")
    if 1982 <= y <= 1986:
        rate2 = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], y))
        cap2 = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], y))
        agi = agi + (rate2 * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)).clip(0, cap2)
    if y >= 1998 and y not in (2011, 2012):
        agi = agi + _p("self_employment_tax_agi_addback_rate", y) * pl.col("mi_setax")
    elif y in (2011, 2012):
        threshold = _p("self_employment_tax_agi_addback_threshold", y)
        agi = agi + pl.when(pl.col("mi_setax") <= threshold).then(
            _p("self_employment_tax_agi_addback_lower_rate", y) * pl.col("mi_setax")
        ).otherwise(
            _p("self_employment_tax_agi_addback_upper_rate", y) * pl.col("mi_setax")
            + _p("self_employment_tax_agi_addback_amount", y)
        )

    # --- Exemptions ---
    num = n_tp + pl.col("depx").floor()
    xmp1 = _p("personal_exemption", y)
    if y < 1987:
        exemp = num * xmp1
    else:
        ump = pl.when(
            ui_total >= _p("special_exemption_ui_share", y) * pl.col("agi")
        ).then(1.0).otherwise(0.0)
        exemp = num * xmp1 + ump * _p("special_exemption", y)
        exemp = exemp + _p("dependent_exemption", y) * pl.col("depx")

    taxinc = (agi - exemp).clip(0, None)
    regtax = _p("rate", y) * taxinc

    # --- Homestead property tax credit (non-elderly branch) ---
    hhy = (
        pl.col("mi_hh")
        - _p("property_credit_self_employment_tax_deduction_rate", y)
        * pl.col("mi_setax")
    ).clip(0, None)
    allow = _p("property_credit_phaseout_start", y)
    share = _p("property_credit_income_share", y)
    pcred = (
        _p("property_credit_rate", y)
        * (pl.col("proptax") - share * hhy).clip(0, None)
    ).clip(None, _p("property_credit_cap", y))
    pcred = pl.when(hhy > allow).then(
        pcred
        * (
            1.0
            - _p("property_credit_phaseout_rate", y)
            * (hhy - allow)
            / _p("property_credit_phaseout_step", y)
        ).clip(0, None)
    ).otherwise(pcred)
    pcred = pl.when(
        hhy <= allow + _p("property_credit_phaseout_range", y)
    ).then(pcred).otherwise(0.0)

    # --- Home heating credit ---
    amex = n_tp + pl.col("depx")
    nexemp = amex.clip(None, 6.0).floor()
    hy = pl.col("mi_hy")
    if y <= 1984:
        base = MI_PARAMS["heating_credit_base_1977_1984"][0]
        fc = pl.lit(0.0)
        for k in range(1, 7):
            fc = pl.when(nexemp == k).then(float(base[k - 1])).otherwise(fc)
        fc = fc * _p("heating_credit_inflation_1977_1984", y)
    elif y <= 2005:
        base = MI_PARAMS["heating_credit_base_1985_2005"][y]
        fc = pl.lit(0.0)
        for k in range(1, 7):
            fc = pl.when(nexemp == k).then(float(base[k - 1])).otherwise(fc)
        fc = fc + (amex - 6.0).clip(0, None) * _p("heating_credit_per_extra_exemption", y)
    else:
        fc = _p("heating_credit_base_2006plus", y) + (amex - 1.0).clip(0, None) * _p("heating_credit_per_extra_exemption", y)
    fuel = (
        pl.when(nexemp > 0)
        .then((fc - _p("heating_credit_income_rate", y) * hy).clip(0, None))
        .otherwise(0.0)
        * _p("heating_credit_share", y)
    )

    earncr = _p("eitc_rate", y) * pl.col("eitc") if y >= 2008 else pl.lit(0.0)

    statax = regtax.clip(0, None) - pcred - fuel - earncr
    return df.with_columns(siitax=statax * flate)
