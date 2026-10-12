"""Utah individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import by_filing_status, checkpoint, pre1987_federal_itemizing, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

UT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ut" / "income_tax.yaml")


def compute_ut_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Utah income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, UT_PARAMS)
    y = effective_year
    p = YearParams(UT_PARAMS, effective_year)
    df = deflate_for_extrapolation(df, flate)

    is_joint = files_joint()
    is_hoh = files_head_of_household()
    married_table = is_joint | is_hoh
    sep = pl.when(files_separate()).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    agi = pl.col("agi")
    single_phase = files_single()
    phases = p["retirement_phaseout_start"]
    phase = pl.when(single_phase).then(float(phases["single"])).otherwise(float(phases["other"]) / sep)
    salt = pl.col("state_sales_or_income_tax_ded")

    # --- Exemptions ---
    if p.in_effect("exemption_per_person"):
        exemp = p.num("exemption_per_person") * federal_exemption_count(y)
    elif p.in_effect("federal_exemption_share"):
        exemp = p.num("federal_exemption_share") * pl.col("personal_exemptions")
    else:
        exemp = p.num("dependent_exemption") * pl.col("depx")

    if p.in_effect("brackets"):
        # --- Deductions ---
        if y <= 1986:
            gross, fed_itemizes, _ = pre1987_federal_itemizing(y)
            itemized = gross
            c = p.value("standard_deduction_share")
            stded = (c["rate"] * agi).clip(c["low"] / sep, c["high"] / sep)
            if y > 1980:
                stded = pl.when(fed_itemizes).then(0.0).otherwise(stded)
            taxbc = pl.col("pre1987_taxbc")
            almtax = pl.col("pre1987_almtax")
            fed_credit = pl.col("credit")
        else:
            fed_itemizes = pl.col("itemizes")
            itemized = pl.col("itemized_deduction")
            stded = pl.when(fed_itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
            taxbc = pl.col("regular_tax")
            almtax = pl.col("amt")
            fed_credit = pl.col("nonrefundable_credits")
        xitded = (pl.when(fed_itemizes).then(itemized).otherwise(0.0) - salt).clip(0, None)
        deduc = pl.max_horizontal(xitded, stded)
        fedtax = (almtax + taxbc - fed_credit).clip(0, None) * p.num("federal_tax_share")

        # --- State income tax deduction lost to the federal-style limit (1993 on) ---
        tx = pl.lit(0.0)
        if p.in_effect("itemized_limit_threshold"):
            fed_agi = agi
            phas92 = p.num("itemized_limit_threshold") * p.num("itemized_limit_index") / sep
            xtot = salt + pl.col("proptax") + pl.col("mortgage") + pl.col("charity_cash")
            over = fed_agi - phas92
            xconst = pl.min_horizontal(p["itemized_limit_share"] * xtot, p["itemized_limit_rate"] * over)
            lost = xconst / xtot * salt
            tx = pl.when((xitded > stded) & (xtot > 0) & (over > 0)).then(lost).otherwise(0.0)
        # --- Retirement income deduction ---
        under65 = p.num("retirement_deduction_under65")
        aged_amount = p.num("retirement_deduction_aged")
        investment = pl.col("dividends") + pl.col("intrec")
        retinc = pl.col("pensions") + pl.col("taxable_social_security") + investment * aged.clip(None, 1)
        if y <= 1987:
            cap = (
                pl.when(aged == 0).then(txp * under65)
                .when(aged == 1).then(aged_amount + (txp - 1) * under65)
                .otherwise(txp * aged_amount)
            )
            retded = retinc.clip(0, cap)
        else:
            reduction = float(p["retirement_deduction_phaseout_rate"]) * (agi - phase).clip(0, None)
            retded = (
                pl.when(aged == 0).then(
                    pl.when(retinc > 0).then(txp * pl.min_horizontal(retinc, pl.lit(under65)) - reduction).otherwise(0.0)
                )
                .otherwise(aged.clip(None, 2) * aged_amount - reduction)
            ).clip(0, None)
        taxinc = (agi - retded - fedtax - exemp - deduc + tx).clip(0, None)
        df, (taxinc,) = checkpoint(df, ut_taxinc=taxinc)

        schedule = p.value("brackets")
        statax = pl.when(married_table).then(bracket_tax(taxinc, schedule["married"])).otherwise(
            bracket_tax(taxinc, schedule["single"])
        )
        rate = pl.when(married_table).then(bracket_rate(taxinc, schedule["married"])).otherwise(
            bracket_rate(taxinc, schedule["single"])
        )
        credits = pl.lit(0.0)
        if p.in_effect("single_rate_alternative"):
            statax = pl.min_horizontal(statax, p.num("single_rate_alternative") * agi.clip(0, None))
        if p.in_effect("small_tax_credit"):
            c = p.value("small_tax_credit")
            credits = pl.when(statax <= c["tax_limit"]).then((c["rate"] * statax).clip(0, c["cap"])).otherwise(0.0)
            statax = (statax - credits).clip(0, None)
        detail = {"standard_deduction": stded, "itemized_deductions": xitded}
    else:
        taxinc = agi.clip(0, None)
        statax = p.num("flat_rate") * taxinc
        rate = pl.lit(p.num("flat_rate"))
        detail = {}
        fed_itemizes = pl.col("itemizes")
        if not p.in_effect("salt_cap"):
            itemized = (pl.col("itemized_deduction") - salt).clip(0, None)
        else:
            other = pl.col("proptax") + pl.col("otheritem")
            sttax = pl.min_horizontal(p.num("salt_cap") / sep, other + salt)
            stt = pl.min_horizontal(salt, sttax - other)
            itemized = pl.col("itemized_before_limit") - stt
        deduc = pl.when(fed_itemizes).then(itemized).otherwise(pl.col("standard_deduction"))
        t = p.value("taxpayer_credit_phaseout_threshold")
        index = p.num("taxpayer_credit_index")
        if "per_taxpayer" in t:
            threshold = pl.when(is_hoh).then(t["head_of_household"] * index).otherwise(t["per_taxpayer"] * index * txp)
        else:
            threshold = by_filing_status(t) * index
        if p.in_effect("taxpayer_credit_personal_exemption"):
            credit_base = deduc + p.num("taxpayer_credit_personal_exemption") * pl.col("depx")
        else:
            credit_base = exemp + deduc
        credit = (
            p["taxpayer_credit_rate"] * credit_base
            - p["taxpayer_credit_phaseout_rate"] * (taxinc - threshold).clip(0, None)
        ).clip(0, None)
        ss = pl.col("taxable_social_security")
        phaseout_rate = float(p["retirement_credit_phaseout_rate"])
        if p.in_effect("retirement_credit_age"):
            retirement_age = p.num("retirement_credit_age")
            retirement_taxpayers = (
                (pl.col("page") >= retirement_age).cast(pl.Float64)
                + (pl.col("sage") >= retirement_age).cast(pl.Float64)
            )
            retirement_credit = (
                p.num("retirement_credit_max") * retirement_taxpayers - phaseout_rate * (agi - phase).clip(0, None)
            ).clip(0, None)
            ss_threshold = by_filing_status(p.value("social_security_credit_phaseout_start"))
            ss_credit = (p["social_security_credit_rate"] * ss - phaseout_rate * (agi - ss_threshold).clip(0, None)).clip(0, None)
            retcrd = pl.max_horizontal(retirement_credit, ss_credit)
        else:
            retcrd = pl.when(aged > 0).then(
                (p.num("retirement_credit_max") * aged - phaseout_rate * (agi - phase).clip(0, None)).clip(0, None)
            ).otherwise(0.0)
            retcrd = retcrd + pl.when(ss > 0).then(
                pl.min_horizontal(
                    p.num("social_security_credit_per_taxpayer") * (txp - aged),
                    float(p["social_security_credit_rate"]) * ss,
                )
            ).otherwise(0.0)

        ctc = pl.lit(0.0)
        if p.in_effect("child_tax_credit"):
            k = p.value("child_tax_credit")
            ctc_children = pl.col("children_under_4" if k["child_age_limit"] == 4 else "dep6").cast(pl.Float64)
            ctc = (
                k["amount"] * ctc_children
                - k["reduction_rate"] * (taxinc - by_filing_status(k["phaseout_start"])).clip(0, None)
            ).clip(0, None)
        earned_income_credit = pl.lit(0.0)
        if p.in_effect("earned_income_credit_rate"):
            earned_income_credit = pl.min_horizontal(
                p.num("earned_income_credit_rate") * pl.col("eitc"),
                (pl.col("pwages") + pl.col("swages")).clip(0, None),
            )

        statax = (statax - credit - retcrd - ctc - earned_income_credit).clip(0, None)
        credits = credit + retcrd + ctc + earned_income_credit

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df, agi=agi, exemptions=exemp, taxable_income=taxinc, credits=credits, rate=rate, **detail
    )
