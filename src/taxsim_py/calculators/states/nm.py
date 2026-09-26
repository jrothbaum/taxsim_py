"""New Mexico personal income tax calculator."""

import math

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, by_filing_status, checkpoint, household_income, interpolate_table, unemployment_total, with_default, itemize_choice, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NM_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "nm" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _p(name: str, year: int) -> float:
    return float(resolve_year(NM_PARAMS[name], year))


def _adj(name: str, year: int) -> float:
    return float(resolve_year(STATE_ADJUSTMENT_PARAMS[name], year))


def _pre1987_brackets(kind: str, y: int) -> list[list[float]]:
    factor = _p("rate_factor_pre1987", y)
    rows = []
    for index, (start, rate_pct) in enumerate(NM_PARAMS["brackets_1977"][kind], start=1):
        rate_pct = math.floor(10.0 * float(rate_pct) * factor + 0.5) / 10.0
        if y >= 1983 and index == 14:
            rate_pct = float(NM_PARAMS["bracket14_rate_from_1983"])
        rows.append([float(start), rate_pct / 100.0])
    return rows


def _schedule_tax(taxinc: pl.Expr, status: pl.Expr, sep: pl.Expr, y: int) -> pl.Expr:
    if y <= 1986:
        return (
            pl.when(status == "single")
            .then(bracket_tax(taxinc, _pre1987_brackets("single", y)))
            .when(status == "married_separate")
            .then(bracket_tax(taxinc, _pre1987_brackets("separate", y)))
            .otherwise(bracket_tax(taxinc, _pre1987_brackets("married", y)))
        )
    tables = NM_PARAMS["brackets"]
    married = bracket_tax(taxinc * sep, resolve_year(tables["married"], y)) / sep
    if y <= 2005:
        if y == 1995:
            married = pl.when(status == "married_separate").then(
                bracket_tax(taxinc, NM_PARAMS["brackets_separate_1995"])
            ).otherwise(bracket_tax(taxinc, resolve_year(tables["married"], y)))
        return (
            pl.when(status == "single")
            .then(bracket_tax(taxinc, resolve_year(tables["single"], y)))
            .when(status == "head_of_household")
            .then(bracket_tax(taxinc, resolve_year(tables["head_of_household"], y)))
            .otherwise(married)
        )
    return pl.when(status == "single").then(bracket_tax(taxinc, resolve_year(tables["single"], y))).otherwise(married)


def _schedule_rate(taxinc: pl.Expr, status: pl.Expr, sep: pl.Expr, y: int) -> pl.Expr:
    if y <= 1986:
        return (
            pl.when(status == "single")
            .then(bracket_rate(taxinc, _pre1987_brackets("single", y)))
            .when(status == "married_separate")
            .then(bracket_rate(taxinc, _pre1987_brackets("separate", y)))
            .otherwise(bracket_rate(taxinc, _pre1987_brackets("married", y)))
        )
    tables = NM_PARAMS["brackets"]
    married = bracket_rate(taxinc * sep, resolve_year(tables["married"], y))
    if y <= 2005:
        if y == 1995:
            married = pl.when(status == "married_separate").then(
                bracket_rate(taxinc, NM_PARAMS["brackets_separate_1995"])
            ).otherwise(bracket_rate(taxinc, resolve_year(tables["married"], y)))
        return (
            pl.when(status == "single")
            .then(bracket_rate(taxinc, resolve_year(tables["single"], y)))
            .when(status == "head_of_household")
            .then(bracket_rate(taxinc, resolve_year(tables["head_of_household"], y)))
            .otherwise(married)
        )
    return pl.when(status == "single").then(
        bracket_rate(taxinc, resolve_year(tables["single"], y))
    ).otherwise(married)


