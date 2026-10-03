"""Vermont personal income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    checkpoint,
    household_income,
    interpolate_table,
    taxsim_socsec,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

VT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "vt" / "income_tax.yaml")


def compute_vt_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Vermont income tax for each row."""
    state_year = "vt" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    y = effective_year
    p = YearParams(VT_PARAMS, effective_year)
    df = df.with_columns(
        vt_household_income=household_income(),
        vt_ui=unemployment_total(),
        # Federal Schedule E income (`comnew(8)`).
        vt_schede=pl.col("otherprop") + (pl.col("scorp") if year >= 1987 else 0.0),
        # Self-employment and additional Medicare tax are not deflated.
        vt_setax=pl.col("setax"),
    )
    df = deflate_for_extrapolation(df, flate, extra=("vt_household_income", "vt_ui", "vt_schede"))

    is_single = files_single()
    is_joint = files_joint()
    is_hoh = files_head_of_household()
    sep = pl.when(files_separate()).then(2.0).otherwise(1.0)
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
            statax = p.num("federal_tax_share") * taxinc
            rate = p.num("federal_tax_share") * pl.col("federal_source_rate") / 100.0
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
            if y >= 2022:
                starts = resolve_year(p["social_security_exemption_start_2022plus"], y)
            phb = pl.when(is_joint).then(float(starts["married_joint"])).otherwise(float(starts["other"]))
            exssb = ss - ((fed_agi - phb).clip(0, None) / p["social_security_exemption_range"]).clip(None, 1.0) * ss
            agi = fed_agi - dedg - exssb
            # The federal exemption count is deflated in projected years.
            exemps = federal_exemption_count(y) / flate
            factors = p["standard_deduction_factor"]
            stded = p.num("standard_deduction_2018") * (
                pl.when(is_hoh).then(factors["head_of_household"]).when(is_joint).then(factors["married_joint"]).otherwise(1.0)
            )
            if y >= 2022:
                # Dated statutory amounts (not the base times a status factor).
                dated = p["standard_deduction_2022plus"][y]
                stded = pl.when(is_hoh).then(float(dated["head_of_household"])).when(is_joint).then(
                    float(dated["married_joint"])
                ).otherwise(float(dated["single"]))
            stded = stded + p.num("standard_deduction_aged_addition_2018") * aged
            taxinc = (agi - exemps * p.num("exemption_2018") - stded).clip(0, None)
            detail_agi = agi
            detail_exemp = exemps * p.num("exemption_2018")
            detail_stded = stded
        df, (taxinc,) = checkpoint(df, vt_taxinc=taxinc)
        tables = p.value("brackets")
        index = p.num("bracket_index")
        statax = (
            pl.when(is_single).then(bracket_tax(taxinc, scale_brackets(tables["single"], index)))
            .when(is_hoh).then(bracket_tax(taxinc, scale_brackets(tables["head_of_household"], index)))
            .otherwise(bracket_tax(taxinc * sep, scale_brackets(tables["married"], index)) / sep)
        )
        rate = (
            pl.when(is_single).then(bracket_rate(taxinc, scale_brackets(tables["single"], index)))
            .when(is_hoh).then(bracket_rate(taxinc, scale_brackets(tables["head_of_household"], index)))
            .otherwise(bracket_rate(taxinc * sep, scale_brackets(tables["married"], index)))
        )
        if y >= 2018:
            statax = pl.when(fed_agi > p["minimum_tax_agi"]).then(
                pl.max_horizontal(statax, p["minimum_tax_share"] * fed_agi)
            ).otherwise(statax)
    df, (statax,) = checkpoint(df, vt_tax_before_credits=statax)

    # --- Nonrefundable credits ---
    hh = pl.col("vt_household_income")
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
    earncr = p.num("eitc_rate") * pl.col("eitc") if y >= 1988 else pl.lit(0.0)
    if y >= 2025 and behavior.mode.value == "statutory":
        # Enhanced structure: the full federal credit for a filer with no child dependents.
        childless = float(resolve_year(p["eitc_childless_rate_2025plus"], y))
        earncr = pl.when(pl.col("dep17") < 1).then(childless * pl.col("eitc")).otherwise(earncr)
    telcr = pl.lit(0.0)
    if y >= 1986:
        aged_tel = pl.when(hh < p.num("telephone_credit_aged_income_limit")).then(p.num("telephone_credit")).otherwise(0.0)
        young_tel = pl.lit(0.0)
        if y >= 2000:
            young_tel = pl.when(hh < p.num("telephone_credit_income_limit")).then(p.num("telephone_credit")).otherwise(0.0)
        telcr = pl.when(dependent_filer).then(0.0).when(aged > 0).then(aged_tel).otherwise(young_tel)

    # Homeowner/renter rebate (refundable).
    rentpaid, proptax = pl.col("rentpaid"), pl.col("proptax")
    if y <= 1994:
        c = p.value("property_rebate")
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
        excess = ptax - interpolate_table(vthy, p.value("renter_rebate_table")) * vthy
        if y <= 1997:
            cap = float(p["renter_rebate_max_1995_1997"])
            full = float(p["renter_rebate_full_limit_1995_1997"])
            pcred = pl.when(vthy <= full).then(excess.clip(0, cap)).otherwise(cap - 0.5 * (vthy - full))
            pcred = pl.when(aged > 0).then(pcred).otherwise(0.0)
        else:
            pcred = excess.clip(0, None)
        pcred = pl.when(vthy > p["renter_rebate_income_limit"]).then(0.0).otherwise(pcred)
    ctc = pl.lit(0.0)
    if y >= 2022:
        # Statutory years: no telephone credit; the child and dependent care
        # credit is 72% of the federal credit, refundable; and a child tax
        # credit for children under six (32 V.S.A. 5830f).
        telcr = pl.lit(0.0)
        chcref = float(p["child_care_credit_rate_2022plus"]) * fed_ccc
        chcr = pl.lit(0.0)
        credit = p["child_tax_credit_2022plus"]
        excess = (fed_agi - float(credit["reduction_start"])).clip(0, None)
        reduction = float(credit["reduction_amount"]) * (excess / float(credit["reduction_increment"])).ceil()
        # (counts without ages fall back to the under-6 count)
        young = pl.max_horizontal(pl.col("children_under_7"), pl.col("dep6")) if resolve_year(p["child_tax_credit_age_limit_2022plus"], y) >= 7 else pl.col("dep6")
        ctc = (float(credit["amount"]) * young - reduction).clip(0, None)
    statax = statax - pcred - earncr - telcr - chcref - ctc

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
        credits=lowcr + studcr + pcred + telcr + earncr + chcr + chcref + contr + ctc,
        rate=rate,
    )
