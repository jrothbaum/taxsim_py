"""Michigan individual income tax (`mitax`, taxsim_2024_09_21.f:8904-9230,
state id 23). See parameters/states/mi/income_tax.yaml for the scope note
(confirmed-inert elderly/blind/pension/rent/solar inputs).

Harness: 3,228/3,243 (99.5%) on the first run; the 15 residuals are all
2023, all under $0.31, all EITC-driven (the standing real-vs-oracle
EITC-table family). Point 2 below was verified by removing the extra $1:
782 cases then fail.

A flat-rate tax on federal AGI (plus the pre-1987 two-earner deduction
addback and, from 1998, half the household's self-employment tax) less
per-person exemptions, followed by three refundable credits: the
Homestead Property Tax Credit, the Home Heating Credit, and (2008+) a
share of the federal EITC.

Real, non-obvious mechanics:
1. The Home Heating Credit's income test reads `hy`, which the state
   dispatcher sets to the RAW household-income figure `data(159)` before
   its own inflation deflation - so in extrapolated years (2022-2023) the
   credit phases out against nominal income while its dollar base is the
   2021 figure. The property tax credit, by contrast, reads the deflated
   `data(159)`.
2. `data(159)` (household income) includes `data(93)`, which the input
   reader only sets to 1 AFTER summing the first record's household
   income - so every record after the first in an input file carries an
   extra $1 of household income. Replicated here, matching the batched
   validation harness (and any real multi-record run).
3. `data(159)` excludes self-employment income entirely (it sums wages,
   dividends, interest, UI, positive net capital gains and business
   income fields), and the property tax credit then SUBTRACTS half the
   self-employment tax from it - so self-employed filers get a lower
   household-resources figure than their actual income.
4. The 1987+ "special exemption" goes to anyone whose UI is at least
   half their federal AGI, not only the elderly.
"""

import polars as pl

from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year
from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS

MI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mi" / "income_tax.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _p(name: str, year: int) -> float:
    return float(resolve_year(MI_PARAMS[name], year))


def _pp(name: str, year: int) -> float:
    return float(resolve_year(PAYROLL_PARAMS[name], year))


def compute_mi_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    for col in ("dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "psemp", "ssemp", "proptax", "depx"):
        df = _with_default(df, col)
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
    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    # `data(159)`, raw: wages (positive parts) + dividends (+.001 input
    # fudge) + UI + interest + positive net capital gains + `data(93)`
    # (1 for every record after the first - see docstring point 2).
    hh_income = (
        pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None) + pl.col("dividends") + 0.001
        + ui_total + pl.col("intrec") + (pl.col("stcg") + pl.col("ltcg")).clip(0, None) + 1.0
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
        agi = agi + 0.5 * pl.col("mi_setax")
    elif y in (2011, 2012):
        agi = agi + pl.when(pl.col("mi_setax") <= 14204.0).then(0.5751 * pl.col("mi_setax")).otherwise(
            0.5 * pl.col("mi_setax") + 1067.0
        )

    # --- Exemptions ---
    num = n_tp + pl.col("depx").floor()
    xmp1 = _p("personal_exemption", y)
    if y < 1987:
        exemp = num * xmp1
    else:
        ump = pl.when(ui_total >= 0.5 * pl.col("agi")).then(1.0).otherwise(0.0)
        exemp = num * xmp1 + ump * _p("special_exemption", y)
        if 2000 <= y <= 2011:
            exemp = exemp + 600.0 * pl.col("depx")
        if y in (1998, 1999):
            exemp = exemp + 300.0 * pl.col("depx")

    taxinc = (agi - exemp).clip(0, None)
    regtax = _p("rate", y) * taxinc

    # --- Homestead property tax credit (non-elderly branch) ---
    hhy = (pl.col("mi_hh") - 0.5 * pl.col("mi_setax")).clip(0, None)
    allow = _p("property_credit_phaseout_start", y)
    share = 0.035 if y <= 2017 else 0.032
    pcred = (0.6 * (pl.col("proptax") - share * hhy).clip(0, None)).clip(None, 1200.0 if y <= 2017 else 1500.0)
    pcred = pl.when(hhy > allow).then(pcred * (1.0 - 0.1 * (hhy - allow) / 1000.0).clip(0, None)).otherwise(pcred)
    pcred = pl.when(hhy <= allow + 9000.0).then(pcred).otherwise(0.0)

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
    fuel = pl.when(nexemp > 0).then((fc - 0.035 * hy).clip(0, None)).otherwise(0.0) * _p("heating_credit_share", y)

    earncr = _p("eitc_rate", y) * pl.col("eitc") if y >= 2008 else pl.lit(0.0)

    statax = regtax.clip(0, None) - pcred - fuel - earncr
    return df.with_columns(siitax=statax * flate)
