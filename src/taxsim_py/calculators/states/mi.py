"""Michigan individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    household_income,
    interpolate_table,
    unemployment_total,
    with_default as _with_default,
    with_defaults,
    with_state_detail,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mi" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(MI_PARAMS[name], year))


def _adj(name: str, year: int) -> float:
    return float(resolve_year(STATE_ADJUSTMENT_PARAMS[name], year))



def compute_mi_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Michigan income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    df = with_defaults(
        df,
        ("dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "psemp", "ssemp", "proptax", "depx",
         "pensions", "taxable_social_security", "rentpaid"),
    )
    df = _with_default(df, "eitc")

    is_joint = pl.col("filing_status") == "married_joint"
    is_sep = pl.col("filing_status") == "married_separate"
    n_tp = taxpayer_count()
    aged = aged_count()

    # `comnew(175)`: household SE tax, outside the dispatcher's deflation
    # range - computed at the real year on real (undeflated) earnings.
    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    ui_total = unemployment_total()
    hh_income = household_income(
        _adj("household_income_dividend_adjustment", y), _adj("household_income_record_adjustment", y)
    )
    df = df.with_columns(mi_setax=setax, mi_hy=hh_income, mi_hh=hh_income)

    df = deflate_for_extrapolation(
        df, flate,
        ["pwages", "swages", "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "proptax",
         "agi", "eitc", "mi_hh", "pensions", "taxable_social_security", "rentpaid"],
    )

    # --- AGI ---
    agi = pl.col("agi")
    if 1982 <= y <= 1986:
        agi = agi + pl.col("pre1987_twoded")
    if y >= 1984:
        agi = agi - pl.col("taxable_social_security")
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

    # --- Retirement subtraction for taxpayers 65 or older ---
    pensions = pl.col("pensions")
    if y <= 1993:
        penexc = pl.when(is_joint | is_sep).then(
            pl.min_horizontal(pensions / pl.when(is_sep).then(2.0).otherwise(1.0), _p("pension_exclusion_joint_1977_1993", y))
        ).otherwise(pl.min_horizontal(pensions, _p("pension_exclusion_single_1977_1993", y)))
    else:
        penexc = pl.min_horizontal(pensions, _p("pension_exclusion_per_taxpayer", y) * n_tp)
        if y == 1994:
            penexc = pl.when(is_joint).then(pl.min_horizontal(pensions, _p("pension_exclusion_joint_1994", y))).otherwise(penexc)
    penexc = pl.when(aged >= 1).then(penexc).otherwise(0.0)
    # From 1994, interest and dividends (and from 1997 capital gains) instead.
    divded = pl.lit(0.0)
    if y >= 1994:
        unearned = pl.col("dividends") + pl.col("intrec")
        if y >= 1997:
            unearned = unearned + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        divded = pl.when(aged > 0).then(
            pl.min_horizontal(unearned, _p("senior_investment_deduction_per_taxpayer", y) * n_tp)
        ).otherwise(0.0)
    pens = pl.max_horizontal(penexc, divded)
    if y >= 2013:
        # A flat deduction per taxpayer 65 or older replaces it.
        pens = pl.when(aged > 0).then(_p("senior_standard_deduction", y) * aged).otherwise(pens)

    # --- Exemptions ---
    num = n_tp + pl.col("depx").floor() + aged
    xmp1 = _p("personal_exemption", y)
    if y < 1987:
        exemp = num * xmp1
    else:
        ump = pl.when(
            ui_total >= _p("special_exemption_ui_share", y) * pl.col("agi")
        ).then(1.0).otherwise(0.0)
        exemp = (num - aged) * xmp1 + (aged + ump) * _p("special_exemption", y)
        exemp = exemp + _p("dependent_exemption", y) * pl.col("depx")
    dependent_exemption = _p("dependent_filer_exemption", y)
    exemp = pl.when(is_dependent_filer()).then(dependent_exemption).otherwise(exemp)

    taxinc = (agi - pens - exemp).clip(0, None)
    regtax = _p("rate", y) * taxinc
    regtax = pl.when(is_dependent_filer() & (agi < dependent_exemption)).then(0.0).otherwise(regtax)

    # --- Homestead property tax credit (non-elderly branch) ---
    hhy = (
        pl.col("mi_hh")
        - _p("property_credit_self_employment_tax_deduction_rate", y)
        * pl.col("mi_setax")
    ).clip(0, None)
    allow = _p("property_credit_phaseout_start", y)
    share = _p("property_credit_income_share", y)
    rentpaid = pl.col("rentpaid")
    ptax = pl.col("proptax") + _p("property_credit_rent_share", y) * rentpaid
    pcred_under65 = _p("property_credit_rate", y) * (ptax - share * hhy).clip(0, None)
    # Taxpayers 65 or older: property tax over a sliding share of income,
    # from 2012 scaled by an income percentage; renters may instead claim
    # rent over 40% of income.
    if y < 1980:
        pcred_aged = ptax
    else:
        senior_table = MI_PARAMS["property_credit_senior_income_share"][2018 if y >= 2018 else 1980]
        pcred_aged = (ptax - interpolate_table(hhy, senior_table) * hhy).clip(0, None)
        if y >= 2012:
            pcred_aged = pcred_aged * interpolate_table(hhy, MI_PARAMS["property_credit_senior_percentage"])
        pcred_aged = pl.when(rentpaid > 0).then(
            pl.max_horizontal(pcred_aged, rentpaid - _p("property_credit_senior_rent_income_share", y) * hhy)
        ).otherwise(pcred_aged)
    pcred = pl.when(aged > 0).then(pcred_aged).otherwise(pcred_under65).clip(None, _p("property_credit_cap", y))
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
    amex = pl.when(is_dependent_filer()).then(0.0).otherwise(n_tp + pl.col("depx"))
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
    dependent_below_exemption = is_dependent_filer() & (agi < dependent_exemption)
    detail_exemp = pl.when(dependent_below_exemption).then(0.0).otherwise(exemp)
    detail_taxinc = pl.when(dependent_below_exemption).then(0.0).otherwise(taxinc)
    detail_stded = _p("senior_standard_deduction", y) * aged if y >= 2013 else pl.lit(0.0)
    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=detail_exemp,
        standard_deduction=detail_stded,
        taxable_income=detail_taxinc,
        property_credit=pcred,
        eic=earncr,
        credits=fuel + pcred + earncr,
        rate=_p("rate", y),
    )
