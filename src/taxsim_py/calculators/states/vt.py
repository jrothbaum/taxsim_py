"""Vermont personal income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
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
    effective_year, flate = resolve_state_year(year, behavior, VT_PARAMS)
    y = effective_year
    p = YearParams(VT_PARAMS, effective_year)
    df = df.with_columns(
        vt_household_income=household_income(behavior),
        vt_ui=unemployment_total(),
        # Federal Schedule E income (`comnew(8)`).
        vt_schede=pl.col("otherprop") + (pl.col("scorp") if year >= 1987 else 0.0),
        # Self-employment and additional Medicare tax are not deflated.
        vt_setax=pl.col("payroll_setax"),
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
    share = p.num("capital_gain_exclusion_share") if p.in_effect("capital_gain_exclusion_share") else 0.0

    # --- Taxable income and tax ---
    detail_agi = fed_agi
    detail_exemp = pl.lit(0.0)
    detail_stded = pl.lit(0.0)
    if p.in_effect("federal_tax_share"):
        taxinc = regtax + almtax - fed_ccc
        if y >= 1987:
            taxinc = taxinc - pl.col("federal_elder")
        taxinc = taxinc.clip(0, None)
        if p.in_effect("federal_tax_brackets"):
            statax = bracket_tax(taxinc, p.value("federal_tax_brackets"))
            rate = bracket_rate(taxinc, p.value("federal_tax_brackets"))
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
            elif p.in_effect("capital_gain_exclusion_cap"):
                taxinc = (taxinc - pl.min_horizontal(share * fti, capgn.clip(None, p.num("capital_gain_exclusion_cap")))).clip(0, None)
            elif p.in_effect("capital_gain_exclusion_floor"):
                dgain = pl.max_horizontal(capgn.clip(None, p.num("capital_gain_exclusion_floor")), share * capgn)
                taxinc = (taxinc - pl.min_horizontal(share * fti, dgain)).clip(0, None)
            if p.in_effect("itemized_addback_multiple"):
                itemized = pl.col("itemized_deduction")
                salt = pl.col("state_sales_or_income_tax_ded")
                addd50 = pl.min_horizontal(itemized, salt)
                addit = (
                    (itemized - (pl.col("charity_cash") + salt)).clip(0, None)
                    - p.num("itemized_addback_multiple") * pl.col("standard_deduction")
                ).clip(0, None)
                taxinc = taxinc + pl.when(fed_itemizes).then(addd50 + addit).otherwise(0.0)
        else:
            dedg = pl.min_horizontal(
                share * fti,
                pl.max_horizontal(capgn.clip(None, p.num("capital_gain_exclusion_floor")), share * capgn),
            )
            # Taxable Social Security is exempt, phased out above an AGI start.
            ss = pl.col("taxable_social_security")
            starts = p.value("social_security_exemption_start")
            phb = pl.when(is_joint).then(float(starts["married_joint"])).otherwise(float(starts["other"]))
            exssb = ss - ((fed_agi - phb).clip(0, None) / p["social_security_exemption_range"]).clip(None, 1.0) * ss
            agi = fed_agi - dedg - exssb
            # The federal exemption count is deflated in projected years.
            exemps = federal_exemption_count(y) / flate
            amounts = p.value("standard_deduction")
            stded = pl.when(is_hoh).then(float(amounts["head_of_household"])).when(is_joint).then(
                float(amounts["married_joint"])
            ).otherwise(float(amounts["single"]))
            stded = stded + p.num("standard_deduction_aged_addition") * aged
            taxinc = (agi - exemps * p.num("exemption_amount") - stded).clip(0, None)
            detail_agi = agi
            detail_exemp = exemps * p.num("exemption_amount")
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
        if p.in_effect("minimum_tax_share"):
            statax = pl.when(fed_agi > p["minimum_tax_agi"]).then(
                pl.max_horizontal(statax, p.num("minimum_tax_share") * fed_agi)
            ).otherwise(statax)
    df, (statax,) = checkpoint(df, vt_tax_before_credits=statax)

    # --- Nonrefundable credits ---
    hh = pl.col("vt_household_income")
    lowcr = pl.lit(0.0)
    if p.in_effect("low_income_credit_share"):
        rows = p.value("low_income_credit_share")
        if p.in_effect("low_income_credit_aged_income"):
            lowcr = pl.when((aged > 0) & (hh < p.num("low_income_credit_aged_income"))).then(
                statax * interpolate_table(statax, rows)
            ).otherwise(0.0)
        else:
            lowcr = pl.when(hh <= p["low_income_credit_income"]).then(statax * interpolate_table(statax, rows)).otherwise(0.0)
    studcr = pl.lit(0.0)
    if p.in_effect("student_credit") and not behavior.omit_vermont_student_credit:
        # TAXSIM pays the student credit to every filer who is not a dependent
        # (there is no student input).
        studcr = pl.when(dependent_filer).then(0.0).otherwise(pl.min_horizontal(pl.lit(p.num("student_credit")), statax))
    chcr = p.num("child_care_rate") * fed_ccc if p.in_effect("child_care_rate") else pl.lit(0.0)
    chcref = pl.lit(0.0)
    if p.in_effect("child_care_refundable_rate"):
        limits = p.value("child_care_refundable_agi")
        low = fed_agi <= pl.when(is_joint).then(limits["married_joint"]).otherwise(limits["other"])
        chcref = pl.when(low).then(p.num("child_care_refundable_rate") * fed_ccc).otherwise(0.0)
        chcr = pl.when(chcref > 0).then(0.0).otherwise(chcr)
    contr = pl.lit(0.0)
    if p.in_effect("charity_credit"):
        c = p.value("charity_credit")
        contr = c["rate"] * pl.col("charity_cash").clip(None, c["cap"])
    statax = (statax - lowcr - studcr - chcr - contr).clip(0, None)

    # --- Refundable credits ---
    earncr = p.num("eitc_rate") * pl.col("eitc") if p.in_effect("eitc_rate") else pl.lit(0.0)
    if p.in_effect("eitc_childless_rate"):
        earncr = pl.when(pl.col("dep17") < 1).then(p.num("eitc_childless_rate") * pl.col("eitc")).otherwise(earncr)
    telcr = pl.lit(0.0)
    if p.in_effect("telephone_credit"):
        aged_tel = pl.when(hh < p.num("telephone_credit_aged_income_limit")).then(p.num("telephone_credit")).otherwise(0.0)
        young_tel = pl.lit(0.0)
        if p.in_effect("telephone_credit_income_limit"):
            young_tel = pl.when(hh < p.num("telephone_credit_income_limit")).then(p.num("telephone_credit")).otherwise(0.0)
        telcr = pl.when(dependent_filer).then(0.0).when(aged > 0).then(aged_tel).otherwise(young_tel)

    # Homeowner/renter rebate (refundable).
    rentpaid, proptax = pl.col("rentpaid"), pl.col("proptax")
    if p.in_effect("property_rebate"):
        c = p.value("property_rebate")
        ptax = proptax + c["rent_share"] * rentpaid
        pcred = (ptax - interpolate_table(hh, c["table"]) * hh).clip(0, c["max"])
        if "income_limit" in c:
            pcred = pl.when(hh >= c["income_limit"]).then(0.0).otherwise(pcred)
        pcred = pl.when(aged > 0).then(pcred).otherwise(0.0)
    else:
        investment = pl.col("dividends") + pl.col("intrec")
        socsec = taxsim_socsec(y, pl.col("vt_setax"), pl.col("addmed"), behavior)
        vthy = (
            pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None) + investment + pl.col("vt_ui")
            + (pl.col("psemp") + pl.col("ssemp")).clip(0, None) + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
            + pl.col("pensions") + pl.col("gssi") + pl.col("vt_schede").clip(0, None) - socsec
            + (investment - p["renter_income_investment_threshold"]).clip(0, None)
        )
        ptax = float(p["renter_rebate_rent_share"]) * rentpaid
        excess = ptax - interpolate_table(vthy, p.value("renter_rebate_table")) * vthy
        if p.in_effect("renter_rebate_aged_cap"):
            cap = float(p.value("renter_rebate_aged_cap")["max"])
            full = float(p.value("renter_rebate_aged_cap")["full_limit"])
            pcred = pl.when(vthy <= full).then(excess.clip(0, cap)).otherwise(cap - 0.5 * (vthy - full))
            pcred = pl.when(aged > 0).then(pcred).otherwise(0.0)
        else:
            pcred = excess.clip(0, None)
        pcred = pl.when(vthy > p["renter_rebate_income_limit"]).then(0.0).otherwise(pcred)
    ctc = pl.lit(0.0)
    if p.in_effect("child_care_full_refundable_rate"):
        chcref = p.num("child_care_full_refundable_rate") * fed_ccc
        chcr = pl.lit(0.0)
    if p.in_effect("child_tax_credit"):
        credit = p.value("child_tax_credit")
        excess = (fed_agi - float(credit["reduction_start"])).clip(0, None)
        reduction = float(credit["reduction_amount"]) * (excess / float(credit["reduction_increment"])).ceil()
        # Counts without ages fall back to the under-6 count.
        young = pl.max_horizontal(pl.col("children_under_7"), pl.col("dep6")) if credit["age_limit"] >= 7 else pl.col("dep6")
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