def compute_nm_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate New Mexico income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = NM_PARAMS
    df = with_defaults(df, (
        "dividends", "intrec", "psemp", "ssemp", "stcg", "ltcg", "ui", "pui", "sui", "proptax", "otheritem", "mortgage", "depx",
        "childcare", "state_sales_or_income_tax_ded", "itemized_deduction", "standard_deduction",
        "personal_exemptions", "ccc", "ccc_uncapped", "regular_tax", "eitc", "pre1987_capgn", "pre1987_chcr",
        "pensions", "gssi", "taxable_social_security", "rentpaid", "pbusinc", "pprofinc", "scorp", "sbusinc",
        "sprofinc", "pre1987_amex",
        "federal_chcr",
    ))
    df = with_default(df, "itemizes", False)
    dividend_adjustment = _adj("household_income_dividend_adjustment", y)
    hh = household_income(dividend_adjustment, _adj("household_income_record_adjustment", y))
    business = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
    modagi = (
        pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None) + unemployment_total()
        + business.clip(0, None) + pl.col("ltcg").clip(0, None) + pl.col("stcg").clip(0, None)
        + pl.col("dividends") + dividend_adjustment + pl.col("intrec") + pl.col("gssi") + pl.col("pensions")
    )
    # Business and S corporation income, which are not deflated in projected years.
    business_income = sum(pl.col(c).clip(0, None) for c in ("pbusinc", "pprofinc", "scorp", "sbusinc", "sprofinc"))
    df = df.with_columns(nm_hh=hh, nm_modagi=modagi, nm_business=business_income)
    df = deflate_for_extrapolation(
        df, flate,
        [
            "federal_chcr", "stcg", "ltcg", "childcare", "state_sales_or_income_tax_ded", "agi", "itemized_deduction",
            "standard_deduction", "personal_exemptions", "ccc", "ccc_uncapped", "regular_tax", "eitc",
            "pre1987_capgn", "pre1987_chcr", "nm_hh", "nm_modagi", "taxable_social_security", "rentpaid",
            "proptax",
        ],
    )

    status = pl.col("filing_status")
    is_single = status == "single"
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    dependent_filer = is_dependent_filer()
    depx = pl.col("depx")
    exemps = federal_exemption_count(y)
    fed_agi = pl.col("agi")
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    modagi = pl.col("nm_modagi") + pl.col("nm_business")

    # --- Federal values ---
    if y <= 1986:
        fed_deduc = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
        fed_zbr = by_filing_status({s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], y) for s in _STATUSES})
        fed_itemizes = fed_deduc > fed_zbr if y <= 1981 else itemize_choice(fed_deduc > fed_zbr)
        capgn = pl.col("pre1987_capgn")
        fed_ccc = pl.col("federal_chcr")
        amex = pl.col("pre1987_amex")
    else:
        fed_itemizes = pl.col("itemizes")
        fed_deduc = pl.col("itemized_deduction")
        fed_zbr = pl.when(fed_itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
        fullcg = pl.col("stcg") + pl.col("ltcg")
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        capgn = pl.max_horizontal(fullcg, -loss_limit / flate / sep)
        fed_ccc = pl.col("federal_chcr")
        amex = pl.col("personal_exemptions")

    # --- New Mexico AGI ---
    share = _p("capital_gains_deduction_share", y)
    floor = _p("capital_gains_deduction_floor", y)
    cgded = pl.when(capgn > 0).then(
        pl.min_horizontal(capgn, pl.max_horizontal(pl.lit(floor), share * capgn))
    ).otherwise(0.0) / sep
    agi = fed_agi - cgded
    if 1984 <= y <= 1989:
        # TAXSIM adds taxable Social Security back again in these years.
        agi = agi + pl.col("taxable_social_security")
    df, (agi, fed_agi) = checkpoint(df, nm_agi=agi, nm_fed_agi=fed_agi)

    # --- Deduction for taxpayers 65 or older ---
    elded = pl.lit(0.0)
    if 1981 <= y <= 1984:
        elded = float(p["aged_deduction_1981_1984"]) * aged
    elif y >= 1985:
        tables = p["aged_deduction_1985_1986" if y <= 1986 else "aged_deduction_1987plus"]
        amount = (
            pl.when(is_single).then(interpolate_table(agi, tables["single"]))
            .when(is_sep).then(interpolate_table(agi, tables["married_separate"]))
            .otherwise(interpolate_table(agi, tables["married"]))
        )
        elded = amount * (aged if y <= 1986 else pl.min_horizontal(aged, txp))

    # --- Deductions ---
    xitded = pl.when(fed_itemizes).then(
        (fed_deduc - pl.min_horizontal(fed_deduc - fed_zbr, salt_ded)).clip(0, None)
    ).otherwise(0.0)
    if 1986 <= y <= 1990:
        stded = by_filing_status({s: resolve_year(v, y) for s, v in p["standard_deduction_1986_1990"].items()})
    else:
        stded = fed_zbr
    df, (deduc, xitded, stded) = checkpoint(
        df,
        nm_deduc=pl.max_horizontal(xitded, stded),
        nm_xitded=xitded,
        nm_stded=stded,
    )

    # --- Exemptions ---
    if y in (1984, 1985) or y >= 1991:
        exemp = amex
    else:
        exemp = _p("exemption_per_person", y) * exemps
    if y >= 2006:
        limits = p["low_income_exemption_income_limit"]
        income_limit = pl.when(is_single).then(float(resolve_year(limits["single"], y))).otherwise(
            float(resolve_year(limits["other"], y)) / sep
        )
        start = by_filing_status({s: resolve_year(v, y) for s, v in p["low_income_exemption_phaseout_start"].items()})
        rate = by_filing_status(p["low_income_exemption_phaseout_rate"])
        per_person = (_p("low_income_exemption_amount", y) - rate * (fed_agi - start).clip(0, None)).clip(0, None)
        # The federal exemption count is deflated with other federal values
        # when a later year is projected.
        exemp = exemp + pl.when(fed_agi <= income_limit).then(exemps / flate * per_person).otherwise(0.0)
    exemp = pl.when(dependent_filer).then(0.0).otherwise(exemp)
    cerdep = pl.when(is_joint | is_hoh).then(_p("dependent_deduction", y) * (depx - 1).clip(0, None)).otherwise(0.0)
    df, (taxinc,) = checkpoint(df, nm_taxinc=(agi - elded - deduc - exemp - cerdep).clip(0, None))
    df, (statax,) = checkpoint(df, nm_table_tax=_schedule_tax(taxinc, status, sep, y))
    rate = _schedule_rate(taxinc, status, sep, y)

    # --- Refundable credits ---
    # Taxpayers 65 or older count twice from 1985.
    ncred = (txp + depx + aged).clip(None, 6).floor()
    if y >= 1985:
        ncred = (ncred + aged).clip(None, 6)
    rebate_rows = resolve_year(p["low_income_rebate"], y)
    ycred = pl.lit(0.0)
    for size in range(1, 7):
        table = [[float(row[0]), float(row[size])] for row in rebate_rows]
        ycred = pl.when(ncred == size).then(interpolate_table(modagi, table)).otherwise(ycred)
    ycred = pl.when(dependent_filer).then(0.0).otherwise(ycred)
    if y <= 1985:
        xtra = (_p("food_credit", y) + _p("medical_credit", y)) * ncred
    elif y <= 1989:
        xtra = pl.when(ycred > 0).then((txp + depx) * _p("food_credit", y)).otherwise(0.0)
    elif y == 1990:
        food = p["food_credit_1990"]
        hh = pl.col("nm_hh")
        xtra = pl.when(is_single).then(interpolate_table(hh, food["single"])).otherwise(
            interpolate_table(hh / sep, food["married"])
        ) * (txp + depx)
    else:
        xtra = pl.lit(0.0)
    chcr = pl.lit(0.0)
    if y >= 1981:
        children = depx.clip(None, float(p["child_care_max_children"]))
        expense = pl.min_horizontal(
            float(p["child_care_expense_share"]) * pl.col("childcare"), float(p["child_care_cap_per_child"]) * children
        )
        chcr = pl.when(modagi < _p("child_care_income_limit", y)).then(
            (pl.min_horizontal(pl.lit(float(p["child_care_credit_cap"])), expense) - fed_ccc).clip(0, None)
        ).otherwise(0.0)
    earncr = _p("eitc_match_rate", y) * pl.col("eitc")
    # Property tax rebate for taxpayers 65 or older.
    ptax = pl.col("proptax") + float(p["property_rebate_rent_share"]) * pl.col("rentpaid")
    pmax = interpolate_table(modagi, p["property_rebate_threshold"])
    pcred = pl.when((aged > 0) & (modagi <= float(p["property_rebate_income_limit"]))).then(
        (ptax - pmax).clip(0, None).clip(None, float(p["property_rebate_max"]) / sep)
    ).otherwise(0.0)
    credit = ycred + xtra + chcr + pcred + earncr
    statax = statax - credit

    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        property_credit=pcred,
        child_care_credit=chcr,
        eic=earncr,
        credits=credit,
        rate=rate,
    )
