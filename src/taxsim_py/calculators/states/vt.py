"""Vermont personal income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, is_dependent_filer
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    checkpoint,
    household_income,
    interpolate_table,
    taxsim_socsec,
    unemployment_total,
    with_default,
    with_defaults,
    with_state_detail,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

VT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "vt" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(VT_PARAMS[name], year))


def _adj(name: str, year: int) -> float:
    return float(resolve_year(STATE_ADJUSTMENT_PARAMS[name], year))


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def compute_vt_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Vermont income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = VT_PARAMS
    df = with_defaults(df, (
        "dividends", "intrec", "stcg", "ltcg", "depx", "charity_cash", "state_sales_or_income_tax_ded",
        "taxable_income", "standard_deduction", "itemized_deduction", "regular_tax", "schedule_tax", "amt",
        "ccc", "ccc_uncapped", "eitc", "pre1987_almtax", "pre1987_chcr", "federal_elder", "taxable_social_security",
        "gssi", "pensions", "rentpaid", "proptax", "psemp", "ssemp", "ui", "pui", "sui", "otherprop", "scorp",
        "addmed",
        "federal_chcr", "federal_source_rate",
    ))
    df = with_default(df, "itemizes", False)
    payroll = payroll_parts(year)
    df = df.with_columns(
        vt_hh=household_income(
            _adj("household_income_dividend_adjustment", y), _adj("household_income_record_adjustment", y)
        ),
        vt_ui=unemployment_total(),
        # Federal Schedule E income (`comnew(8)`).
        vt_schede=pl.col("otherprop") + (pl.col("scorp") if year >= 1987 else 0.0),
        # Self-employment and additional Medicare tax are not deflated.
        vt_setax=payroll["setax"],
    )
    df = deflate_for_extrapolation(
        df, flate,
        [
            "federal_chcr", "stcg", "ltcg", "charity_cash", "state_sales_or_income_tax_ded", "agi", "taxable_income",
            "standard_deduction", "itemized_deduction", "regular_tax", "schedule_tax", "amt", "ccc", "eitc", "vt_hh",
            "federal_elder", "taxable_social_security", "gssi", "pensions", "rentpaid", "proptax", "psemp", "ssemp",
            "vt_ui", "vt_schede", "pwages", "swages", "dividends", "intrec",
        ],
    )

    status = pl.col("filing_status")
    is_single = status == "single"
    is_joint = status == "married_joint"
    is_hoh = status == "head_of_household"
    sep = pl.when(status == "married_separate").then(2.0).otherwise(1.0)
    fed_agi = pl.col("agi")
    fti = pl.col("taxable_income")
    fed_itemizes = pl.col("itemizes")
    aged = aged_count()
    dependent_filer = is_dependent_filer()

    if y <= 1986:
        regtax = pl.col("regular_tax")
        almtax = pl.col("pre1987_almtax")
        fed_ccc = pl.col("federal_chcr")
    else:
        # Federal schedule tax (`comnew(28)`), without the 1988-1996 surtaxes.
        regtax = pl.col("schedule_tax")
        almtax = pl.col("amt")
        fed_ccc = pl.col("federal_chcr")

    # Positive federal net capital gain (`comnew(6)`); losses do not matter here.
    capgn = (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
    share = p["capital_gain_exclusion_share"]

    # --- Taxable income and tax ---
    detail_agi = fed_agi
    detail_exemp = pl.lit(0.0)
    detail_stded = pl.lit(0.0)
    if y <= 2000:
        taxinc = regtax + almtax - fed_ccc
        if y >= 1987:
            taxinc = taxinc - pl.col("federal_elder")
        taxinc = taxinc.clip(0, None)
        if 1991 <= y <= 1993:
            statax = bracket_tax(taxinc, p["federal_tax_brackets_1991"])
            rate = bracket_rate(taxinc, p["federal_tax_brackets_1991"])
        else:
            statax = _p("federal_tax_share", y) * taxinc
            rate = _p("federal_tax_share", y) * pl.col("federal_source_rate") / 100.0
    else:
        if y <= 2017:
            taxinc = fti
            if 2002 <= y <= 2007:
                taxinc = (taxinc - share * capgn).clip(0, None)
            elif y in (2008, 2009):
                taxinc = (taxinc - share * pl.min_horizontal(capgn, fti)).clip(0, None)
            elif y == 2010:
                taxinc = (taxinc - pl.min_horizontal(share * fti, capgn.clip(None, p["capital_gain_exclusion_cap_2010"]))).clip(0, None)
            elif y >= 2011:
                dgain = pl.max_horizontal(capgn.clip(None, p["capital_gain_exclusion_flat_2011"]), share * capgn)
                taxinc = (taxinc - pl.min_horizontal(share * fti, dgain)).clip(0, None)
            if y >= 2015:
                # Federal itemizers add back state income tax and deductions
                # above a multiple of the standard deduction.
                itemized = pl.col("itemized_deduction")
                salt = pl.col("state_sales_or_income_tax_ded")
                addd50 = pl.min_horizontal(itemized, salt)
                addit = (
                    (itemized - (pl.col("charity_cash") + salt)).clip(0, None)
                    - p["itemized_addback_multiple"] * pl.col("standard_deduction")
                ).clip(0, None)
                taxinc = taxinc + pl.when(fed_itemizes).then(addd50 + addit).otherwise(0.0)
        else:
            dedg = pl.min_horizontal(
                share * fti,
                pl.max_horizontal(capgn.clip(None, p["capital_gain_exclusion_flat_2011"]), share * capgn),
            )
            # Taxable Social Security is exempt, phased out above an AGI start.
            ss = pl.col("taxable_social_security")
            starts = p["social_security_exemption_start"]
            phb = pl.when(is_joint).then(float(starts["married_joint"])).otherwise(float(starts["other"]))
            exssb = ss - ((fed_agi - phb).clip(0, None) / p["social_security_exemption_range"]).clip(None, 1.0) * ss
            agi = fed_agi - dedg - exssb
            # The federal exemption count is deflated in projected years.
            exemps = federal_exemption_count(y) / flate
            factors = p["standard_deduction_factor"]
            stded = _p("standard_deduction_2018", y) * (
                pl.when(is_hoh).then(factors["head_of_household"]).when(is_joint).then(factors["married_joint"]).otherwise(1.0)
            )
            stded = stded + _p("standard_deduction_aged_addition_2018", y) * aged
            taxinc = (agi - exemps * _p("exemption_2018", y) - stded).clip(0, None)
            detail_agi = agi
            detail_exemp = exemps * _p("exemption_2018", y)
            detail_stded = stded
        df, (taxinc,) = checkpoint(df, vt_taxinc=taxinc)
        tables = resolve_year(p["brackets"], y)
        index = _p("bracket_index", y)
        statax = (
            pl.when(is_single).then(bracket_tax(taxinc, _scaled(tables["single"], index)))
            .when(is_hoh).then(bracket_tax(taxinc, _scaled(tables["head_of_household"], index)))
            .otherwise(bracket_tax(taxinc * sep, _scaled(tables["married"], index)) / sep)
        )
        rate = (
            pl.when(is_single).then(bracket_rate(taxinc, _scaled(tables["single"], index)))
            .when(is_hoh).then(bracket_rate(taxinc, _scaled(tables["head_of_household"], index)))
            .otherwise(bracket_rate(taxinc * sep, _scaled(tables["married"], index)))
        )
        if y >= 2018:
            statax = pl.when(fed_agi > p["minimum_tax_agi"]).then(
                pl.max_horizontal(statax, p["minimum_tax_share"] * fed_agi)
            ).otherwise(statax)
    df, (statax,) = checkpoint(df, vt_tax_before_credits=statax)

    # --- Nonrefundable credits ---
    hh = pl.col("vt_hh")
    lowcr = pl.lit(0.0)
    if 1978 <= y <= 1990:
        rows = p["low_income_credit_1982"] if y >= 1982 else p["low_income_credit_1978"]
        lowcr = pl.when(hh <= p["low_income_credit_income"]).then(statax * interpolate_table(statax, rows)).otherwise(0.0)
    elif y == 1977:
        rows = p["low_income_credit_1978"]
        lowcr = pl.when((aged > 0) & (hh < p["low_income_credit_aged_income_1977"])).then(
            statax * interpolate_table(statax, rows)
        ).otherwise(0.0)
    studcr = pl.lit(0.0)
    if y <= 1988:
        studcr = pl.when(dependent_filer).then(0.0).otherwise(pl.min_horizontal(pl.lit(p["student_credit_1988"]), statax))
    chcr = p["child_care_rate"] * fed_ccc
    chcref = pl.lit(0.0)
    if y >= 2003:
        limits = p["child_care_refundable_agi"]
        low = fed_agi <= pl.when(is_joint).then(limits["married_joint"]).otherwise(limits["other"])
        chcref = pl.when(low).then(p["child_care_refundable_rate"] * fed_ccc).otherwise(0.0)
        chcr = pl.when(chcref > 0).then(0.0).otherwise(chcr)
    contr = pl.lit(0.0)
    if y >= 2018:
        c = p["charity_credit_2018"]
        contr = c["rate"] * pl.col("charity_cash").clip(None, c["cap"])
    statax = (statax - lowcr - studcr - chcr - contr).clip(0, None)

    # --- Refundable credits ---
    earncr = _p("eitc_rate", y) * pl.col("eitc") if y >= 1988 else pl.lit(0.0)
    telcr = pl.lit(0.0)
    if y >= 1986:
        aged_tel = pl.when(hh < _p("telephone_credit_aged_income_limit", y)).then(_p("telephone_credit", y)).otherwise(0.0)
        young_tel = pl.lit(0.0)
        if y >= 2000:
            young_tel = pl.when(hh < _p("telephone_credit_income_limit", y)).then(_p("telephone_credit", y)).otherwise(0.0)
        telcr = pl.when(dependent_filer).then(0.0).when(aged > 0).then(aged_tel).otherwise(young_tel)

    # Homeowner/renter rebate (refundable).
    rentpaid, proptax = pl.col("rentpaid"), pl.col("proptax")
    if y <= 1994:
        c = resolve_year(p["property_rebate"], y)
        ptax = proptax + c["rent_share"] * rentpaid
        pcred = (ptax - interpolate_table(hh, c["table"]) * hh).clip(0, c["max"])
        if "income_limit" in c:
            pcred = pl.when(hh >= c["income_limit"]).then(0.0).otherwise(pcred)
        pcred = pl.when(aged > 0).then(pcred).otherwise(0.0)
    else:
        investment = pl.col("dividends") + pl.col("intrec")
        socsec = taxsim_socsec(y, pl.col("vt_setax"), pl.col("addmed"))
        vthy = (
            pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None) + investment + pl.col("vt_ui")
            + (pl.col("psemp") + pl.col("ssemp")).clip(0, None) + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
            + pl.col("pensions") + pl.col("gssi") + pl.col("vt_schede").clip(0, None) - socsec
            + (investment - p["renter_income_investment_threshold"]).clip(0, None)
        )
        ptax = float(p["renter_rebate_rent_share"]) * rentpaid
        excess = ptax - interpolate_table(vthy, resolve_year(p["renter_rebate_table"], y)) * vthy
        if y <= 1997:
            cap = float(p["renter_rebate_max_1995_1997"])
            full = float(p["renter_rebate_full_limit_1995_1997"])
            pcred = pl.when(vthy <= full).then(excess.clip(0, cap)).otherwise(cap - 0.5 * (vthy - full))
            pcred = pl.when(aged > 0).then(pcred).otherwise(0.0)
        else:
            pcred = excess.clip(0, None)
        pcred = pl.when(vthy > p["renter_rebate_income_limit"]).then(0.0).otherwise(pcred)
    statax = statax - pcred - earncr - telcr - chcref

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=detail_agi,
        exemptions=detail_exemp,
        standard_deduction=detail_stded,
        taxable_income=taxinc,
        property_credit=pcred,
        child_care_credit=pl.when(chcref > 0).then(chcref).otherwise(chcr),
        eic=earncr,
        credits=lowcr + studcr + pcred + telcr + earncr + chcr + chcref + contr,
        rate=rate,
    )
